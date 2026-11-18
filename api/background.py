"""The web process's background loop (api/background.py): reads worker_events and turns them into
per-user events (R0.14, R21.2), watches the worker's heartbeat (R0.21) and wakes job waiters (R0.16)."""
import asyncio
import logging

from asgiref.sync import sync_to_async
from django.db import connection

from feeds.models import Setting
from feeds.util import now
from reading.models import Subscription
from reading.unread import unread_counts
from worker import wakeup
from worker.models import WorkerEvent

from .events import bus

log = logging.getLogger("web.background")

HEARTBEAT_ALIVE = 120  # R0.19

_task: asyncio.Task | None = None
_tick: asyncio.Event | None = None
_worker_state = {"alive": True, "last_heartbeat": 0}


def worker_state() -> dict:
    """{alive, last_heartbeat} for the bootstrap response and the `worker` event (R0.21)."""
    try:
        heartbeat = int(Setting.get("worker_heartbeat") or 0)
    except ValueError:
        heartbeat = 0
    return {"alive": now() - heartbeat <= HEARTBEAT_ALIVE, "last_heartbeat": heartbeat}


def wake() -> None:
    """Release everything waiting in wait_tick."""
    global _tick
    if _tick is not None:
        event, _tick = _tick, asyncio.Event()
        event.set()


async def wait_tick(timeout: float = 0.2) -> None:
    """Wait for the polling interval or for the worker's wake-up signal, whichever comes first."""
    global _tick
    if _tick is None:
        _tick = asyncio.Event()
    try:
        await asyncio.wait_for(_tick.wait(), timeout)
    except asyncio.TimeoutError:
        pass


def _latest_event_id() -> int:
    try:
        return WorkerEvent.objects.order_by("-id").values_list("id", flat=True).first() or 0
    finally:
        connection.close()


def _deliver(last_id: int) -> int:
    """Translate new worker_events rows into events for the connected subscribers of each feed."""
    try:
        users = bus.connected_users()
        rows = list(WorkerEvent.objects.filter(id__gt=last_id).order_by("id")[:500])
        if not rows:
            return last_id
        if users:
            feed_ids = {row.feed_id for row in rows if row.feed_id}
            subscribers: dict[int, list] = {}
            subs = Subscription.objects.filter(feed_id__in=feed_ids, user_id__in=users, deleted_at__isnull=True)
            for sub in subs.select_related("user"):
                subscribers.setdefault(sub.feed_id, []).append(sub.user)
            for row in rows:
                for user in subscribers.get(row.feed_id, ()):
                    _publish(user, row)
        return rows[-1].id
    finally:
        connection.close()


def _publish(user, row: WorkerEvent) -> None:
    feed_id, payload = row.feed_id, row.payload or {}
    if row.type == "feed_fetched":
        bus.publish(user.id, "feed:fetched", {"feed_id": feed_id, **payload})
    elif row.type == "new_stories":
        bus.publish(user.id, "feed:new_stories", {"feed_id": feed_id, **payload})
        bus.publish(user.id, "counts", {"feeds": unread_counts(user, [feed_id])})
    elif row.type == "icon":
        bus.publish(user.id, "feed:icon", {"feed_id": feed_id, **payload})
    elif row.type == "push_state":
        bus.publish(user.id, "feed:push", {"feed_id": feed_id, **payload})
    elif row.type == "tree_changed":
        bus.publish(user.id, "tree", {"version": row.id})


def _check_worker() -> dict:
    try:
        return worker_state()
    finally:
        connection.close()


async def _run() -> None:
    """Every step runs inside the loop's error handling: an exception that escaped would end the
    task silently and with it all live updates."""
    global _worker_state
    last_id: int | None = None
    last_check = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            if last_id is None:
                last_id = await sync_to_async(_latest_event_id, thread_sensitive=False)()  # R21.5
                _worker_state = await sync_to_async(_check_worker, thread_sensitive=False)()
            last_id = await sync_to_async(_deliver, thread_sensitive=False)(last_id)
            if loop.time() - last_check > 5:
                last_check = loop.time()
                state = await sync_to_async(_check_worker, thread_sensitive=False)()
                if state["alive"] != _worker_state["alive"]:
                    for user_id in bus.connected_users():
                        bus.publish(user_id, "worker", state)
                _worker_state = state
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("background loop failed: %s", e)
            await asyncio.sleep(1)
        await wait_tick(0.2)


def start() -> None:
    global _task, _tick
    loop = asyncio.get_running_loop()
    _tick = asyncio.Event()
    wakeup.start_reader(lambda: loop.call_soon_threadsafe(wake))
    _task = loop.create_task(_run())


async def stop() -> None:
    bus.close_all()
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
