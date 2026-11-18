"""The worker's process pool (worker/pool.py): spawned children with one pipe each,
killed on time limit, recycled after 200 tasks or 500 MB, one task per feed at a time, priority
for interactive tasks."""
import asyncio
import logging
import multiprocessing
import os
import signal
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import psutil

log = logging.getLogger("worker.pool")

RECYCLE_TASKS = 200
RECYCLE_BYTES = 500 * 1024 * 1024
MAX_EXTRA_CHILDREN = 4


class TaskTimeout(Exception):
    pass


class TaskCrashed(Exception):
    pass


class TaskFailed(Exception):
    pass


def child_main(conn, index: int) -> None:
    """Entry point of a pool child. Imports everything up front so tasks start without cost
    and exits when the coordinator's end of the pipe closes."""
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["NEWSSHARP_PROC"] = f"worker/{index}"
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    import django

    django.setup()
    import trafilatura  # noqa: F401  (heavy import, done before the first task)

    from worker import tasks

    child_log = logging.getLogger("worker.child")
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            break
        if message is None:
            break
        name, payload = message
        try:
            reply = (True, tasks.TASKS[name](**payload))
        except Exception as e:
            child_log.exception("task %s failed", name)
            reply = (False, f"{type(e).__name__}: {e}")
        try:
            conn.send(reply)
        except (EOFError, OSError):
            break


class Child:
    def __init__(self, ctx, index: int, extra: bool = False):
        parent_conn, child_conn = ctx.Pipe()
        self.index = index
        self.extra = extra
        self.tasks = 0
        self.dead = False
        self.process = ctx.Process(target=child_main, args=(child_conn, index), daemon=True)
        self.process.start()
        child_conn.close()
        self.conn = parent_conn

    def roundtrip(self, message):
        self.conn.send(message)
        return self.conn.recv()

    def kill(self) -> None:
        self.dead = True
        try:
            self.process.kill()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    def retire(self) -> None:
        """Ask the child to exit after its current message; used for recycling."""
        self.dead = True
        try:
            self.conn.send(None)
            self.conn.close()
        except Exception:
            pass

    def memory(self) -> int:
        try:
            return psutil.Process(self.process.pid).memory_info().rss
        except Exception:
            return 0


class Pool:
    def __init__(self, size: int):
        self.size = size
        self.ctx = multiprocessing.get_context("spawn")
        self.io = ThreadPoolExecutor(max_workers=size + MAX_EXTRA_CHILDREN + 4, thread_name_prefix="pool-io")
        self.idle: deque[Child] = deque()
        self.children: list[Child] = []
        self.waiting_interactive: deque[asyncio.Future] = deque()
        self.waiting_background: deque[asyncio.Future] = deque()
        self.feed_locks: dict[int, asyncio.Lock] = {}
        self.feed_waiters: dict[int, int] = {}
        self.extras = 0
        self.next_index = 1
        self.closed = False

    @property
    def queued(self) -> int:
        """Tasks waiting for a free child."""
        return len(self.waiting_background) + len(self.waiting_interactive)

    def _spawn(self, extra: bool = False) -> Child:
        child = Child(self.ctx, self.next_index, extra)
        self.next_index += 1
        if not extra:
            self.children.append(child)
        log.info("started child worker/%d%s", child.index, " (extra)" if extra else "")
        return child

    def start(self) -> None:
        for _ in range(self.size):
            self.idle.append(self._spawn())

    async def _acquire(self, interactive: bool) -> Child:
        if self.idle:
            return self.idle.popleft()
        if interactive and self.extras < MAX_EXTRA_CHILDREN:
            self.extras += 1  # one extra child for an interactive task when all are busy
            return self._spawn(extra=True)
        future = asyncio.get_running_loop().create_future()
        (self.waiting_interactive if interactive else self.waiting_background).append(future)
        return await future

    def _hand_over(self, child: Child) -> None:
        for queue in (self.waiting_interactive, self.waiting_background):
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_result(child)
                    return
        self.idle.append(child)

    def _release(self, child: Child) -> None:
        if child.extra:
            self.extras -= 1
            if not child.dead:
                child.retire()
            log.info("retired extra child worker/%d", child.index)
            return
        if not child.dead:
            child.tasks += 1
            if child.tasks >= RECYCLE_TASKS or child.memory() > RECYCLE_BYTES:
                log.info("recycling child worker/%d after %d tasks", child.index, child.tasks)
                child.retire()
        if child.dead:
            if child in self.children:
                self.children.remove(child)
            if self.closed:
                return
            child = self._spawn()
        self._hand_over(child)

    async def run(self, name: str, payload: dict, *, limit: float, feed_id: int | None = None, interactive: bool = False):
        """Run one task in a child. Raises TaskTimeout (child killed), TaskCrashed or TaskFailed."""
        lock = None
        if feed_id is not None:
            lock = self.feed_locks.setdefault(feed_id, asyncio.Lock())
            self.feed_waiters[feed_id] = self.feed_waiters.get(feed_id, 0) + 1
            await lock.acquire()
        try:
            child = await self._acquire(interactive)
            try:
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(self.io, child.roundtrip, (name, payload))
                try:
                    ok, result = await asyncio.wait_for(future, limit)
                except asyncio.TimeoutError:
                    child.kill()  # a task over its time limit kills the child
                    log.warning("killed child worker/%d: task %s exceeded %ss", child.index, name, limit)
                    raise TaskTimeout(name) from None
                except (EOFError, OSError, BrokenPipeError):
                    child.kill()  # the child died; make sure it is gone
                    log.warning("child worker/%d exited during task %s", child.index, name)
                    raise TaskCrashed(name) from None
                except asyncio.CancelledError:
                    child.kill()
                    raise
                if not ok:
                    raise TaskFailed(result)
                return result
            finally:
                self._release(child)
        finally:
            if lock is not None:
                lock.release()
                self.feed_waiters[feed_id] -= 1
                if self.feed_waiters[feed_id] == 0:
                    del self.feed_waiters[feed_id]
                    self.feed_locks.pop(feed_id, None)

    async def shutdown(self, grace: float = 10) -> None:
        """Let running tasks finish for up to `grace` seconds, then stop the children."""
        self.closed = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + grace
        while len(self.idle) < len(self.children) and loop.time() < deadline:
            await asyncio.sleep(0.1)
        for child in list(self.children):
            child.retire()
        await asyncio.sleep(0.3)
        for child in list(self.children):
            if child.process.is_alive():
                child.kill()
        self.io.shutdown(wait=False, cancel_futures=True)
