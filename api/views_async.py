"""Async API views (api/views_async.py): the endpoints that wait for a worker job (R0.12, R0.32).
They reach the ORM through sync_to_async."""
import asyncio
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.db import connection, transaction

from feeds import store
from feeds.models import Feed, Story
from feeds.util import InvalidUrl, normalize_url, now
from reading import tree
from reading.models import SavedStory, Subscription

from . import jobs
from .events import bus
from .http import ApiError, client_id, endpoint, require_str

_text_slots: dict[int, asyncio.Semaphore] = {}


def _in_thread(fn):
    """Run a blocking database function in a worker thread and release its connection."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            connection.close()

    return sync_to_async(wrapper, thread_sensitive=False)


def _normalize(url: str) -> str:
    try:
        return normalize_url(url)
    except InvalidUrl:
        raise ApiError(422, "invalid_url", "This address does not point to an RSS feed or a website with an RSS feed.") from None


async def _find_feed(user, url: str, proxy: bool = False) -> dict:
    """An existing feed by address, or a `discover` job (R2.20), through the proxy with `proxy`."""
    address = _normalize(url)

    def existing():
        feed = Feed.objects.filter(feed_address=address).first()
        return {"feed_address": feed.feed_address, "title": feed.title, "site_link": feed.feed_link} if feed else None

    found = await _in_thread(existing)()
    if found is None:
        found = await jobs.run_and_wait("discover", user_id=user.id, payload={"url": address, "proxy": proxy})
    return found


def _subscribed_id(user, address: str) -> int | None:
    return (
        tree.live_subs(user).filter(feed__feed_address=address).values_list("feed_id", flat=True).first()
    )


async def _discover(request, data):
    found = await _find_feed(request.user, require_str(data, "url", ""))
    found["subscribed_feed_id"] = await _in_thread(_subscribed_id)(request.user, found["feed_address"])
    return found


def _create_subscription(user, found: dict, folder_id, new_folder: str) -> dict:
    with transaction.atomic():
        try:
            if new_folder:
                folder_id = tree.create_folder(user, new_folder, folder_id).id
            else:
                tree.get_folder(user, folder_id)
        except tree.TreeError as e:
            raise ApiError(422, "invalid", str(e)) from None
        feed, _ = store.get_or_create_feed(found["feed_address"], found.get("title") or "", found.get("site_link") or "")
        _, created = tree.subscribe(user, feed, folder_id)
    if not feed.fetched_once or feed.last_update < now() - 86400:
        # R3.8: a new subscription is fetched at once, ahead of scheduled fetches.
        active = jobs.Job.objects.filter(type="fetch_feed", feed_id=feed.id, state__in=("queued", "running")).exists()
        if not active:
            jobs.enqueue("fetch_feed", feed_id=feed.id, payload={"forced": False}, interactive=True)
    return {"feed_id": feed.id, "created": created, "payload": tree_response(user)}


def tree_response(user) -> dict:
    from .views import tree_response as build

    return build(user)


async def _subscribe(request, data):
    """POST /api/subscriptions {url, folder_id?, new_folder?} (R7.12)."""
    found = await _find_feed(request.user, require_str(data, "url", ""))
    folder_id = data.get("folder_id")
    if folder_id is not None and type(folder_id) is not int:
        raise ApiError(422, "invalid", "'folder_id' must be a number.")
    new_folder = (data.get("new_folder") or "").strip() if isinstance(data.get("new_folder"), str) else ""
    result = await _in_thread(_create_subscription)(request.user, found, folder_id, new_folder)
    bus.publish(request.user.id, "tree", {"version": now()}, origin=client_id(request))
    return {"feed_id": result["feed_id"], "created": result["created"], "tree": result["payload"]}


def _change_address(user, feed_id: int, address: str) -> int:
    """R7.11: replace the address of a (broken) feed, keeping its stories."""
    with transaction.atomic():
        sub = tree.live_subs(user).filter(feed_id=feed_id).first()
        if sub is None:
            raise ApiError(404, "not_found", "Feed not found.")
        other = Feed.objects.filter(feed_address=address).exclude(pk=feed_id).first()
        if other is not None:
            if Subscription.objects.filter(user=user, feed=other, deleted_at__isnull=True).exists():
                raise ApiError(409, "duplicate", "You already subscribe to the feed at this address.")
            Subscription.objects.filter(user=user, feed=other).delete()
            Subscription.objects.filter(pk=sub.pk).update(feed=other)
            target = other.id
        else:
            Feed.objects.filter(pk=feed_id).update(
                feed_address=address, etag="", last_modified="", redirects_in_row=0, next_scheduled_update=now()
            )
            target = feed_id
    jobs.request_fetch(target, forced=True)
    jobs.enqueue("fetch_icon", feed_id=target)
    return target


def _uses_proxy(user, feed_id: int) -> bool:
    return tree.live_subs(user).filter(feed_id=feed_id, feed__use_proxy=True).exists()


async def _address(request, data, feed_id):
    # A feed that is fetched through the proxy is also looked up at its new address through it.
    proxy = await _in_thread(_uses_proxy)(request.user, feed_id)
    found = await _find_feed(request.user, require_str(data, "url", ""), proxy)
    target = await _in_thread(_change_address)(request.user, feed_id, found["feed_address"])
    bus.publish(request.user.id, "tree", {"version": now()}, origin=client_id(request))
    return {"feed_id": target, "feed_address": found["feed_address"]}


def _story_text_state(user, story_id: int):
    story = Story.objects.filter(pk=story_id).values("feed_id", "permalink", "original_text").first()
    if story is None or not tree.live_subs(user).filter(feed_id=story["feed_id"]).exists():
        return None
    return story


def _saved_text_state(user, saved_id: int):
    return SavedStory.objects.filter(user=user, pk=saved_id).values("permalink", "original_text").first()


async def _text(request, loader, key: str, item_id: int) -> dict:
    """R13.6, R13.9: answer from the cache when possible; otherwise queue a job and wait."""
    force = request.GET.get("force") == "1"
    state = await _in_thread(loader)(request.user, item_id)
    if state is None:
        raise ApiError(404, "not_found", "Story not found.")
    if state["original_text"] and not force:
        return {"text": state["original_text"], "url": state["permalink"], "failed": False, "reason": ""}
    slots = _text_slots.setdefault(request.user.id, asyncio.Semaphore(2))  # R13.8
    async with slots:
        result = await jobs.run_and_wait("extract_text", user_id=request.user.id, payload={key: item_id, "force": force})
    if result.get("failed"):
        return {"text": None, "url": state["permalink"], "failed": True, "reason": result.get("reason") or "failed"}
    state = await _in_thread(loader)(request.user, item_id)
    text = state["original_text"] if state else None
    return {"text": text, "url": result.get("url") or "", "failed": not text, "reason": "" if text else "failed"}


async def _story_text(request, data, story_id):
    return await _text(request, _story_text_state, "story_id", story_id)


async def _saved_text(request, data, saved_id):
    return await _text(request, _saved_text_state, "saved_id", saved_id)


def _subscribes(user, feed_id: int) -> bool:
    return tree.live_subs(user).filter(feed_id=feed_id).exists()


def _icon_link(data) -> str | None:
    """The optional `url` of a fetch_icon request: None when absent, "" to look the icon up again."""
    if "url" not in data:
        return None
    url = require_str(data, "url", "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or len(url) > 1000:
        raise ApiError(422, "invalid_url", "The icon address must be an http or https address.")
    return url


def _set_icon_link(feed_id: int, url: str) -> None:
    with transaction.atomic():
        Feed.objects.filter(pk=feed_id).update(icon_link=url)


async def _fetch_icon(request, data, feed_id):
    """POST /api/feeds/<id>/icon/fetch {url?}: look for the feed's icon again now, retrying through the
    proxy when one is configured (R8.1). A `url` is kept as the feed's icon address, which every later
    search fetches instead of looking the icon up; an empty one returns to looking it up. The address
    belongs to the feed, so it applies to all its subscribers. Answers {found, icon_url, favicon_color,
    tried, out_of_time}, or {pending: true} when the search outlasts the wait; other tabs learn of a
    found icon from the feed:icon event."""
    link = _icon_link(data)
    if not await _in_thread(_subscribes)(request.user, feed_id):
        raise ApiError(404, "not_found", "Feed not found.")
    if link is not None:
        await _in_thread(_set_icon_link)(feed_id, link)
    try:
        return await jobs.run_and_wait("fetch_icon", feed_id=feed_id, user_id=request.user.id, payload={"force": True})
    except ApiError as e:
        if e.code != "worker_timeout":
            raise
        return {"pending": True}


discover_view = endpoint(POST=_discover)
fetch_icon_view = endpoint(POST=_fetch_icon)
subscriptions_view = endpoint(POST=_subscribe)
address_view = endpoint(POST=_address)
story_text_view = endpoint(GET=_story_text)
saved_text_view = endpoint(GET=_saved_text)
