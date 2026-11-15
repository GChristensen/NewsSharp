"""The worker's coordinator (worker/coordinator.py), R0.23: one event loop running the scheduler
(item 3), job intake (R0.9), every outbound request, the heartbeat and the pool. It parses nothing."""
import asyncio
import functools
import http
import logging
import random
import signal
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count
from django.db.utils import DatabaseError

from feeds import store
from feeds.models import Feed, FeedIcon, PushSubscription, Setting, Story
from feeds.util import now, site_root
from reading.models import Folder, ReadStory, SavedStory, Subscription, ViewSetting
from worker import fetcher, pipeline, wakeup, websub
from worker.models import Job, WorkerEvent
from worker.pool import Pool, TaskCrashed, TaskFailed, TaskTimeout
from worker.tasks import TIME_LIMITS

log = logging.getLogger("worker")

FETCH_CONCURRENCY = 4  # R2.16
INTERACTIVE_CONCURRENCY = 4  # R0.10
MAX_WAITING_BODIES = 8  # R0.31
PURGE_AFTER = 60  # R7.10
ICON_MAX_AGE = 30 * 86400  # R8.1
TEXT_FAILURE_TTL = 600  # R13.6
FEED_FIELDS = ("id", "feed_address", "feed_link", "image_url", "etag", "last_modified", "fetched_once")


# ------------------------------------------------------------------ database operations (sync)


def migrations_pending() -> bool:
    executor = MigrationExecutor(connection)
    return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def recover_at_start() -> None:
    """R0.13 and R3.10."""
    ts = now()
    with transaction.atomic():
        Job.objects.filter(state="running", interactive=True, created_at__lt=ts - 60).update(
            state="failed", finished_at=ts, error="worker restarted"
        )
        Job.objects.filter(state="running").update(state="queued", started_at=None)
        for feed_id in Feed.objects.filter(next_scheduled_update__lte=ts).values_list("id", flat=True):
            Feed.objects.filter(pk=feed_id).update(next_scheduled_update=ts + random.randint(0, 300))


def claim_jobs() -> list[dict]:
    with transaction.atomic():
        jobs = list(
            Job.objects.filter(state="queued")
            .order_by("-interactive", "id")
            .values("id", "type", "feed_id", "user_id", "payload", "interactive")[:100]
        )
        if jobs:
            Job.objects.filter(id__in=[j["id"] for j in jobs]).update(state="running", started_at=now())
    return jobs


def finish_job(job_id: int, result: dict | None, error: str | None) -> None:
    Job.objects.filter(pk=job_id).update(
        state="failed" if error else "done", finished_at=now(), result=result, error=(error or "")[:500] or None, blob=None
    )


def job_blob(job_id: int) -> bytes:
    blob = Job.objects.filter(pk=job_id).values_list("blob", flat=True).first()
    return bytes(blob) if blob else b""


def load_feed(feed_id: int) -> dict | None:
    return Feed.objects.filter(pk=feed_id).values(*FEED_FIELDS).first()


def due_feeds() -> list[int]:
    return list(
        Feed.objects.filter(next_scheduled_update__lte=now(), subscriptions__deleted_at__isnull=True)
        .distinct()
        .order_by("next_scheduled_update")
        .values_list("id", flat=True)[:1000]
    )


def stale_icons() -> list[int]:
    return list(
        Feed.objects.filter(fetched_once=True, favicon_checked_at__lt=now() - ICON_MAX_AGE).values_list("id", flat=True)[:3]
    )


def icon_not_found(feed_id: int) -> None:
    has_icon = FeedIcon.objects.filter(feed_id=feed_id).exists()
    Feed.objects.filter(pk=feed_id).update(favicon_not_found=not has_icon, favicon_checked_at=now())


def change_address(feed_id: int, address: str) -> int | None:
    """Point a feed at a new address (R2.21). Returns the id of the feed to fetch, if any."""
    feed = Feed.objects.filter(pk=feed_id).first()
    if feed is None or feed.feed_address == address:
        return None
    target = Feed.objects.filter(feed_address=address).first()
    if target is not None:
        pipeline._merge_into(feed, target, now())
        return target.id
    Feed.objects.filter(pk=feed_id).update(feed_address=address, etag="", last_modified="", redirects_in_row=0)
    return feed_id


def text_source(story_id: int | None, saved_id: int | None) -> str:
    if story_id:
        return Story.objects.filter(pk=story_id).values_list("permalink", flat=True).first() or ""
    return SavedStory.objects.filter(pk=saved_id).values_list("permalink", flat=True).first() or ""


def purge_deleted() -> list[dict]:
    """R7.10, R1.2, R22.5: purge soft-deleted subscriptions and folders older than 60 s, then feeds
    left without any subscription. Returns the hub unsubscribe requests to send (R4.10)."""
    ts = now()
    limit = ts - PURGE_AFTER
    requests = []
    with transaction.atomic():
        for sub in Subscription.objects.filter(deleted_at__lt=limit):
            ReadStory.objects.filter(user_id=sub.user_id, feed_id=sub.feed_id).delete()
            ViewSetting.objects.filter(user_id=sub.user_id, target_type="feed", target_id=sub.feed_id).delete()
            sub.delete()
        for folder in Folder.objects.filter(deleted_at__lt=limit):
            ViewSetting.objects.filter(user_id=folder.user_id, target_type="folder", target_id=folder.id).delete()
        Folder.objects.filter(deleted_at__lt=limit).delete()
    orphans = list(
        Feed.objects.filter(created_at__lt=limit).annotate(n=Count("subscriptions")).filter(n=0).values_list("id", flat=True)
    )
    for feed_id in orphans:
        with transaction.atomic():
            push = PushSubscription.objects.filter(feed_id=feed_id, removing=False).first()
            if push is not None:
                requests.append({"action": "unsubscribe", **websub.detach(push, ts)})
        store.purge_feed(feed_id)
        log.info("purged feed %s", feed_id)
    if orphans:
        WorkerEvent.objects.create(at=ts, type="feeds_purged", feed_id=None, payload={"feed_ids": orphans})
    return requests


def cleanup() -> None:
    """R0.13, R0.14: forget finished jobs and delivered events after an hour."""
    limit = now() - 3600
    Job.objects.filter(state__in=("done", "failed"), finished_at__lt=limit).delete()
    WorkerEvent.objects.filter(at__lt=limit).delete()
    PushSubscription.objects.filter(removing=True, requested_at__lt=now() - 86400).delete()


def heartbeat() -> None:
    Setting.put("worker_heartbeat", str(now()))


def _db_call(fn, args, kwargs):
    try:
        return fn(*args, **kwargs)
    except DatabaseError:
        connection.close()
        raise


class Waker:
    def __init__(self):
        self._event = asyncio.Event()

    def wake(self) -> None:
        event, self._event = self._event, asyncio.Event()
        event.set()

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            pass


# ------------------------------------------------------------------ coordinator


class Coordinator:
    def __init__(self):
        self.pool = Pool(settings.WORKER_PROCESSES)
        self.db_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db")  # one connection (R0.18)
        self.client = None
        self.waker = Waker()
        self.stopping = asyncio.Event()
        self.fetch_slots = asyncio.Semaphore(FETCH_CONCURRENCY)
        self.interactive_slots = asyncio.Semaphore(INTERACTIVE_CONCURRENCY)
        self.host_locks: dict[str, asyncio.Lock] = {}
        self.scheduled: set[int] = set()
        self.icons_in_flight: set[int] = set()
        self.text_failures: dict[tuple, tuple[int, dict]] = {}
        self.tasks: set[asyncio.Task] = set()

    async def db(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.db_thread, functools.partial(_db_call, fn, args, kwargs))

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)

        def done(t: asyncio.Task):
            self.tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("task failed: %r", t.exception(), exc_info=t.exception())

        task.add_done_callback(done)
        return task

    # -------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for name in ("SIGTERM", "SIGINT"):
            try:
                loop.add_signal_handler(getattr(signal, name), self.stopping.set)
            except (NotImplementedError, AttributeError, ValueError):
                pass
        while await self.db(migrations_pending):
            log.info("waiting for database migrations")
            await asyncio.sleep(2)
        await self.db(recover_at_start)
        await self.db(heartbeat)
        self.client = fetcher.make_client()
        self.pool.start()
        wakeup.start_reader(lambda: loop.call_soon_threadsafe(self.waker.wake))
        log.info("worker started with %d pool processes", settings.WORKER_PROCESSES)
        loops = [
            self.spawn(self.heartbeat_loop()),
            self.spawn(self.intake_loop()),
            self.spawn(self.scheduler_loop()),
            self.spawn(self.housekeeping_loop()),
        ]
        try:
            await self.stopping.wait()
        finally:
            log.info("worker stopping")
            for task in loops:
                task.cancel()
            await self.pool.shutdown(10)  # R23.5
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            await self.client.aclose()

    async def heartbeat_loop(self) -> None:
        """R0.19."""
        while True:
            try:
                await self.db(heartbeat)
            except Exception as e:
                log.error("heartbeat failed: %s", e)
            await asyncio.sleep(10)

    async def intake_loop(self) -> None:
        """R0.16: poll the jobs table every 200 ms, or at once when the web process signals."""
        while True:
            try:
                for job in await self.db(claim_jobs):
                    self.spawn(self.run_job(job))
            except Exception as e:
                log.error("job intake failed: %s", e)
                await asyncio.sleep(1)
            await self.waker.wait(0.2)

    async def scheduler_loop(self) -> None:
        """R3.1: every 30 s start the feeds that are due."""
        while True:
            try:
                await self.schedule_tick()
            except Exception as e:
                log.error("scheduler tick failed: %s", e)
            await asyncio.sleep(30)

    async def schedule_tick(self) -> None:
        for feed_id in await self.db(due_feeds):
            if feed_id not in self.scheduled:
                self.scheduled.add(feed_id)
                self.spawn(self.scheduled_fetch(feed_id))
        for due in await self.db(websub.renewals_due):
            self.spawn(websub.check(self, due["feed_id"], due["hub"], due["topic"], force=True))

    async def scheduled_fetch(self, feed_id: int) -> None:
        try:
            await self.fetch_feed(feed_id)
        finally:
            self.scheduled.discard(feed_id)

    async def housekeeping_loop(self) -> None:
        tick = 0
        while True:
            try:
                for request in await self.db(purge_deleted):
                    self.spawn(websub.send(self, request))
                if tick % 4 == 0:
                    for feed_id in await self.db(stale_icons):
                        self.spawn(self.fetch_icon(feed_id))
                if tick % 40 == 0:
                    await self.db(cleanup)
                    limit = now() - TEXT_FAILURE_TTL
                    self.text_failures = {k: v for k, v in self.text_failures.items() if v[0] > limit}
            except Exception as e:
                log.error("housekeeping failed: %s", e)
            tick += 1
            await asyncio.sleep(15)

    # -------------------------------------------------------------- jobs

    async def run_job(self, job: dict) -> None:
        result, error = None, None
        try:
            handler = getattr(self, f"job_{job['type']}", None)
            if handler is None:
                error = f"unknown job type {job['type']}"
            else:
                result = await handler(job)
        except fetcher.DiscoveryError as e:
            error = str(e)
        except TaskTimeout:
            error = "timeout"
        except (TaskCrashed, TaskFailed) as e:
            error = f"error: {e}"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("job %s (%s) failed", job["id"], job["type"])
            error = f"{type(e).__name__}: {e}"
        await self.db(finish_job, job["id"], result, error)
        wakeup.notify()
        log.info("job %s %s feed=%s -> %s", job["id"], job["type"], job["feed_id"], error or "done")

    async def job_discover(self, job: dict) -> dict:
        async with self.interactive_slots:
            return await fetcher.discover(self.client, self.pool, job["payload"]["url"])

    async def job_fetch_feed(self, job: dict) -> dict:
        forced = bool(job["payload"].get("forced"))
        outcome = await self.fetch_feed(job["feed_id"], forced=forced, interactive=forced)
        return {k: v for k, v in outcome.items() if k in ("status", "error", "message", "new", "updated", "same")}

    async def job_push_body(self, job: dict) -> dict:
        """R4.7: a pushed body goes through the pipeline without an HTTP request."""
        body = await self.db(job_blob, job["id"])
        fetch = {"kind": "push", "body": body, "content_type": job["payload"].get("content_type", "")}
        try:
            outcome = await self.pool.run(
                "process_feed", {"feed_id": job["feed_id"], "fetch": fetch},
                limit=TIME_LIMITS["process_feed"], feed_id=job["feed_id"],
            )  # fmt: skip
        except TaskTimeout:
            outcome = await self.db(pipeline.record_failure, job["feed_id"], 505, "Timeout")
        wakeup.notify()
        if outcome.get("fetch_needed"):
            outcome = await self.fetch_feed(job["feed_id"], forced=True, interactive=True)
        else:
            log.info("push feed=%s new=%s updated=%s", job["feed_id"], outcome.get("new"), outcome.get("updated"))
            if outcome.get("resubscribe"):
                self.spawn(websub.check(self, job["feed_id"], outcome["hub"], outcome["topic"], force=True))
        return {k: v for k, v in outcome.items() if k in ("status", "error", "new", "updated", "same")}

    async def job_extract_text(self, job: dict) -> dict:
        """R13.2, R13.9: download the page here, extract in a pool child."""
        payload = job["payload"]
        story_id, saved_id = payload.get("story_id"), payload.get("saved_id")
        key = (story_id, saved_id)
        remembered = self.text_failures.get(key)
        if remembered and not payload.get("force") and now() - remembered[0] < TEXT_FAILURE_TTL:
            return remembered[1]
        url = await self.db(text_source, story_id, saved_id)

        def failed(reason: str) -> dict:
            result = {"failed": True, "reason": reason, "url": url}
            self.text_failures[key] = (now(), result)
            return result

        if not url.startswith(("http://", "https://")):
            return failed("the story has no link")
        async with self.interactive_slots:
            page = await fetcher.download_page(self.client, url, 5 * 1024 * 1024, 15)
        if not page.ok:
            return failed(f"the page could not be loaded ({page.error or page.status})")
        kind = page.content_type.split(";")[0].strip().lower()
        if kind not in ("text/html", "application/xhtml+xml"):
            return failed("not an HTML page")
        try:
            result = await self.pool.run(
                "extract_text", {"story_id": story_id, "saved_id": saved_id, "body": page.body, "url": page.url},
                limit=TIME_LIMITS["extract_text"], interactive=True,
            )  # fmt: skip
        except TaskTimeout:
            return failed("timeout")
        if result["failed"]:
            self.text_failures[key] = (now(), result)
        return result

    async def job_fetch_icon(self, job: dict) -> dict:
        await self.fetch_icon(job["feed_id"])
        return {}

    async def job_websub_resubscribe_all(self, job: dict) -> dict:
        """R4.11."""
        subscriptions = await self.db(websub.all_subscriptions)
        for sub in subscriptions:
            await websub.check(self, sub["feed_id"], sub["hub"], sub["topic"], force=True)
        return {"count": len(subscriptions)}

    async def job_schedule_now(self, job: dict) -> dict:
        await self.schedule_tick()
        return {}

    # -------------------------------------------------------------- fetching

    async def fetch_feed(self, feed_id: int, forced: bool = False, interactive: bool = False) -> dict:
        feed = await self.db(load_feed, feed_id)
        if feed is None:
            return {"gone": True}
        if interactive:
            async with self.interactive_slots:
                result = await fetcher.download_feed(self.client, feed, forced)
        else:
            while self.pool.queued >= MAX_WAITING_BODIES:
                await asyncio.sleep(0.1)
            host = urlsplit(feed["feed_address"]).hostname or ""
            async with self.host_locks.setdefault(host, asyncio.Lock()), self.fetch_slots:
                result = await fetcher.download_feed(self.client, feed, forced)
        outcome = await self.process_download(feed_id, result, interactive)
        wakeup.notify()
        log.info(
            "fetch feed=%s %s status=%s new=%s updated=%s download=%dms process=%sms",
            feed_id, feed["feed_address"], outcome.get("status") or outcome.get("error"), outcome.get("new", 0),
            outcome.get("updated", 0), result.ms, outcome.get("process_ms", 0),
        )  # fmt: skip
        if outcome.get("rediscover"):
            self.spawn(self.rediscover(feed_id))
        if outcome.get("need_icon"):
            self.spawn(self.fetch_icon(feed_id))
        if outcome.get("status") == 200:
            self.spawn(websub.check(self, feed_id, outcome.get("hub"), outcome.get("topic")))
        if outcome.get("merged_into"):
            self.spawn(self.fetch_feed(outcome["merged_into"]))
        return outcome

    async def process_download(self, feed_id: int, result: fetcher.Download, interactive: bool) -> dict:
        if result.error_code:
            return await self.db(pipeline.record_failure, feed_id, result.error_code, result.error, result.ms)
        if result.status == 304:
            return await self.db(pipeline.record_not_modified, feed_id, result.ms, result.headers)
        if result.status >= 400:
            try:
                reason = http.HTTPStatus(result.status).phrase
            except ValueError:
                reason = "HTTP error"
            return await self.db(pipeline.record_failure, feed_id, result.status, reason, result.ms, result.headers)
        fetch = {
            "kind": "fetch", "body": result.body, "content_type": result.content_type, "headers": result.headers,
            "permanent_url": result.permanent_url, "download_ms": result.ms,
        }  # fmt: skip
        try:
            return await self.pool.run(
                "process_feed", {"feed_id": feed_id, "fetch": fetch},
                limit=TIME_LIMITS["process_feed"], feed_id=feed_id, interactive=interactive,
            )  # fmt: skip
        except TaskTimeout:
            return await self.db(pipeline.record_failure, feed_id, 505, "Timeout", result.ms)  # R0.26
        except (TaskCrashed, TaskFailed):
            return await self.db(pipeline.record_failure, feed_id, 500, "Error", result.ms)  # R0.27

    async def rediscover(self, feed_id: int) -> None:
        """R2.21: repair the address of a feed that never fetched successfully."""
        feed = await self.db(load_feed, feed_id)
        if feed is None:
            return
        try:
            found = await fetcher.discover(self.client, self.pool, feed["feed_link"] or feed["feed_address"], False)
        except (fetcher.DiscoveryError, TaskTimeout, TaskCrashed, TaskFailed):
            return
        target = await self.db(change_address, feed_id, found["feed_address"])
        if target is not None:
            log.info("feed %s: address rediscovered as %s", feed_id, found["feed_address"])
            self.spawn(self.fetch_feed(target))

    async def fetch_icon(self, feed_id: int) -> None:
        """R8.1, R8.2: the coordinator downloads, a pool child decodes and computes the color."""
        if feed_id in self.icons_in_flight:
            return
        self.icons_in_flight.add(feed_id)
        try:
            feed = await self.db(load_feed, feed_id)
            if feed is None:
                return
            page_url = feed["feed_link"] or ""
            candidates = []
            try:
                if page_url:
                    page = await fetcher.download_page(self.client, page_url, 2 * 1024 * 1024, 10)
                    if page.ok and page.body:
                        parsed = await self.pool.run(
                            "parse_page", {"body": page.body, "base_url": page.url, "content_type": page.content_type},
                            limit=TIME_LIMITS["parse_page"],
                        )  # fmt: skip
                        candidates.extend(parsed.get("icon_links") or [])
                if feed["image_url"]:
                    candidates.append(urljoin(page_url or feed["feed_address"], feed["image_url"]))
                candidates.append(site_root(page_url or feed["feed_address"]) + "/favicon.ico")
                headers = {"User-Agent": fetcher.BROWSER_UA, "Accept": "image/png,image/x-icon,image/*;q=0.9,*/*;q=0.8"}
                for url in list(dict.fromkeys(candidates))[:6]:
                    image = await fetcher.download(self.client, url, headers=headers, max_bytes=512 * 1024, total_timeout=10)
                    if not image.ok or not image.body:
                        continue
                    stored = await self.pool.run(
                        "process_icon", {"feed_id": feed_id, "data": image.body, "icon_url": url},
                        limit=TIME_LIMITS["process_icon"],
                    )  # fmt: skip
                    if stored.get("ok"):
                        wakeup.notify()
                        return
            except (TaskTimeout, TaskCrashed, TaskFailed) as e:
                log.info("icon for feed %s failed: %r", feed_id, e)
            await self.db(icon_not_found, feed_id)  # R8.6
        finally:
            self.icons_in_flight.discard(feed_id)
