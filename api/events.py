"""Live updates (api/events.py): an in-process bus with one bounded queue per connection,
a ring buffer of the last 200 events per user, and the Server-Sent Events view."""
import asyncio
import json
import threading
import time
from collections import deque

from django.http import StreamingHttpResponse

from .http import error_response

RING_SIZE = 200  # events kept per user for replay after a reconnect
QUEUE_SIZE = 256  # events waiting per connection before it is dropped
MAX_CONNECTIONS = 6  # open streams per user
KEEP_ALIVE = 25  # seconds between keep-alive comments


class Connection:
    def __init__(self, user_id: int, loop):
        self.user_id = user_id
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.closed = False

    def offer(self, item) -> None:
        """Runs in the connection's event loop."""
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.closed = True  # a slow client is disconnected and resynchronizes

    def close(self) -> None:
        self.closed = True
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


class Bus:
    def __init__(self):
        self.lock = threading.Lock()
        self.connections: dict[int, list[Connection]] = {}
        self.rings: dict[int, deque] = {}
        # Ids keep increasing across restarts of the web process, so a stale Last-Event-ID is
        # recognized and answered with `resync`.
        self.boot_id = int(time.time() * 1000)
        self.next_id = self.boot_id + 1

    def publish(self, user_id: int, event: str, data: dict, origin: str | None = None) -> None:
        """Deliver an event to all connections of a user. Callable from any thread."""
        payload = dict(data)
        if origin:
            payload["origin"] = origin
        with self.lock:
            item = (self.next_id, event, json.dumps(payload, separators=(",", ":")))
            self.next_id += 1
            self.rings.setdefault(user_id, deque(maxlen=RING_SIZE)).append(item)
            targets = list(self.connections.get(user_id, ()))
        for connection in targets:
            connection.loop.call_soon_threadsafe(connection.offer, item)

    def connected_users(self) -> list[int]:
        with self.lock:
            return [user_id for user_id, connections in self.connections.items() if connections]

    def register(self, user_id: int, loop, last_id: int | None) -> tuple[Connection, list]:
        """Add a connection and return the events to send first: missed ones, or a resync."""
        connection = Connection(user_id, loop)
        with self.lock:
            connections = self.connections.setdefault(user_id, [])
            connections.append(connection)
            evicted = connections[:-MAX_CONNECTIONS]
            del connections[:-MAX_CONNECTIONS]
            ring = self.rings.get(user_id, ())
            backlog = []
            if last_id is not None:
                complete = last_id >= self.boot_id and not (len(ring) == RING_SIZE and ring[0][0] > last_id + 1)
                if complete:
                    backlog = [item for item in ring if item[0] > last_id]
                else:
                    backlog = [(self.next_id, "resync", "{}")]
                    self.next_id += 1
        for old in evicted:
            old.loop.call_soon_threadsafe(old.close)
        return connection, backlog

    def unregister(self, connection: Connection) -> None:
        with self.lock:
            connections = self.connections.get(connection.user_id, [])
            if connection in connections:
                connections.remove(connection)

    def close_all(self) -> None:
        with self.lock:
            targets = [c for connections in self.connections.values() for c in connections]
        for connection in targets:
            connection.loop.call_soon_threadsafe(connection.close)


bus = Bus()


def _format(item) -> str:
    event_id, event, data = item
    return f"id: {event_id}\nevent: {event}\ndata: {data}\n\n"


async def events_view(request):
    """GET /api/events."""
    user = await request.auser()
    if not user.is_authenticated:
        return error_response(401, "unauthenticated", "Log in to continue.")
    raw_last = request.headers.get("Last-Event-ID") or request.GET.get("last_event_id")
    try:
        last_id = int(raw_last) if raw_last else None
    except ValueError:
        last_id = None
    connection, backlog = bus.register(user.id, asyncio.get_running_loop(), last_id)

    async def stream():
        try:
            yield ": connected\n\n"
            for item in backlog:
                yield _format(item)
            while not connection.closed:
                try:
                    item = await asyncio.wait_for(connection.queue.get(), KEEP_ALIVE)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None or connection.closed:
                    break
                yield _format(item)
        finally:
            bus.unregister(connection)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
