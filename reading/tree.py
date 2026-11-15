"""Feed list, folders and ordering (reading/tree.py), item 7. Positions are 0-based within a
container and shared by feeds and folders."""
from django.core import signing
from django.db import transaction

from feeds.models import FeedFetchLog, FeedIcon
from feeds.util import now

from .models import Folder, Subscription
from .unread import unread_counts

UNDO_SALT = "newssharp.undo"


class TreeError(Exception):
    pass


def live_folders(user):
    return Folder.objects.filter(user=user, deleted_at__isnull=True)


def live_subs(user):
    return Subscription.objects.filter(user=user, deleted_at__isnull=True)


def get_folder(user, folder_id) -> Folder | None:
    if folder_id is None:
        return None
    folder = live_folders(user).filter(pk=folder_id).first()
    if folder is None:
        raise TreeError("folder not found")
    return folder


def container_items(user, parent_id) -> list:
    """Folders and subscriptions of one container in display order."""
    items = list(live_folders(user).filter(parent_id=parent_id)) + list(live_subs(user).filter(folder_id=parent_id))
    return sorted(items, key=lambda item: (item.position, isinstance(item, Subscription), item.id))


def next_position(user, parent_id) -> int:
    items = container_items(user, parent_id)
    return items[-1].position + 1 if items else 0


def _renumber(items: list) -> None:
    for position, item in enumerate(items):
        if item.position != position:
            type(item).objects.filter(pk=item.pk).update(position=position)


def descendant_folder_ids(user, folder_id: int) -> list[int]:
    """The folder and all its subfolders."""
    children: dict[int | None, list[int]] = {}
    for fid, parent_id in live_folders(user).values_list("id", "parent_id"):
        children.setdefault(parent_id, []).append(fid)
    result, stack = [], [folder_id]
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(children.get(current, []))
    return result


def feeds_in_folder(user, folder_id: int) -> list[int]:
    return list(
        live_subs(user).filter(folder_id__in=descendant_folder_ids(user, folder_id)).values_list("feed_id", flat=True)
    )


def create_folder(user, title: str, parent_id=None) -> Folder:
    title = (title or "").strip()[:255]
    if not title:
        raise TreeError("a folder needs a name")
    get_folder(user, parent_id)
    return Folder.objects.create(user=user, parent_id=parent_id, title=title, position=next_position(user, parent_id))


def subscribe(user, feed, folder_id=None, user_title=None, mark_read_date=None) -> tuple[Subscription, bool]:
    """Subscribe a user to a feed at the end of a container (R7.5). An existing subscription is
    left where it is; a soft-deleted one is revived."""
    ts = now()
    with transaction.atomic():
        sub = Subscription.objects.filter(user=user, feed=feed).first()
        if sub is not None and sub.deleted_at is None:
            return sub, False
        if mark_read_date is None:
            mark_read_date = ts - user.days_of_unread * 86400  # R6.4
        position = next_position(user, folder_id)
        if sub is not None:
            sub.folder_id, sub.position, sub.deleted_at, sub.user_title = folder_id, position, None, user_title
            sub.save()
            return sub, True
        sub = Subscription.objects.create(
            user=user, feed=feed, folder_id=folder_id, position=position, user_title=user_title,
            mark_read_date=mark_read_date, created_at=ts,
        )  # fmt: skip
        return sub, True


def move(user, item: str, parent_id, position: int) -> None:
    """R7.7: one move operation; positions of the affected containers are renumbered in one transaction."""
    kind, _, raw_id = item.partition(":")
    try:
        item_id = int(raw_id)
    except ValueError:
        raise TreeError("invalid item") from None
    with transaction.atomic():
        get_folder(user, parent_id)
        if kind == "folder":
            obj = live_folders(user).filter(pk=item_id).first()
            if obj is not None and parent_id is not None and parent_id in descendant_folder_ids(user, item_id):
                raise TreeError("a folder cannot be moved into itself")  # R7.6
            old_parent = obj.parent_id if obj else None
        elif kind == "feed":
            obj = live_subs(user).filter(feed_id=item_id).first()
            old_parent = obj.folder_id if obj else None
        else:
            raise TreeError("invalid item")
        if obj is None:
            raise TreeError("item not found")
        target = [i for i in container_items(user, parent_id) if not (type(i) is type(obj) and i.pk == obj.pk)]
        position = max(0, min(int(position), len(target)))
        target.insert(position, obj)
        if kind == "folder":
            Folder.objects.filter(pk=obj.pk).update(parent_id=parent_id, position=-1)
        else:
            Subscription.objects.filter(pk=obj.pk).update(folder_id=parent_id, position=-1)
        obj.position = -1
        _renumber(target)
        if old_parent != parent_id:
            _renumber(container_items(user, old_parent))


def delete_feed(user, feed_id: int) -> str:
    """R7.10: soft delete; returns an undo token."""
    ts = now()
    updated = live_subs(user).filter(feed_id=feed_id).update(deleted_at=ts)
    if not updated:
        raise TreeError("subscription not found")
    return signing.dumps({"u": user.id, "at": ts, "feeds": [feed_id], "folders": []}, salt=UNDO_SALT)


def delete_folder(user, folder_id: int) -> str:
    ts = now()
    with transaction.atomic():
        get_folder(user, folder_id)
        folder_ids = descendant_folder_ids(user, folder_id)
        feed_ids = list(live_subs(user).filter(folder_id__in=folder_ids).values_list("feed_id", flat=True))
        live_subs(user).filter(folder_id__in=folder_ids).update(deleted_at=ts)
        Folder.objects.filter(user=user, pk__in=folder_ids).update(deleted_at=ts)
    return signing.dumps({"u": user.id, "at": ts, "feeds": feed_ids, "folders": folder_ids}, salt=UNDO_SALT)


def undo(user, token: str) -> bool:
    """Restore what a delete removed, at its former position, if the purge has not run yet."""
    try:
        data = signing.loads(token, salt=UNDO_SALT, max_age=120)
    except signing.BadSignature:
        return False
    if data.get("u") != user.id:
        return False
    with transaction.atomic():
        restored = Folder.objects.filter(user=user, pk__in=data["folders"], deleted_at=data["at"]).update(deleted_at=None)
        restored += Subscription.objects.filter(user=user, feed_id__in=data["feeds"], deleted_at=data["at"]).update(
            deleted_at=None
        )
    return restored > 0


def tree_payload(user) -> dict:
    """The folders and feeds of GET /api/tree, with unread counts."""
    counts = unread_counts(user)
    subs = list(live_subs(user).select_related("feed"))
    feed_ids = [s.feed_id for s in subs]
    icons = dict(FeedIcon.objects.filter(feed_id__in=feed_ids).values_list("feed_id", "updated_at"))
    failing = [s.feed_id for s in subs if s.feed.has_exception]
    messages = {}
    for feed_id in failing:
        entry = FeedFetchLog.objects.filter(feed_id=feed_id, type="feed").order_by("-id").values_list("message", flat=True).first()
        messages[feed_id] = entry or ""
    feeds = []
    for sub in subs:
        feed = sub.feed
        feeds.append(
            {
                "id": feed.id,
                "title": sub.user_title or feed.title or feed.feed_address,
                "feed_title": feed.title,
                "feed_address": feed.feed_address,
                "feed_link": feed.feed_link,
                "folder_id": sub.folder_id,
                "position": sub.position,
                "unread": counts.get(feed.id, 0),
                "favicon_color": feed.favicon_color,
                "has_icon": feed.id in icons,
                "icon_version": icons.get(feed.id, 0),
                "is_push": feed.is_push,
                "fetched_once": feed.fetched_once,
                "has_exception": feed.has_exception,
                "exception_code": feed.exception_code,
                "exception_message": messages.get(feed.id, ""),
                "errors_since_good": feed.errors_since_good,
            }
        )
    folders = list(live_folders(user).values("id", "parent_id", "title", "position", "collapsed"))
    return {"folders": folders, "feeds": feeds}
