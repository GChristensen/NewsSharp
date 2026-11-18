"""The worker's coordinator (worker/coordinator.py): one event loop running the scheduler,
job intake, every outbound request, the heartbeat and the pool. It parses nothing."""
import asyncio
import contextlib
import functools
import http
import logging
import math
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

FETCH_CONCURRENCY = 4  # scheduled downloads at a time
INTERACTIVE_CONCURRENCY = 4  # interactive downloads at a time
FORCED_ICON_SECONDS = 24  # the downloads of a "Fetch icon" search, within the web process's 30 s wait
MAX_WAITING_BODIES = 8  # downloaded bodies waiting for a pool child
PURGE_AFTER = 60  # seconds a soft delete can be undone
ICON_MAX_AGE = 30 * 86400  # icons are looked up again after 30 days
TEXT_FAILURE_TTL = 600  # a failed text extraction is not retried for 10 min
FEED_FIELDS = (
    "id", "feed_address", "feed_link", "image_url", "etag", "last_modified", "fetched_once", "use_proxy", "icon_link",
)  # fmt: skip


# ------------------------------------------------------------------ database operations (sync)


def migrations_pending() -> bool:
    executor = MigrationExecutor(connection)
    return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def recover_at_start() -> None:
    """Requeue jobs left running by a previous worker (interactive ones older than 60 s fail) and
    spread overdue feeds over the first 5 minutes instead of fetching them at once."""
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
    """The three icons checked longest ago, so a search that keeps failing cannot hold the queue."""
    return list(
        Feed.objects.filter(fetched_once=True, favicon_checked_at__lt=now() - ICON_MAX_AGE)
        .order_by("favicon_checked_at", "id")
        .values_list("id", flat=True)[:3]
    )


def icon_not_found(feed_id: int) -> None:
    has_icon = FeedIcon.objects.filter(feed_id=feed_id).exists()
    Feed.objects.filter(pk=feed_id).update(favicon_not_found=not has_icon, favicon_checked_at=now())


def change_address(feed_id: int, address: str) -> int | None:
    """Point a feed at a new address. Returns the id of the feed to fetch, if any.
    A feed that worked before is never merged into another one: the merge deletes its stories and
    read state, and the rediscovered address may be a different feed of the same site."""
    feed = Feed.objects.filter(pk=feed_id).first()
    if feed is None or feed.feed_address == address:
        return None
    target = Feed.objects.filter(feed_address=address).first()
    if target is not None and feed.known_good:
        log.info("feed %s: rediscovered address %s belongs to feed %s, not merging", feed_id, address, target.id)
        return None
    if target is not None:
        pipeline._merge_into(feed, target, now())
        return target.id
    Feed.objects.filter(pk=feed_id).update(feed_address=address, etag="", last_modified="", redirects_in_row=0)
    return feed_id


def text_source(story_id: int | None, saved_id: int | None) -> tuple[str, bool]:
    """The page of a story and whether its feed is fetched through the proxy."""
    if story_id:
        row = Story.objects.filter(pk=story_id).values_list("permalink", "feed_id").first()
    else:
        row = SavedStory.objects.filter(pk=saved_id).values_list("permalink", "feed_id").first()
    if row is None:
        return "", False
    permalink, feed_id = row
    use_proxy = bool(feed_id) and Feed.objects.filter(pk=feed_id, use_proxy=True).exists()
    return permalink or "", use_proxy


def purge_deleted() -> list[dict]:
    """Purge soft-deleted subscriptions and folders older than 60 s, then feeds
    left without any subscription. Returns the hub unsubscribe requests to send."""
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
    """Forget finished jobs and delivered events after an hour."""
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
        self.db_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db")  # one connection
        self.client = None
        self.proxy_client = None  # only when config.yaml sets proxy.url
        self.waker = Waker()
        self.stopping = asyncio.Event()
        self.fetch_slots = asyncio.Semaphore(FETCH_CONCURRENCY)
        self.interactive_slots = asyncio.Semaphore(INTERACTIVE_CONCURRENCY)
        self.host_locks: dict[str, asyncio.Lock] = {}
        self.scheduled: set[int] = set()
        self.icon_searches: dict[int, tuple[asyncio.Task, bool]] = {}  # feed id -> (search, forced)
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
        if settings.PROXY_URL:
            self.proxy_client = fetcher.make_client(proxied=True)
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
            await self.pool.shutdown(10)  # let running tasks finish
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            await self.client.aclose()
            if self.proxy_client is not None:
                await self.proxy_client.aclose()

    def client_for(self, use_proxy: bool):
        """The client for the requests of a feed: through the proxy when the feed is set to use it
        and a proxy is configured, directly otherwise."""
        if use_proxy and self.proxy_client is not None:
            return self.proxy_client
        return self.client

    async def heartbeat_loop(self) -> None:
        """Write the heartbeat every 10 s; the web process and the supervisor watch it."""
        while True:
            try:
                await self.db(heartbeat)
            except Exception as e:
                log.error("heartbeat failed: %s", e)
            await asyncio.sleep(10)

    async def intake_loop(self) -> None:
        """Poll the jobs table every 200 ms, or at once when the web process signals."""
        while True:
            try:
                for job in await self.db(claim_jobs):
                    self.spawn(self.run_job(job))
            except Exception as e:
                log.error("job intake failed: %s", e)
                await asyncio.sleep(1)
            await self.waker.wait(0.2)

    async def scheduler_loop(self) -> None:
        """Every 30 s start the feeds that are due."""
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
        """A fetch that fails unexpectedly is recorded as an error, which also reschedules the feed;
        otherwise it would stay due and be requested again on every tick."""
        try:
            await self.fetch_feed(feed_id)
        except Exception:
            log.exception("fetch of feed %s failed", feed_id)
            try:
                await self.db(pipeline.record_failure, feed_id, 500, "Error")
            except Exception as e:
                log.error("recording the failure of feed %s failed: %s", feed_id, e)
        finally:
            self.scheduled.discard(feed_id)

    async def housekeeping_loop(self) -> None:
        tick = 0
        while True:
            try:
                for request in await self.db(purge_deleted):
                    self.spawn(websub.send(self, request))
                await self.db(websub.expire_leases)
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
            client = self.client_for(bool(job["payload"].get("proxy")))
            return await fetcher.discover(client, self.pool, job["payload"]["url"])

    async def job_fetch_feed(self, job: dict) -> dict:
        forced = bool(job["payload"].get("forced"))
        outcome = await self.fetch_feed(job["feed_id"], forced=forced, interactive=forced)
        return {k: v for k, v in outcome.items() if k in ("status", "error", "message", "new", "updated", "same")}

    async def job_push_body(self, job: dict) -> dict:
        """A pushed body goes through the pipeline without an HTTP request."""
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
        """Download the page here, extract in a pool child."""
        payload = job["payload"]
        story_id, saved_id = payload.get("story_id"), payload.get("saved_id")
        key = (story_id, saved_id)
        remembered = self.text_failures.get(key)
        if remembered and not payload.get("force") and now() - remembered[0] < TEXT_FAILURE_TTL:
            return remembered[1]
        url, use_proxy = await self.db(text_source, story_id, saved_id)

        def failed(reason: str) -> dict:
            result = {"failed": True, "reason": reason, "url": url}
            self.text_failures[key] = (now(), result)
            return result

        if not url.startswith(("http://", "https://")):
            return failed("the story has no link")
        async with self.interactive_slots:
            page = await fetcher.download_page(self.client_for(use_proxy), url, 5 * 1024 * 1024, 15)
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
        return await self.fetch_icon(job["feed_id"], forced=bool(job["payload"].get("force")))

    async def job_websub_resubscribe_all(self, job: dict) -> dict:
        """Renew every push subscription, for example after the callback address changed."""
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
                result = await fetcher.download_feed(self.client_for(feed["use_proxy"]), feed, forced)
        else:
            while self.pool.queued >= MAX_WAITING_BODIES:
                await asyncio.sleep(0.1)
            host = urlsplit(feed["feed_address"]).hostname or ""
            async with self.host_locks.setdefault(host, asyncio.Lock()), self.fetch_slots:
                result = await fetcher.download_feed(self.client_for(feed["use_proxy"]), feed, forced)
        outcome = await self.process_download(feed_id, feed["feed_address"], result, interactive)
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

    async def process_download(self, feed_id: int, address: str, result: fetcher.Download, interactive: bool) -> dict:
        if result.error_code:
            return await self.db(pipeline.record_failure, feed_id, result.error_code, result.error, result.ms)
        if result.status == 304:
            redirected = fetcher.redirected_elsewhere(result, address)
            return await self.db(pipeline.record_not_modified, feed_id, result.ms, result.headers, redirected)
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
            return await self.db(pipeline.record_failure, feed_id, 505, "Timeout", result.ms)
        except (TaskCrashed, TaskFailed):
            return await self.db(pipeline.record_failure, feed_id, 500, "Error", result.ms)

    async def rediscover(self, feed_id: int) -> None:
        """Repair the address of a feed that never fetched successfully."""
        feed = await self.db(load_feed, feed_id)
        if feed is None:
            return
        try:
            client = self.client_for(feed["use_proxy"])
            found = await fetcher.discover(client, self.pool, feed["feed_link"] or feed["feed_address"], False)
        except (fetcher.DiscoveryError, TaskTimeout, TaskCrashed, TaskFailed):
            return
        target = await self.db(change_address, feed_id, found["feed_address"])
        if target is not None:
            log.info("feed %s: address rediscovered as %s", feed_id, found["feed_address"])
            self.spawn(self.fetch_feed(target))

    async def fetch_icon(self, feed_id: int, forced: bool = False) -> dict:
        """Look for a feed's icon; one search per feed at a time, later requests share it.
        A forced search (the "Fetch icon" button) that the running one cannot satisfy starts another.
        Returns {found, icon_url, via_proxy, tried}."""
        if feed_id in self.icon_searches:
            running, running_forced = self.icon_searches[feed_id]
            outcome = await asyncio.shield(running)
            if outcome["found"] or not forced or running_forced:
                return outcome
        search = self.spawn(self.search_icon(feed_id, forced))
        self.icon_searches[feed_id] = (search, forced)

        def forget(done: asyncio.Task) -> None:
            if self.icon_searches.get(feed_id, (None,))[0] is done:
                del self.icon_searches[feed_id]

        search.add_done_callback(forget)
        return await asyncio.shield(search)

    async def search_icon(self, feed_id: int, forced: bool) -> dict:
        """The coordinator downloads, a pool child decodes and computes the color. A forced search
        that finds nothing directly repeats the whole search through the proxy, since sites that block
        automated clients usually answer 403 or with a page instead of failing to connect.
        A forced search is someone waiting in the feed settings dialog, so both passes together stay
        within FORCED_ICON_SECONDS, which the web process's 30 s wait covers; the direct pass
        gets the first part of it when the proxy pass may follow."""
        outcome = {"found": False, "icon_url": "", "favicon_color": "", "tried": 0, "out_of_time": False}
        feed = await self.db(load_feed, feed_id)
        if feed is None:
            return outcome
        direct = self.client_for(feed["use_proxy"])
        proxy = self.proxy_client if self.proxy_client is not None and direct is not self.proxy_client else None
        tried: set[str] = set()
        loop = asyncio.get_running_loop()
        end = loop.time() + FORCED_ICON_SECONDS if forced else math.inf
        log.info(
            "icon feed=%s: %s search, site %s, proxy %s", feed_id, "forced" if forced else "background",
            feed["feed_link"] or "-", "always" if direct is self.proxy_client else "on retry" if proxy else "none",
        )  # fmt: skip
        page: dict = {}  # "links": the icon links of the site's page, once it was downloaded
        try:
            async with (self.interactive_slots if forced else contextlib.nullcontext()):
                clients = [direct, proxy] if proxy else [direct]
                retry = forced and proxy is not None
                first_end = min(end, loop.time() + FORCED_ICON_SECONDS / 2) if retry else end
                stored = await self.icon_pass(feed, clients, tried, page, first_end)
                if stored is None and retry and clients[0] is not proxy:
                    clients = [proxy]
                    stored = await self.icon_pass(feed, clients, tried, page, end)
        except (TaskTimeout, TaskCrashed, TaskFailed) as e:
            log.info("icon for feed %s failed: %r", feed_id, e)
            stored = None
        except Exception:
            # Recorded as "not found" below, so the feed leaves the stale-icon queue.
            log.exception("icon search for feed %s failed", feed_id)
            stored = None
        outcome["tried"] = len(tried)
        outcome["out_of_time"] = stored is None and loop.time() >= end - 1
        if stored is None:
            log.info("icon feed=%s: no icon found, %d addresses tried", feed_id, len(tried))
            await self.db(icon_not_found, feed_id)  # the default icon is shown
            return outcome
        wakeup.notify()
        return {**outcome, "found": True, "icon_url": stored[0], "favicon_color": stored[1]}

    async def icon_pass(
        self, feed: dict, clients: list, tried: set[str], page: dict, end: float = math.inf
    ) -> tuple[str, str] | None:
        """One search over the site's page, the feed's image and /favicon.ico, or only over the icon
        address set by hand. A site the first client cannot reach is retried through the next, which is
        kept for the rest of the pass (`clients` is shortened in place). The page is downloaded once
        per search: a later pass reuses the links in `page`. Downloads stop at `end` (event loop time).
        Returns the address and the color of the stored icon."""
        page_url = feed["feed_link"] or ""
        loop = asyncio.get_running_loop()
        feed_id = feed["id"]

        def time_left() -> float | None:
            """At most 10 s for a download, None when too little time is left to start one."""
            left = end - loop.time()
            if left < 1:
                log.info("icon feed=%s: out of time", feed_id)
                return None
            return min(10.0, left)

        async def get(url: str, call):
            """Download `url` with call(client, timeout), logging each attempt."""
            for attempt in (1, 2):
                timeout = time_left()
                if timeout is None:
                    return None
                client = clients[0]
                result = await call(client, timeout)
                via = "proxy" if client is self.proxy_client else "direct"
                if result.error_code:
                    answer = f"error {result.error_code} {result.error}"
                else:
                    answer = f"{result.status} {result.content_type or '-'} {len(result.body)} bytes"
                log.info("icon feed=%s %s %s -> %s (%d ms)", feed_id, via, url, answer, result.ms)
                if attempt == 2 or result.error_code not in (502, 504) or len(clients) < 2:
                    return result
                clients.pop(0)

        if feed["icon_link"]:
            candidates = [feed["icon_link"]]
            log.info("icon feed=%s: the icon address is set by hand", feed_id)
        else:
            candidates = await self.page_icon_links(feed, page, get)
            if candidates is None:
                return None
            if feed["image_url"]:
                candidates.append(urljoin(page_url or feed["feed_address"], feed["image_url"]))
            candidates.append(site_root(page_url or feed["feed_address"]) + "/favicon.ico")
        headers = {"User-Agent": fetcher.BROWSER_UA, "Accept": "image/png,image/x-icon,image/*;q=0.9,*/*;q=0.8"}
        for url in list(dict.fromkeys(candidates))[:6]:
            image = await get(
                url,
                lambda client, timeout, url=url: fetcher.download(
                    client, url, headers=headers, max_bytes=512 * 1024, total_timeout=timeout
                ),
            )
            if image is None:
                return None
            tried.add(url)
            if not image.ok or not image.body:
                continue
            stored = await self.pool.run(
                "process_icon", {"feed_id": feed_id, "data": image.body, "icon_url": url},
                limit=TIME_LIMITS["process_icon"],
            )  # fmt: skip
            if stored.get("ok"):
                log.info("icon feed=%s: stored %s", feed_id, url)
                return url, stored["color"]
            log.info("icon feed=%s: %s rejected: %s", feed_id, url, stored.get("reason"))
        return None

    async def page_icon_links(self, feed: dict, page: dict, get) -> list[str] | None:
        """The icon links of the feed's site page: downloaded with `get` the first time, kept in `page`
        once the download worked. None when the search ran out of time."""
        if "links" in page:
            return list(page["links"])
        page_url = feed["feed_link"] or ""
        if not page_url:
            return []
        result = await get(
            page_url, lambda client, timeout: fetcher.download_page(client, page_url, 2 * 1024 * 1024, timeout)
        )
        if result is None:
            return None
        if not (result.ok and result.body):
            return []
        parsed = await self.pool.run(
            "parse_page", {"body": result.body, "base_url": result.url, "content_type": result.content_type},
            limit=TIME_LIMITS["parse_page"],
        )  # fmt: skip
        page["links"] = list(parsed.get("icon_links") or [])
        log.info("icon feed=%s: the page links %d icons: %s", feed["id"], len(page["links"]), " ".join(page["links"]) or "-")
        return list(page["links"])
