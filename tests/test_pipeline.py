"""Acceptance checks of items 1, 2, 3, 8 and 18 (tests/test_pipeline.py)."""
import io
import time
from datetime import datetime, timezone
from email.utils import formatdate

import pytest
from PIL import Image

from feeds.models import FeedFetchLog, FeedStoryCount, Story
from feeds.sanitize import extract_image_urls, sanitize
from feeds.util import InvalidUrl, guid_hash, normalize_url, now
from reading import unread
from reading.models import ReadStory
from worker import pipeline, scheduling
from worker.models import WorkerEvent
from worker.tasks import dominant_color, parse_page

pytestmark = pytest.mark.django_db


def rss(items: str, extra: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        f"<channel><title>Test feed</title><link>https://example.com/</link>{extra}{items}</channel></rss>"
    ).encode()


def item(guid=None, title="Title", link=None, body="Body text", date=None, extra="") -> str:
    parts = [f"<title>{title}</title>", f"<description><![CDATA[{body}]]></description>", extra]
    if guid:
        parts.append(f"<guid>{guid}</guid>")
    if link:
        parts.append(f"<link>{link}</link>")
    if date:
        parts.append(f"<pubDate>{formatdate(date)}</pubDate>")
    return "<item>" + "".join(parts) + "</item>"


def fetch(feed, body: bytes, **kwargs) -> dict:
    payload = {"kind": "fetch", "body": body, "content_type": "application/rss+xml", "headers": {}, **kwargs}
    return pipeline.process_feed(feed.id, payload)


def test_stories_are_stored_and_refetch_changes_nothing(make_feed):
    feed = make_feed()
    ts = now()
    body = rss(item("a", "First", "https://example.com/a", date=ts - 3600) + item("b", "Second", "https://example.com/b", date=ts - 60))
    assert fetch(feed, body)["new"] == 2
    outcome = fetch(feed, body)
    assert (outcome["new"], outcome["updated"], outcome["same"]) == (0, 0, 2)
    feed.refresh_from_db()
    assert feed.fetched_once and feed.known_good and feed.title == "Test feed"
    assert feed.stories_last_month == 2


def test_same_guid_changed_content_updates_in_place_and_keeps_read_state(make_feed, user, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    date = now() - 3600
    fetch(feed, rss(item("a", "First", "https://example.com/a", "old body", date)))
    story = Story.objects.get(feed=feed)
    unread.mark_stories_read(user, [story.id])
    outcome = fetch(feed, rss(item("a", "First", "https://example.com/a", "new body", now())))
    assert outcome["updated"] == 1
    updated = Story.objects.get(feed=feed)
    assert updated.id == story.id and "new body" in updated.content and updated.story_date == story.story_date
    assert ReadStory.objects.filter(user=user, story_id=story.id).exists()


def test_new_guid_with_nearly_identical_long_content_updates_existing(make_feed):
    feed = make_feed()
    text = " ".join(f"word{i}" for i in range(600))
    ts = now()
    fetch(feed, rss(item("guid-1", "A long story", "https://example.com/a", text, ts - 86400)))
    fetch(feed, rss(item("guid-2", "A long story", "https://example.com/a2", text + " extra", ts - 3600)))
    stories = list(Story.objects.filter(feed=feed))
    assert len(stories) == 1
    assert stories[0].guid == "guid-2" and stories[0].guid_hash == guid_hash("guid-2")


def test_guid_fallbacks(make_feed):
    feed = make_feed()
    ts = now()
    # all entries share one guid but have distinct links -> guids replaced by links
    body = rss(item("same", "One", "https://example.com/1", date=ts - 50) + item("same", "Two", "https://example.com/2", date=ts - 40))
    assert fetch(feed, body)["new"] == 2
    assert set(Story.objects.filter(feed=feed).values_list("guid", flat=True)) == {"https://example.com/1", "https://example.com/2"}


def test_six_character_hash_collisions_do_not_merge(make_feed):
    feed = make_feed()
    # Stories are identified by the full SHA-1, so any two different guids are two stories (R1.3).
    assert fetch(feed, rss(item("guid-x", "X", date=now() - 10) + item("guid-y", "Y", date=now() - 5)))["new"] == 2
    assert len({s.guid_hash for s in Story.objects.filter(feed=feed)}) == 2


def test_dates(make_feed):
    feed = make_feed()
    ts = now()
    future = item("future", "Future", date=ts + 86400)
    updated_later = (
        "<item><title>Dated</title><guid>dated</guid><description>x</description>"
        f"<pubDate>{formatdate(ts - 7200)}</pubDate>"
        f"<atom:updated>{datetime.fromtimestamp(ts - 60, timezone.utc).isoformat()}</atom:updated></item>"
    )
    fetch(feed, rss(future + updated_later))
    assert abs(Story.objects.get(feed=feed, guid="future").story_date - ts) < 120
    assert Story.objects.get(feed=feed, guid="dated").story_date == ts - 7200


def test_sanitizer_and_enclosure(make_feed):
    feed = make_feed()
    enclosure = '<enclosure url="https://example.com/audio.mp3" type="audio/mpeg" length="1"/>'
    body = rss(item("a", "Media", "https://example.com/post/1", '<script>alert(1)</script><img src="/a.png"><p onclick="x()">hi</p>', now() - 5, enclosure))
    fetch(feed, body)
    story = Story.objects.get(feed=feed)
    assert "<script" not in story.content and "alert(1)" not in story.content and "onclick" not in story.content
    assert 'src="https://example.com/a.png"' in story.content
    assert story.content.rstrip().endswith("</audio></p>")
    assert story.image_urls == ["https://example.com/a.png"]


def test_escaped_html_with_unknown_tags_is_html(make_feed):
    # feedparser calls this text/plain because of the <svg>; it is HTML all the same.
    body = '&lt;h3&gt;Head&lt;svg viewBox="0 0 16 16"&gt;&lt;path d="M4 9"&gt;&lt;/path&gt;&lt;/svg&gt;&lt;/h3&gt;&lt;p&gt;Text&lt;/p&gt;'
    feed = make_feed()
    fetch(feed, rss(f"<item><title>T</title><guid>g1</guid><content>{body}</content></item>"))
    content = Story.objects.get(feed=feed).content
    assert "<p>Text</p>" in content and "&lt;p&gt;" not in content


def test_plain_text_is_escaped(make_feed):
    feed = make_feed()
    fetch(feed, rss("<item><title>T</title><guid>g1</guid><content>a &lt; b and c &gt; d\nnext</content></item>"))
    content = Story.objects.get(feed=feed).content
    assert "a &lt; b" in content and "<br>" in content


def test_sanitize_iframes_and_links():
    html = (
        '<a href="/x">link</a><iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe>'
        '<iframe src="http://evil.example/frame"></iframe><video poster="/p.jpg" src="/v.mp4"></video>'
    )
    clean = sanitize(html, "https://site.example/post")
    assert 'href="https://site.example/x"' in clean and 'rel="noopener noreferrer"' in clean and 'target="_blank"' in clean
    assert "youtube.com/embed/abcdefghijk" in clean and clean.count("<iframe") == 1
    assert 'href="http://evil.example/frame"' in clean
    assert extract_image_urls(clean) == ["https://img.youtube.com/vi/abcdefghijk/0.jpg", "https://site.example/p.jpg"]


def test_sanitize_keeps_only_display_style():
    clean = sanitize(
        '<div style="display: none; color: red">hidden</div><p style="display: none">p</p>'
        '<span style="position: fixed">s</span>'
    )
    assert '<div style="display:none">hidden</div>' in clean
    assert "<p>p</p>" in clean
    assert "color" not in clean and "position" not in clean


def test_json_feed(make_feed):
    feed = make_feed()
    body = (
        b'{"version":"https://jsonfeed.org/version/1.1","title":"JSON","home_page_url":"https://example.com/",'
        b'"items":[{"id":"1","url":"https://example.com/1","title":"Hello","content_html":"<p>World</p>",'
        b'"date_published":"2026-11-01T10:00:00Z"}]}'
    )
    outcome = pipeline.process_feed(feed.id, {"kind": "fetch", "body": body, "content_type": "application/feed+json", "headers": {}})
    assert outcome["new"] == 1
    assert Story.objects.get(feed=feed).title == "Hello"


def test_html_at_feed_address_is_552_and_asks_for_rediscovery(make_feed):
    feed = make_feed()
    outcome = pipeline.process_feed(
        feed.id, {"kind": "fetch", "body": b"<!doctype html><html><body>hi</body></html>", "content_type": "text/html", "headers": {}}
    )
    assert outcome["error"] == 552 and outcome["rediscover"]
    feed.refresh_from_db()
    assert feed.has_exception and feed.errors_since_good == 1
    assert FeedFetchLog.objects.filter(feed=feed, status_code=552, message="Non-xml feed").exists()


def test_fetched_event_and_tree_carry_the_failure_state(api, user, make_feed, subscribe):
    """The start page lists failing feeds and the last update from the tree and feed:fetched events."""
    feed = make_feed()
    subscribe(user, feed)
    pipeline.record_failure(feed.id, 500, "Server error")
    pipeline.record_failure(feed.id, 500, "Server error")
    feed.refresh_from_db()
    event = WorkerEvent.objects.filter(feed_id=feed.id, type="feed_fetched").last()
    assert event.payload["errors_since_good"] == 2 and event.payload["last_update"] == feed.last_update > 0
    row = api.get("/api/tree").json()["feeds"][0]
    assert row["errors_since_good"] == 2 and row["last_update"] == feed.last_update


def test_parse_page_finds_feed_and_icon_links():
    page = (
        b'<html><head><title>Site</title><link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        b'<link rel="icon" href="/big.png" sizes="192x192"><link rel="icon" href="/i.png" sizes="32x32"></head></html>'
    )
    result = parse_page(page, "https://site.example/blog/", "text/html")
    assert not result["is_feed"] and result["feed_links"] == ["https://site.example/feed.xml"]
    assert result["icon_links"][0] == "https://site.example/i.png"
    assert parse_page(rss(item("a")), "https://site.example/feed.xml", "text/xml")["is_feed"]


def test_permanent_redirect_changes_address_after_three_fetches(make_feed):
    feed = make_feed("https://old.example/feed")
    body = rss(item("a", date=now() - 5))
    fetch(feed, body)  # first fetch, no redirect
    for expected in ("https://old.example/feed", "https://old.example/feed", "https://new.example/feed"):
        fetch(feed, body, permanent_url="https://new.example/feed")
        feed.refresh_from_db()
        assert feed.feed_address == expected


def test_scheduling_intervals(settings):
    settings.FETCH_INTERVAL_MINUTES = 60
    ts = now()
    assert scheduling.interval_minutes(90, ts, 0, False, ts) == 45
    assert scheduling.interval_minutes(15, ts, 0, False, ts) == 60
    assert scheduling.interval_minutes(0, ts - 90 * 86400, 0, False, ts) == 60
    assert scheduling.interval_minutes(15, ts, 5, False, ts) == 300
    assert scheduling.interval_minutes(15, ts, 1000, False, ts) == 7 * 24 * 60
    assert scheduling.interval_minutes(90, ts, 0, True, ts) == 540
    hint = scheduling.server_hint_seconds({"retry-after": "7200", "cache-control": "max-age=600"}, ts)
    assert hint == 7200 and scheduling.interval_minutes(90, ts, 0, False, ts, hint) == 120
    upper = ts + 45 * 60 + 45 * 60 / 4
    assert ts + 45 * 60 <= scheduling.next_update(45, ts) <= upper


def test_error_backoff_and_log_retention(make_feed):
    feed = make_feed()
    body = rss(item("a", date=now() - 5))
    for _ in range(6):
        fetch(feed, body)
    assert FeedFetchLog.objects.filter(feed=feed, type="feed").count() == 5
    pipeline.record_failure(feed.id, 500, "Error")
    for _ in range(40):
        fetch(feed, body)
        pipeline.record_failure(feed.id, 500, "Error")
    assert FeedFetchLog.objects.filter(feed=feed, type="feed").count() == 25
    feed.refresh_from_db()
    assert feed.errors_since_good == 1 and feed.has_exception


def test_counters_use_utc_and_survive_pruning(make_feed, settings):
    settings.STORIES_PER_FEED = 2
    feed = make_feed()
    date = int(datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc).timestamp())
    fetch(feed, rss("".join(item(f"g{i}", f"Story number {i}", f"https://example.com/{i}", f"text {i}", date + i) for i in range(3))))
    counts = {(c.period_type, c.period_key): c.count for c in FeedStoryCount.objects.filter(feed=feed)}
    assert counts == {("month", "2026-10"): 3, ("weekday", "5"): 3, ("hour", "23"): 3}
    assert Story.objects.filter(feed=feed).count() == 2  # pruned to STORIES_PER_FEED
    assert FeedStoryCount.objects.get(feed=feed, period_type="month").count == 3


def test_pruning_protects_unread_stories(make_feed, make_story, user, subscribe, settings):
    settings.STORIES_PER_FEED = 3
    feed = make_feed()
    subscribe(user, feed)
    stories = [make_story(feed, age_days=1 + i * 0.01) for i in range(5)]  # newest first
    unread.mark_stories_read(user, [stories[4].id])
    pipeline.prune(feed.id, now())
    remaining = set(Story.objects.filter(feed=feed).values_list("id", flat=True))
    assert stories[3].id in remaining  # beyond the limit but still unread
    assert stories[4].id not in remaining  # beyond the limit and read


def test_dominant_color():
    solid = Image.new("RGBA", (16, 16), (0x33, 0x66, 0x99, 255))
    assert dominant_color(solid) == "487bae"
    marked = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
    for x in range(8):
        for y in range(8):
            marked.putpixel((x, y), (220, 20, 30, 255))
    color = dominant_color(marked)
    assert color == dominant_color(marked)
    red, green, blue = int(color[:2], 16), int(color[2:4], 16), int(color[4:], 16)
    assert red > 200 and green < 100 and blue < 100


def test_process_icon_stores_png(make_feed):
    from worker.tasks import process_icon

    feed = make_feed()
    data = io.BytesIO()
    Image.new("RGB", (128, 128), (0x33, 0x66, 0x99)).save(data, format="ICO")
    assert process_icon(feed.id, data.getvalue(), "https://example.com/favicon.ico")["ok"]
    feed.refresh_from_db()
    assert feed.favicon_color == "487bae" and bytes(feed.icon.data).startswith(b"\x89PNG")


def test_normalize_url():
    assert normalize_url("example.com/feed") == "https://example.com/feed"
    assert normalize_url("https:/example.com/feed.xml?a=1&b=2") == "https://example.com/feed.xml?a=1&b=2"
    assert normalize_url("@user@mastodon.social") == "https://mastodon.social/users/user.rss"
    assert normalize_url("https://www.youtube.com/channel/UC123") == "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"
    with pytest.raises(InvalidUrl):
        normalize_url("not a url")


def test_similarity_gives_up_quickly():
    started = time.monotonic()
    assert pipeline._find_similar({"title": "x", "content": "y" * 2000, "story_date": 0, "permalink": ""}, [], started + 5) is None
