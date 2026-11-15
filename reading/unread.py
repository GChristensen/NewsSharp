"""Unread state and counting (reading/unread.py), item 6. Counts are computed by query (R6.3)."""
from django.core import signing
from django.db import connection, transaction
from django.db.models import Max

from feeds.models import Story
from feeds.util import chunks, now

from .models import ReadStory, Subscription

MARK_READ_UNDO_SALT = "newssharp.mark-read-undo"
MARK_READ_UNDO_SECONDS = 120  # the toast shows for 10 s; the margin covers a slow request


class TooOld(Exception):
    def __init__(self, days: int):
        self.days = days


def unread_cutoff(user, ts: int | None = None) -> int:
    return (ts or now()) - user.days_of_unread * 86400


def unread_counts(user, feed_ids=None) -> dict[int, int]:
    """Unread count per subscribed feed (R6.1, R6.3)."""
    sql = (
        "SELECT sub.feed_id, COUNT(s.id) FROM reading_subscription sub "
        "LEFT JOIN feeds_story s ON s.feed_id = sub.feed_id AND s.story_date > MAX(sub.mark_read_date, %s) "
        "AND NOT EXISTS (SELECT 1 FROM reading_readstory r WHERE r.user_id = %s AND r.story_id = s.id) "
        "WHERE sub.user_id = %s AND sub.deleted_at IS NULL"
    )
    params = [unread_cutoff(user), user.id, user.id]
    if feed_ids is not None:
        ids = [int(i) for i in feed_ids]
        if not ids:
            return {}
        sql += f" AND sub.feed_id IN ({','.join(map(str, ids))})"
    with connection.cursor() as cur:
        cur.execute(sql + " GROUP BY sub.feed_id", params)
        return {feed_id: count for feed_id, count in cur.fetchall()}


def _subscribed_feed_ids(user) -> set[int]:
    return set(Subscription.objects.filter(user=user, deleted_at__isnull=True).values_list("feed_id", flat=True))


def mark_stories_read(user, story_ids: list[int]) -> tuple[list[int], set[int]]:
    """Insert read rows (idempotent, R6.7). Returns the ids marked and the feeds affected."""
    subscribed = _subscribed_feed_ids(user)
    ts = now()
    rows, feeds = [], set()
    for chunk in chunks(list(dict.fromkeys(story_ids)), 500):
        for story_id, feed_id, date in Story.objects.filter(id__in=chunk).values_list("id", "feed_id", "story_date"):
            if feed_id in subscribed:
                rows.append(ReadStory(user=user, story_id=story_id, feed_id=feed_id, story_date=date, read_at=ts))
                feeds.add(feed_id)
    if rows:
        ReadStory.objects.bulk_create(rows, ignore_conflicts=True)
    return [r.story_id for r in rows], feeds


def mark_feeds_read(user, subs: list[Subscription], cutoff: int | None = None, direction: str | None = None) -> dict:
    """R6.5 for a list of subscriptions. Returns {feed_id: mark_read_date} of the dates that moved
    and the story ids marked individually."""
    ts = now()
    moved, marked_ids = {}, []
    with transaction.atomic():
        for sub in subs:
            if cutoff is not None and direction == "newer":
                ids = list(_unread_ids(user, sub, ts, min_date=cutoff))
                ReadStory.objects.bulk_create(
                    [ReadStory(user=user, story_id=i, feed_id=sub.feed_id, story_date=d, read_at=ts) for i, d in ids],
                    ignore_conflicts=True,
                )
                marked_ids.extend(i for i, _ in ids)
                if ids:
                    moved.setdefault(sub.feed_id, None)
                continue
            if cutoff is not None:
                target = cutoff + 1
            else:
                newest = Story.objects.filter(feed_id=sub.feed_id).aggregate(m=Max("story_date"))["m"]
                target = newest + 1 if newest else ts
            if target > sub.mark_read_date:
                Subscription.objects.filter(pk=sub.pk).update(mark_read_date=target)
                moved[sub.feed_id] = target
    return {"moved": moved, "story_ids": marked_ids}


def mark_read_undo_token(user, subs: list[Subscription], moved: dict) -> str | None:
    """A signed token that restores the read dates a whole-feed or whole-folder mark-read moved.
    `subs` hold the dates from before the mark. Only moved dates are recorded; None when nothing moved."""
    dates = [[s.feed_id, s.mark_read_date, moved[s.feed_id]] for s in subs if moved.get(s.feed_id) is not None]
    if not dates:
        return None
    return signing.dumps({"u": user.id, "dates": dates}, salt=MARK_READ_UNDO_SALT)


def undo_mark_read(user, token: str) -> tuple[list[int], list[int]] | None:
    """Put back the read dates of a mark-read token. A feed whose date has moved since (a story marked
    unread, another mark-read) keeps its current state. Returns the restored feed ids and the ids of the
    stories that are unread again, or None when the token is invalid, expired or restores nothing."""
    try:
        data = signing.loads(token, salt=MARK_READ_UNDO_SALT, max_age=MARK_READ_UNDO_SECONDS)
    except signing.BadSignature:
        return None
    if data.get("u") != user.id:
        return None
    cutoff = unread_cutoff(user)
    feed_ids, story_ids = [], []
    with transaction.atomic():
        for feed_id, old, new in data["dates"]:
            restored = Subscription.objects.filter(
                user=user, feed_id=feed_id, deleted_at__isnull=True, mark_read_date=new
            ).update(mark_read_date=old)
            if not restored:
                continue
            feed_ids.append(feed_id)
            read = ReadStory.objects.filter(user=user, feed_id=feed_id).values("story_id")
            story_ids.extend(
                Story.objects.filter(feed_id=feed_id, story_date__gt=max(old, cutoff), story_date__lte=new)
                .exclude(pk__in=read)
                .values_list("id", flat=True)
            )
    return (feed_ids, story_ids) if feed_ids else None


def mark_older_than_days(user, subs: list[Subscription], days: int) -> dict:
    target = now() - days * 86400
    moved = {}
    with transaction.atomic():
        for sub in subs:
            if target > sub.mark_read_date:
                Subscription.objects.filter(pk=sub.pk).update(mark_read_date=target)
                moved[sub.feed_id] = target
    return {"moved": moved, "story_ids": []}


def _unread_ids(user, sub: Subscription, ts: int, min_date: int | None = None):
    cutoff = max(sub.mark_read_date, unread_cutoff(user, ts))
    stories = Story.objects.filter(feed_id=sub.feed_id, story_date__gt=cutoff)
    if min_date is not None:
        stories = stories.filter(story_date__gte=min_date)
    read = set(ReadStory.objects.filter(user=user, feed_id=sub.feed_id).values_list("story_id", flat=True))
    return [(i, d) for i, d in stories.values_list("id", "story_date") if i not in read]


def mark_unread(user, story_id: int) -> int | None:
    """R6.6. Returns the feed id, or None when the story is not the user's. Raises TooOld."""
    story = Story.objects.filter(pk=story_id).values("id", "feed_id", "story_date").first()
    if story is None:
        return None
    sub = Subscription.objects.filter(user=user, feed_id=story["feed_id"], deleted_at__isnull=True).first()
    if sub is None:
        return None
    ts = now()
    if story["story_date"] <= unread_cutoff(user, ts):
        raise TooOld(user.days_of_unread)
    with transaction.atomic():
        ReadStory.objects.filter(user=user, story_id=story_id).delete()
        if story["story_date"] <= sub.mark_read_date:
            old, new = sub.mark_read_date, story["story_date"] - 60
            others = Story.objects.filter(
                feed_id=sub.feed_id, story_date__gt=max(new, unread_cutoff(user, ts)), story_date__lte=old
            ).exclude(pk=story_id)
            ReadStory.objects.bulk_create(
                [
                    ReadStory(user=user, story_id=i, feed_id=sub.feed_id, story_date=d, read_at=ts)
                    for i, d in others.values_list("id", "story_date")
                ],
                ignore_conflicts=True,
            )
            Subscription.objects.filter(pk=sub.pk).update(mark_read_date=new)
    return story["feed_id"]
