"""Story lists, search and saved stories (reading/stories.py): items 10, 12, 17. Keyset paging
with raw SQL (R17.2); results of a search are ordered by date, not relevance (R12.5)."""
import json
import re

from django.db import connection, transaction
from django.db.utils import DatabaseError

from feeds import store
from feeds.models import Feed, Setting, Story
from feeds.sanitize import media_urls
from feeds.util import image_signature, now, plain_text

from . import tree
from .models import SavedStory, Subscription
from .search import build_match, plain_match
from .unread import unread_cutoff

PREVIEW_CHARS = 1500
_image_secret = None


class ScopeError(Exception):
    pass


def image_secret() -> str:
    global _image_secret
    if _image_secret is None:
        _image_secret = Setting.secret("image_secret")
    return _image_secret


def parse_scope(user, scope: str) -> dict:
    """Resolve a scope string (0.3) into {kind, feed_ids | None, tag}. feed_ids None means all."""
    parts = (scope or "").split(":")
    try:
        if parts[0] == "saved":
            if len(parts) == 1:
                return {"kind": "saved", "feed_ids": None, "tag": None}
            if parts[1] == "tag":
                return {"kind": "saved", "feed_ids": None, "tag": ":".join(parts[2:])}
            if parts[1] == "feed":
                return {"kind": "saved", "feed_ids": [int(parts[2])], "tag": None}
            if parts[1] == "folder":
                return {"kind": "saved", "feed_ids": tree.feeds_in_folder(user, int(parts[2])), "tag": None}
        elif parts[0] == "all" and len(parts) == 1:
            return {"kind": "live", "feed_ids": None, "tag": None}
        elif parts[0] == "feed":
            feed_id = int(parts[1])
            if not tree.live_subs(user).filter(feed_id=feed_id).exists():
                raise ScopeError("not subscribed to this feed")
            return {"kind": "live", "feed_ids": [feed_id], "tag": None}
        elif parts[0] == "folder":
            folder_id = int(parts[1])
            if not tree.live_folders(user).filter(pk=folder_id).exists():
                raise ScopeError("folder not found")
            return {"kind": "live", "feed_ids": tree.feeds_in_folder(user, folder_id), "tag": None}
    except (ValueError, IndexError):
        pass
    raise ScopeError("invalid scope")


def _encode_cursor(date: int, row_id: int) -> str:
    return f"{date}.{row_id}"


def _decode_cursor(cursor: str | None) -> tuple[int, int] | None:
    if not cursor:
        return None
    try:
        date, row_id = cursor.split(".")
        return int(date), int(row_id)
    except ValueError:
        return None


def _preview(content: str, terms: list[str]) -> str:
    text = plain_text(content, breaks=True)
    if terms and len(text) > 300:
        lowered = text.lower()
        positions = [p for p in (lowered.find(t.lower()) for t in terms) if p > 200]
        hits_early = any(0 <= lowered.find(t.lower()) <= 200 for t in terms)
        if positions and not hits_early:
            start = max(0, min(positions) - 80)
            return "…" + text[start : start + PREVIEW_CHARS]
    return text[:PREVIEW_CHARS]


def _loads(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return default


def story_json(row: dict, with_content: bool = True, terms: list[str] | None = None) -> dict:
    content = row["content"] or ""
    images = _loads(row["image_urls"], [])
    secret = image_secret()
    urls = list(dict.fromkeys(images + media_urls(content)))
    data = {
        "id": row["id"],
        "saved_id": row.get("saved_id"),
        "feed_id": row["feed_id"],
        "title": row["title"],
        "author": row["author"],
        "permalink": row["permalink"],
        "tags": _loads(row["tags"], []),
        "image_urls": images,
        "image_sigs": {url: image_signature(url, secret) for url in urls},
        "story_date": row["story_date"],
        "read": not row.get("unread", False),
        "saved": row.get("saved_id") is not None,
        "user_tags": _loads(row.get("user_tags"), []),
        "preview": _preview(content, terms or []),
    }
    if "feed_title" in row:
        data["feed_title"] = row["feed_title"]
    if "saved_at" in row:
        data["saved_at"] = row["saved_at"]
    if with_content:
        data["content"] = content
    return data


def _rows(sql: str, params: list) -> list[dict]:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


_LIVE_SELECT = (
    "SELECT s.id, s.feed_id, s.title, s.author, s.permalink, s.content, s.tags, s.image_urls, s.story_date, "
    "(s.story_date > MAX(sub.mark_read_date, %s) AND r.id IS NULL) AS unread, sv.id AS saved_id, sv.user_tags "
    "FROM feeds_story s "
    "JOIN reading_subscription sub ON sub.feed_id = s.feed_id AND sub.user_id = %s AND sub.deleted_at IS NULL "
    "LEFT JOIN reading_readstory r ON r.user_id = %s AND r.story_id = s.id "
    "LEFT JOIN reading_savedstory sv ON sv.user_id = %s AND sv.feed_id = s.feed_id AND sv.guid_hash = s.guid_hash "
)
_SAVED_SELECT = (
    "SELECT s.id AS id, sv.id AS saved_id, sv.feed_id, sv.feed_title, sv.title, sv.author, sv.permalink, "
    "sv.content, sv.tags, sv.image_urls, sv.story_date, sv.saved_at, sv.user_tags, 0 AS unread "
    "FROM reading_savedstory sv "
    "LEFT JOIN feeds_story s ON s.feed_id = sv.feed_id AND s.guid_hash = sv.guid_hash "
)


def _in_list(ids: list[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


def list_stories(user, scope: str, read_filter: str, order: str, query: str, cursor: str | None, limit: int,
                 with_content: bool = True) -> dict:  # fmt: skip
    resolved = parse_scope(user, scope)
    limit = max(1, min(int(limit or 30), 100))
    newest = order != "oldest"
    position = _decode_cursor(cursor)
    match, terms = build_match(query) if query and len(query.strip()) >= 2 else (None, [])
    saved = resolved["kind"] == "saved"
    if resolved["feed_ids"] is not None and not resolved["feed_ids"]:
        return {"stories": [], "next_cursor": None, "terms": terms}

    def run(match_expression):
        where, params = [], []
        if saved:
            sql = _SAVED_SELECT
            where.append("sv.user_id = %s")
            params.append(user.id)
            date_column, id_column, fts = "sv.saved_at", "sv.id", "saved_fts"
            if resolved["feed_ids"] is not None:
                where.append(f"sv.feed_id IN ({_in_list(resolved['feed_ids'])})")
            if resolved["tag"] is not None:
                where.append("EXISTS (SELECT 1 FROM json_each(sv.user_tags) WHERE value = %s)")
                params.append(resolved["tag"])
        else:
            sql = _LIVE_SELECT
            cutoff = unread_cutoff(user)
            params.extend([cutoff, user.id, user.id, user.id])
            date_column, id_column, fts = "s.story_date", "s.id", "stories_fts"
            if resolved["feed_ids"] is not None:
                where.append(f"s.feed_id IN ({_in_list(resolved['feed_ids'])})")
            if read_filter == "unread" and not match_expression:  # R12.5: the filter is ignored while searching
                where.append("s.story_date > MAX(sub.mark_read_date, %s) AND r.id IS NULL")
                params.append(cutoff)
        if match_expression:
            where.append(f"{id_column} IN (SELECT rowid FROM {fts} WHERE {fts} MATCH %s)")
            params.append(match_expression)
        if position:
            where.append(f"({date_column}, {id_column}) {'<' if newest else '>'} (%s, %s)")
            params.extend(position)
        direction = "DESC" if newest else "ASC"
        sql += (" WHERE " + " AND ".join(where) if where else "")
        sql += f" ORDER BY {date_column} {direction}, {id_column} {direction} LIMIT %s"
        params.append(limit + 1)
        return _rows(sql, params)

    try:
        rows = run(match)
    except DatabaseError:
        if not match:
            raise
        match, terms = plain_match(query)  # R12.3: never an error for the user
        rows = run(match) if match else []
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(last["saved_at"], last["saved_id"]) if saved else _encode_cursor(last["story_date"], last["id"])
    return {
        "stories": [story_json(row, with_content, terms) for row in rows],
        "next_cursor": next_cursor,
        "terms": terms,
    }


def get_story(user, story_id: int) -> dict | None:
    cutoff = unread_cutoff(user)
    rows = _rows(_LIVE_SELECT + " WHERE s.id = %s", [cutoff, user.id, user.id, user.id, story_id])
    return story_json(rows[0]) if rows else None


def get_saved(user, saved_id: int) -> dict | None:
    rows = _rows(_SAVED_SELECT + " WHERE sv.user_id = %s AND sv.id = %s", [user.id, saved_id])
    return story_json(rows[0]) if rows else None


# ---------------------------------------------------------------- Saved for Later (R10.10-R10.12)

_TAG_SPLIT = re.compile(r"\s*,\s*")


def clean_user_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = _TAG_SPLIT.split(tags)
    result = []
    for tag in tags or []:
        if isinstance(tag, str):
            tag = " ".join(tag.split())[:100]
            if tag and tag not in result:
                result.append(tag)
    return result[:20]


def save_story(user, story_id: int, user_tags=None) -> SavedStory | None:
    """Copy a story into saved_stories; saving again is a no-op (tags are updated when given)."""
    story = Story.objects.filter(pk=story_id).first()
    if story is None or not Subscription.objects.filter(user=user, feed_id=story.feed_id).exists():
        return None
    with transaction.atomic():
        saved = SavedStory.objects.filter(user=user, feed_id=story.feed_id, guid_hash=story.guid_hash).first()
        if saved is None:
            sub = Subscription.objects.filter(user=user, feed_id=story.feed_id).select_related("feed").first()
            saved = SavedStory.objects.create(
                user=user, feed_id=story.feed_id, feed_title=(sub.user_title or sub.feed.title)[:255],
                guid_hash=story.guid_hash, title=story.title, author=story.author, permalink=story.permalink,
                content=story.content, tags=story.tags, image_urls=story.image_urls, story_date=story.story_date,
                saved_at=now(), user_tags=clean_user_tags(user_tags), original_text=story.original_text,
            )  # fmt: skip
        elif user_tags is not None:
            saved.user_tags = clean_user_tags(user_tags)
            saved.save(update_fields=["user_tags"])
        else:
            return saved
        store.index_saved(saved.id, saved.title, saved.content, saved.author, saved.tags, saved.user_tags)
    return saved


def set_saved_tags(user, saved_id: int, user_tags) -> SavedStory | None:
    with transaction.atomic():
        saved = SavedStory.objects.filter(user=user, pk=saved_id).first()
        if saved is None:
            return None
        saved.user_tags = clean_user_tags(user_tags)
        saved.save(update_fields=["user_tags"])
        store.index_saved(saved.id, saved.title, saved.content, saved.author, saved.tags, saved.user_tags)
    return saved


def unsave(user, saved_id: int) -> SavedStory | None:
    with transaction.atomic():
        saved = SavedStory.objects.filter(user=user, pk=saved_id).first()
        if saved is None:
            return None
        store.unindex_saved(saved.id)
        saved_copy = SavedStory(id=saved.id, feed_id=saved.feed_id, guid_hash=saved.guid_hash)
        saved.delete()
    return saved_copy


def live_story_id(saved: SavedStory) -> int | None:
    if saved.feed_id is None:
        return None
    return Story.objects.filter(feed_id=saved.feed_id, guid_hash=saved.guid_hash).values_list("id", flat=True).first()


def saved_counts(user) -> dict:
    """Totals of saved stories per feed and per tag; titles of feeds no longer subscribed (R7.4)."""
    feeds: dict[int, int] = {}
    tags: dict[str, int] = {}
    titles: dict[int, str] = {}
    total = 0
    for feed_id, feed_title, user_tags in SavedStory.objects.filter(user=user).values_list("feed_id", "feed_title", "user_tags"):
        total += 1
        if feed_id is not None:
            feeds[feed_id] = feeds.get(feed_id, 0) + 1
            titles[feed_id] = feed_title
        for tag in user_tags or []:
            tags[tag] = tags.get(tag, 0) + 1
    subscribed = set(tree.live_subs(user).values_list("feed_id", flat=True))
    orphans = {feed_id: title for feed_id, title in titles.items() if feed_id not in subscribed}
    for feed_id in list(orphans):
        if not orphans[feed_id]:
            orphans[feed_id] = Feed.objects.filter(pk=feed_id).values_list("title", flat=True).first() or "Unknown feed"
    return {"total": total, "feeds": feeds, "tags": tags, "titles": orphans}
