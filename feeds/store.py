"""Database helpers shared by both processes (feeds/store.py): full-text index rows (R12.8), fetch
log retention (R18.3), story and feed deletion (R1.2, R1.6)."""
from django.db import connection, transaction

from .models import Feed, FeedFetchLog
from .util import chunks, now, plain_text

BODY_LIMIT = 100000


def index_story(story_id: int, title: str, content: str, author: str, tags: list[str]) -> None:
    with connection.cursor() as cur:
        cur.execute("DELETE FROM stories_fts WHERE rowid = %s", [story_id])
        cur.execute(
            "INSERT INTO stories_fts(rowid, title, body, author, tags) VALUES (%s, %s, %s, %s, %s)",
            [story_id, title or "", plain_text(content)[:BODY_LIMIT], author or "", " ".join(tags or [])],
        )


def index_saved(saved_id: int, title: str, content: str, author: str, tags: list[str], user_tags: list[str]) -> None:
    with connection.cursor() as cur:
        cur.execute("DELETE FROM saved_fts WHERE rowid = %s", [saved_id])
        cur.execute(
            "INSERT INTO saved_fts(rowid, title, body, author, tags, user_tags) VALUES (%s, %s, %s, %s, %s, %s)",
            [saved_id, title or "", plain_text(content)[:BODY_LIMIT], author or "", " ".join(tags or []),
             " ".join(user_tags or [])],
        )  # fmt: skip


def unindex_saved(saved_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute("DELETE FROM saved_fts WHERE rowid = %s", [saved_id])


def delete_stories(story_ids: list[int]) -> None:
    """Delete stories with their index rows and read rows, in the caller's transaction."""
    with connection.cursor() as cur:
        for chunk in chunks(story_ids, 500):
            marks = ",".join(str(int(i)) for i in chunk)
            cur.execute(f"DELETE FROM stories_fts WHERE rowid IN ({marks})")
            cur.execute(f"DELETE FROM reading_readstory WHERE story_id IN ({marks})")
            cur.execute(f"DELETE FROM feeds_story WHERE id IN ({marks})")


def log_fetch(feed_id: int, log_type: str, code: int, message: str, duration_ms: int = 0) -> None:
    """Add a fetch log entry and apply the retention of R18.3."""
    FeedFetchLog.objects.create(
        feed_id=feed_id, type=log_type, at=now(), status_code=code, message=message[:255], duration_ms=duration_ms
    )
    entries = list(
        FeedFetchLog.objects.filter(feed_id=feed_id, type=log_type).order_by("-id").values_list("id", "status_code")[:60]
    )
    newest = entries[:25]
    keep = 25 if any(code not in (200, 304) for _, code in newest) else 5
    stale = [entry_id for entry_id, _ in entries[keep:]]
    if stale:
        FeedFetchLog.objects.filter(id__in=stale).delete()


def purge_feed(feed_id: int) -> None:
    """Remove a feed with its stories, icon, logs, counters and push subscription (R1.2)."""
    with transaction.atomic(), connection.cursor() as cur:
        params = [feed_id]
        cur.execute("DELETE FROM stories_fts WHERE rowid IN (SELECT id FROM feeds_story WHERE feed_id = %s)", params)
        cur.execute("DELETE FROM reading_readstory WHERE feed_id = %s", params)
        cur.execute("DELETE FROM feeds_story WHERE feed_id = %s", params)
        cur.execute("DELETE FROM feeds_feedicon WHERE feed_id = %s", params)
        cur.execute("DELETE FROM feeds_feedfetchlog WHERE feed_id = %s", params)
        cur.execute("DELETE FROM feeds_feedstorycount WHERE feed_id = %s", params)
        cur.execute("DELETE FROM feeds_pushsubscription WHERE feed_id = %s AND removing = 0", params)
        cur.execute("UPDATE feeds_pushsubscription SET feed_id = NULL WHERE feed_id = %s", params)
        cur.execute("DELETE FROM reading_viewsetting WHERE target_type = 'feed' AND target_id = %s", params)
        cur.execute("DELETE FROM reading_subscription WHERE feed_id = %s", params)
        cur.execute("DELETE FROM worker_job WHERE feed_id = %s AND state IN ('queued', 'done', 'failed')", params)
        cur.execute("DELETE FROM feeds_feed WHERE id = %s", params)


def get_or_create_feed(address: str, title: str = "", link: str = "") -> tuple[Feed, bool]:
    """A feed row by normalized address; new rows are due for an immediate first fetch."""
    ts = now()
    return Feed.objects.get_or_create(
        feed_address=address,
        defaults={"title": title[:255], "feed_link": link[:1000], "created_at": ts, "next_scheduled_update": ts},
    )
