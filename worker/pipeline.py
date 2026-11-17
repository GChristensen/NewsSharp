"""The story pipeline (worker/pipeline.py): R2.23-R2.36, statistics (item 18), pruning (R1.6) and
the recording of fetch outcomes. process_feed runs in a pool child; record_failure and
record_not_modified are short database writes the coordinator runs itself."""
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Max, Q

from feeds import store
from feeds.models import Feed, FeedFetchLog, FeedStoryCount, PushSubscription, Story
from feeds.parsing import build_stories, link_header_rels, looks_like_html, parse_feed
from feeds.sanitize import extract_image_urls, sanitize
from feeds.util import InvalidUrl, chunks, guid_hash, normalize_url, now
from reading.models import ReadStory, Subscription
from worker import scheduling
from worker.models import WorkerEvent

log = logging.getLogger("worker.pipeline")

SIMILARITY_SECONDS = 5
SIMILARITY_CHARS = 20000
MAX_UPDATES_PER_FETCH = 3
BATCH = 100
YOUTUBE_HUB = "https://pubsubhubbub.appspot.com/subscribe"


def _ratio_at_least(a: str, b: str, threshold: float) -> bool:
    matcher = SequenceMatcher(None, a, b)
    return (
        matcher.real_quick_ratio() >= threshold
        and matcher.quick_ratio() >= threshold
        and matcher.ratio() >= threshold
    )


def _find_similar(entry: dict, candidates: list, deadline: float):
    """R2.31 case 2. Gives up for this entry at the deadline (R2.36)."""
    content = entry["content"]
    for existing in candidates:
        if time.monotonic() > deadline:
            return None
        if abs(existing.story_date - entry["story_date"]) >= 2 * 86400:
            continue
        if not _ratio_at_least(existing.title or "", entry["title"], 0.75):
            continue
        identical = existing.title == entry["title"] and existing.permalink == entry["permalink"]
        if len(content) <= (20 if identical else 1000):
            continue
        if _ratio_at_least(existing.content[:SIMILARITY_CHARS], content[:SIMILARITY_CHARS], 0.98 + 1e-9):
            return existing
    return None


def _upsert_counters(feed_id: int, dates: list[int]) -> None:
    """R18.1: month, weekday and hour counters from story_date in UTC."""
    counts: Counter = Counter()
    for ts in dates:
        moment = datetime.fromtimestamp(ts, timezone.utc)
        counts[("month", moment.strftime("%Y-%m"))] += 1
        counts[("weekday", str(moment.weekday()))] += 1
        counts[("hour", str(moment.hour))] += 1
    with connection.cursor() as cur:
        for (period_type, key), n in counts.items():
            cur.execute(
                "INSERT INTO feeds_feedstorycount(feed_id, period_type, period_key, count) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT(feed_id, period_type, period_key) DO UPDATE SET count = count + excluded.count",
                [feed_id, period_type, key, n],
            )


def store_stories(feed: Feed, parsed: dict, ts: int) -> dict:
    """Match, insert and update the stories of one parse result. Computes first and writes
    afterwards in transactions of at most 100 stories (R0.18)."""
    base = feed.feed_link or parsed.get("link") or feed.feed_address
    prepared, seen = [], set()
    for entry in sorted(build_stories(parsed, ts), key=lambda s: s["story_date"]):
        entry["guid_hash"] = guid_hash(entry["guid"])
        if entry["guid_hash"] in seen:
            continue
        seen.add(entry["guid_hash"])
        entry["content"] = sanitize(entry["content"], entry["permalink"] or base)
        entry["image_urls"] = extract_image_urls(entry["content"])
        prepared.append(entry)

    by_hash = {}
    for chunk in chunks([e["guid_hash"] for e in prepared], 400):
        for story in Story.objects.filter(feed_id=feed.id, guid_hash__in=chunk):
            by_hash[story.guid_hash] = story
    # A stored story stands for one entry of this fetch: the entry with its guid, or else the first
    # similar one. Without this, two entries could take turns rewriting one story's guid.
    claimed = {story.id for story in by_hash.values()}
    candidates = None
    operations, same, updates = [], 0, 0
    for entry in prepared:
        existing = by_hash.get(entry["guid_hash"])
        if existing is None:
            if candidates is None:
                recent = Story.objects.filter(feed_id=feed.id, story_date__gt=ts - 30 * 86400).order_by("-story_date")
                candidates = list(recent[:300])
            unclaimed = [story for story in candidates if story.id not in claimed]
            existing = _find_similar(entry, unclaimed, time.monotonic() + SIMILARITY_SECONDS)
            if existing is not None:
                claimed.add(existing.id)
        if existing is None:
            operations.append(("insert", None, entry))
        elif existing.content != entry["content"] or existing.permalink != entry["permalink"]:
            if updates < MAX_UPDATES_PER_FETCH:
                updates += 1
                operations.append(("update", existing, entry))
            else:
                same += 1
        else:
            same += 1

    new, updated, newest = 0, 0, 0
    for chunk in chunks(operations, BATCH):
        with transaction.atomic():
            inserted_dates = []
            for kind, existing, e in chunk:
                if kind == "insert":
                    try:
                        with transaction.atomic():
                            story = Story.objects.create(
                                feed_id=feed.id, guid=e["guid"], guid_hash=e["guid_hash"], title=e["title"],
                                author=e["author"], permalink=e["permalink"], content=e["content"],
                                tags=e["tags"], image_urls=e["image_urls"], story_date=e["story_date"],
                                fetched_at=ts, updated_at=ts,
                            )  # fmt: skip
                    except IntegrityError:
                        continue
                    store.index_story(story.id, e["title"], e["content"], e["author"], e["tags"])
                    inserted_dates.append(e["story_date"])
                else:
                    # R2.32: story_date never changes; the story id stays, so read state is kept.
                    Story.objects.filter(pk=existing.pk).update(
                        guid=e["guid"], guid_hash=e["guid_hash"], title=e["title"], author=e["author"],
                        permalink=e["permalink"], content=e["content"], tags=e["tags"],
                        image_urls=e["image_urls"], updated_at=ts, original_text=None, original_text_at=None,
                    )  # fmt: skip
                    store.index_story(existing.pk, e["title"], e["content"], e["author"], e["tags"])
                    updated += 1
            if inserted_dates:
                _upsert_counters(feed.id, inserted_dates)
                new += len(inserted_dates)
                newest = max(newest, max(inserted_dates))
                WorkerEvent.objects.create(
                    at=ts, type="new_stories", feed_id=feed.id,
                    payload={"count": len(inserted_dates), "newest_date": max(inserted_dates)},
                )  # fmt: skip
    return {"new": new, "updated": updated, "same": same}


def prune(feed_id: int, ts: int) -> int:
    """R1.6: keep the newest STORIES_PER_FEED stories; never prune a story unread for a subscriber.
    Also drops read rows made redundant by the cutoffs (item 1 invariants)."""
    overflow = list(
        Story.objects.filter(feed_id=feed_id)
        .order_by("-story_date", "-id")
        .values_list("id", "story_date")[settings.STORIES_PER_FEED :]
    )
    subs = list(Subscription.objects.filter(feed_id=feed_id, deleted_at__isnull=True).select_related("user"))
    protected: set[int] = set()
    with transaction.atomic():
        for sub in subs:
            cutoff = max(sub.mark_read_date, ts - sub.user.days_of_unread * 86400)
            unread = [story_id for story_id, date in overflow if date > cutoff]
            if unread:
                read = set()
                for chunk in chunks(unread, 500):
                    read.update(
                        ReadStory.objects.filter(user_id=sub.user_id, story_id__in=chunk).values_list("story_id", flat=True)
                    )
                protected.update(set(unread) - read)
            ReadStory.objects.filter(user_id=sub.user_id, feed_id=feed_id, story_date__lte=cutoff).delete()
        doomed = [story_id for story_id, _ in overflow if story_id not in protected]
        if doomed:
            store.delete_stories(doomed)
    return len(doomed)


def _stats_fields(feed_id: int, ts: int) -> dict:
    months = list(FeedStoryCount.objects.filter(feed_id=feed_id, period_type="month", count__gt=0).values_list("count", flat=True))
    newest = Story.objects.filter(feed_id=feed_id).aggregate(m=Max("story_date"))["m"]
    return {
        "stories_last_month": Story.objects.filter(feed_id=feed_id, story_date__gt=ts - scheduling.MONTH).count(),
        "last_story_date": newest or ts,
        "average_stories_per_month": round(sum(months) / len(months)) if months else 0,
    }


def _push_active(feed_id: int) -> bool:
    """A verified subscription that has delivered before and whose lease has not run out."""
    return (
        PushSubscription.objects.filter(feed_id=feed_id, verified=True, removing=False, last_push_at__gt=0)
        .filter(Q(lease_expires=0) | Q(lease_expires__gt=now()))
        .exists()
    )


REDISCOVER_AFTER_552 = 3  # consecutive "Non-xml feed" fetches before a known-good feed is rediscovered


def _repeated_552(feed_id: int) -> bool:
    codes = list(
        FeedFetchLog.objects.filter(feed_id=feed_id, type="feed")
        .order_by("-id")
        .values_list("status_code", flat=True)[:REDISCOVER_AFTER_552]
    )
    return len(codes) == REDISCOVER_AFTER_552 and all(code == 552 for code in codes)


def _schedule_fields(feed_id: int, stats: dict, errors: int, ts: int, hint: int | None = None) -> dict:
    minutes = scheduling.interval_minutes(
        stats["stories_last_month"], stats["last_story_date"], errors, _push_active(feed_id), ts, hint
    )
    return {"min_to_decay": minutes, "next_scheduled_update": scheduling.next_update(minutes, ts)}


def _fetched_event(feed_id: int, ts: int) -> None:
    feed = (
        Feed.objects.filter(pk=feed_id)
        .values("fetched_once", "has_exception", "exception_code", "errors_since_good", "last_update", "title", "favicon_color")
        .first()
    )
    if feed:
        WorkerEvent.objects.create(at=ts, type="feed_fetched", feed_id=feed_id, payload=feed)


def record_failure(feed_id: int, code: int, message: str, duration_ms: int = 0, headers: dict | None = None) -> dict:
    """R2.9: a failed fetch. Returns the outcome record."""
    ts = now()
    with transaction.atomic():
        feed = Feed.objects.filter(pk=feed_id).first()
        if feed is None:
            return {"gone": True}
        errors = feed.errors_since_good + 1
        stats = {"stories_last_month": feed.stories_last_month, "last_story_date": feed.last_story_date}
        fields = _schedule_fields(feed_id, stats, errors, ts, scheduling.server_hint_seconds(headers or {}, ts))
        Feed.objects.filter(pk=feed_id).update(
            errors_since_good=errors, has_exception=True, exception_code=code, last_update=ts,
            last_load_ms=duration_ms, **fields,
        )  # fmt: skip
        store.log_fetch(feed_id, "feed", code, message, duration_ms)
        _fetched_event(feed_id, ts)
        # R2.21. A feed that worked before is rediscovered only when it keeps answering with a page:
        # one error page must not move its subscribers to whatever feed the site's home page links.
        rediscover = not feed.known_good or (code == 552 and _repeated_552(feed_id))
    return {"error": code, "message": message, "rediscover": rediscover}


def record_not_modified(feed_id: int, duration_ms: int, headers: dict, redirected: bool = False) -> dict:
    """R2.6. A 304 reached through a permanent redirect keeps the redirect count of R2.8 as it is;
    one without a redirect ends the series."""
    ts = now()
    with transaction.atomic():
        feed = Feed.objects.filter(pk=feed_id).first()
        if feed is None:
            return {"gone": True}
        stats = _stats_fields(feed_id, ts)
        fields = _schedule_fields(feed_id, stats, 0, ts, scheduling.server_hint_seconds(headers, ts))
        if not redirected:
            fields["redirects_in_row"] = 0
        Feed.objects.filter(pk=feed_id).update(
            errors_since_good=0, has_exception=False, exception_code=0, last_update=ts,
            last_load_ms=duration_ms, stories_last_month=stats["stories_last_month"], **fields,
        )  # fmt: skip
        store.log_fetch(feed_id, "feed", 304, "Not modified", duration_ms)
        _fetched_event(feed_id, ts)
    return {"status": 304, "new": 0, "updated": 0}


def _merge_into(feed: Feed, target: Feed, ts: int) -> None:
    """R1.5: a permanent redirect onto an existing feed moves the subscriptions and deletes the
    redirecting feed."""
    with transaction.atomic():
        for sub in Subscription.objects.filter(feed_id=feed.id):
            if Subscription.objects.filter(user_id=sub.user_id, feed_id=target.id).exists():
                sub.delete()
            else:
                Subscription.objects.filter(pk=sub.pk).update(feed_id=target.id)
        WorkerEvent.objects.create(at=ts, type="tree_changed", feed_id=target.id, payload={})
    store.purge_feed(feed.id)


def _hub_and_topic(feed: Feed, parsed: dict, headers: dict) -> tuple[str | None, str | None]:
    """R4.1."""
    rels = link_header_rels(headers.get("link", ""))
    hub = (parsed.get("hubs") or [None])[0] or rels.get("hub")
    if not hub and "youtube.com/feeds/videos.xml" in feed.feed_address:
        hub = YOUTUBE_HUB
    topic = parsed.get("self") or rels.get("self") or feed.feed_address
    return hub, topic


def process_feed(feed_id: int, fetch: dict) -> dict:
    """The process_feed pool task (R0.24). `fetch` is either a download result
    {kind: "fetch", body, content_type, headers, permanent_url, download_ms} or a pushed body
    {kind: "push", body, content_type}."""
    started = time.monotonic()
    ts = now()
    feed = Feed.objects.filter(pk=feed_id).first()
    if feed is None:
        return {"gone": True}
    body, content_type = fetch["body"], fetch.get("content_type") or ""
    headers = fetch.get("headers") or {}
    is_push = fetch["kind"] == "push"
    download_ms = fetch.get("download_ms", 0)

    def elapsed() -> int:
        return download_ms + int((time.monotonic() - started) * 1000)

    parsed = parse_feed(body, content_type)
    has_entries = bool(parsed and parsed["entries"])

    if is_push:
        if not has_entries:
            with transaction.atomic():
                store.log_fetch(feed_id, "push", 200, "Notification without a usable body, fetching", 0)
                PushSubscription.objects.filter(feed_id=feed_id).update(last_push_at=ts)
            return {"fetch_needed": True}
        counts = store_stories(feed, parsed, ts)
        outcome = {"push": True, **counts}
        with transaction.atomic():
            PushSubscription.objects.filter(feed_id=feed_id).update(last_push_at=ts)
            stats = _stats_fields(feed_id, ts)
            # A usable pushed body shows the feed is alive: clear a failed fetch's error state and tell
            # the clients, as a successful fetch does.
            Feed.objects.filter(pk=feed_id).update(
                errors_since_good=0, has_exception=False, exception_code=0, last_update=ts,
                **stats, **_schedule_fields(feed_id, stats, 0, ts),
            )  # fmt: skip
            store.log_fetch(feed_id, "push", 200, f"OK, {counts['new']} new, {counts['updated']} updated", elapsed())
            _fetched_event(feed_id, ts)
        push = PushSubscription.objects.filter(feed_id=feed_id).first()
        if push and parsed.get("hubs"):
            topic = parsed.get("self") or push.topic
            if push.hub not in parsed["hubs"] or topic != push.topic:
                outcome["hub"], outcome["topic"], outcome["resubscribe"] = parsed["hubs"][0], topic, True
        if counts["new"]:
            prune(feed_id, ts)
        return outcome

    if parsed is None:
        return record_failure(feed_id, 551, "Broken feed", elapsed())
    if not has_entries and not parsed["version"]:
        if looks_like_html(body, content_type):
            return record_failure(feed_id, 552, "Non-xml feed", elapsed())
        return record_failure(feed_id, 553, "Not an RSS feed", elapsed())

    # R2.8: permanent redirects change the address after 3 in a row, or at once for a new feed.
    redirect_fields = {"redirects_in_row": 0}
    permanent = fetch.get("permanent_url")
    if permanent:
        try:
            permanent = normalize_url(permanent)
        except InvalidUrl:
            permanent = None
    if permanent and permanent != feed.feed_address:
        store.log_fetch(feed_id, "feed", 301, "HTTP Redirect", download_ms)
        in_row = feed.redirects_in_row + 1
        if in_row >= 3 or not feed.fetched_once:
            target = Feed.objects.filter(feed_address=permanent).exclude(pk=feed_id).first()
            if target is not None:
                _merge_into(feed, target, ts)
                return {"merged_into": target.id}
            redirect_fields = {"redirects_in_row": 0, "feed_address": permanent}
            feed.feed_address = permanent
        else:
            redirect_fields = {"redirects_in_row": in_row}

    counts = store_stories(feed, parsed, ts)

    first_fetch = not feed.fetched_once
    etag, last_modified = headers.get("etag") or "", headers.get("last-modified") or ""
    fields = {
        # R2.6: sent back as request headers, which must be ASCII.
        "etag": etag[:255] if etag.isascii() else "",
        "last_modified": last_modified[:64] if last_modified.isascii() else "",
        "fetched_once": True, "known_good": True, "errors_since_good": 0, "has_exception": False,
        "exception_code": 0, "last_update": ts, **redirect_fields,
    }  # fmt: skip
    title = sanitize_title(parsed.get("title"))
    if title:
        fields["title"] = title
    if parsed.get("tagline"):
        fields["tagline"] = sanitize_title(parsed["tagline"], 2000)
    link_changed = False
    link = (parsed.get("link") or "").strip()
    if link.startswith(("http://", "https://")) and not feed.feed_link_locked and link[:1000] != feed.feed_link:
        fields["feed_link"] = link[:1000]
        link_changed = True
    if parsed.get("image"):
        fields["image_url"] = parsed["image"][:1000]

    with transaction.atomic():
        stats = _stats_fields(feed_id, ts)
        hint = scheduling.server_hint_seconds(headers, ts)
        Feed.objects.filter(pk=feed_id).update(
            **fields, **stats, **_schedule_fields(feed_id, stats, 0, ts, hint), last_load_ms=elapsed()
        )
        if first_fetch and title:
            # R15.3: an imported title equal to the feed's own title is not a custom title.
            Subscription.objects.filter(feed_id=feed_id, user_title=title).update(user_title=None)
        store.log_fetch(feed_id, "feed", 200, "OK", elapsed())
        _fetched_event(feed_id, ts)
    if counts["new"]:
        prune(feed_id, ts)

    hub, topic = _hub_and_topic(feed, parsed, headers)
    has_icon_state = feed.favicon_not_found or feed.favicon_checked_at > 0
    return {
        "status": 200, **counts, "hub": hub, "topic": topic,
        "need_icon": first_fetch or link_changed or not has_icon_state,
        "process_ms": int((time.monotonic() - started) * 1000),
    }  # fmt: skip


def sanitize_title(value: str | None, limit: int = 255) -> str:
    from feeds.util import strip_tags

    return strip_tags(value or "")[:limit]


def rebuild_counters(feed_id: int) -> int:
    """R18.7: recompute the counters of a feed from its stored stories."""
    dates = list(Story.objects.filter(feed_id=feed_id).values_list("story_date", flat=True))
    with transaction.atomic():
        FeedStoryCount.objects.filter(feed_id=feed_id).delete()
        _upsert_counters(feed_id, dates)
        Feed.objects.filter(pk=feed_id).update(**_stats_fields(feed_id, now()))
    return len(dates)
