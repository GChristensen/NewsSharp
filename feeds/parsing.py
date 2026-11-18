"""Feed parsing and entry normalization (feeds/parsing.py).
Runs only in worker pool children; never fetches anything."""
import calendar
import html
import io
import json
import random
import re
import warnings
from datetime import datetime, timezone

import feedparser
from dateutil import parser as date_parser

from .util import strip_tags

MAX_ENTRIES = 2000
MIN_DATE = 946684800  # 2000-01-01


# UTC offsets in hours of the zone abbreviations feeds use; dateutil knows only UTC/GMT/Z by itself
# and would read "10:00 EST" as 10:00 UTC.
_ZONE_HOURS = {
    "UT": 0, "UTC": 0, "GMT": 0, "Z": 0, "WET": 0, "WEST": 1, "BST": 1, "CET": 1, "CEST": 2, "MET": 1,
    "MEST": 2, "EET": 2, "EEST": 3, "MSK": 3, "JST": 9, "KST": 9, "AEST": 10, "AEDT": 11, "NZST": 12,
    "NZDT": 13, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
    "AKST": -9, "AKDT": -8, "HST": -10,
}  # fmt: skip
_TZINFOS = {name: hours * 3600 for name, hours in _ZONE_HOURS.items()}


def _text_date(text: str) -> tuple[int | None, bool]:
    """A date in free text as a timestamp, and whether the text gave its time zone. A date without
    a zone is taken as UTC."""
    if not text or not isinstance(text, str):
        return None, False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = date_parser.parse(text, tzinfos=_TZINFOS)
    except (ValueError, OverflowError, TypeError):
        return None, False
    zoned = parsed.tzinfo is not None
    if not zoned:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return int(parsed.timestamp()), zoned
    except (ValueError, OverflowError, OSError):
        return None, False


def _lenient_date(text: str) -> int | None:
    return _text_date(text)[0]


def _struct_ts(value) -> int | None:
    if not value:
        return None
    try:
        return calendar.timegm(value)
    except (ValueError, OverflowError, TypeError):
        return None


# A closing tag or a void element: markup that plain prose does not contain.
_MARKUP = re.compile(r"</[a-zA-Z][a-zA-Z0-9]*\s*>|<(?:br|img|hr)\b[^<>]*>", re.IGNORECASE)


def _text_value(detail: dict | None, value: str) -> str:
    """feedparser types an element as text/plain when its HTML has tags outside its own allow-list
    (an inline <svg>, for example); such values are still HTML and are sanitized as HTML."""
    if detail and detail.get("type") == "text/plain" and not _MARKUP.search(value or ""):
        return html.escape(value or "").replace("\n", "<br>")
    return value or ""


def _fp_entry(e) -> dict:
    content_items = e.get("content") or []
    content = ""
    if content_items:
        content = _text_value(content_items[0], content_items[0].get("value") or "")
    summary = _text_value(e.get("summary_detail"), e.get("summary") or "")
    body = content if len(content) >= len(summary) else summary
    if not body.strip():
        body = e.get("subtitle") or ""
    links = e.get("links") or []
    media = []
    for item in (e.get("media_content") or [])[:15]:
        media.append({"url": item.get("url") or "", "type": item.get("type") or item.get("medium") or "", "rel": "media"})
    for item in links[:15]:
        media.append({"url": item.get("href") or "", "type": item.get("type") or "", "rel": item.get("rel") or ""})
    tags = [t.get("label") or t.get("term") or "" for t in (e.get("tags") or [])]
    return {
        "guid": e.get("guid") or e.get("id") or "",
        "id": e.get("id") or "",
        "title": e.get("title") or "",
        "author": e.get("author") or e.get("credit") or "",
        "link": e.get("link") or (links[0].get("href") if links else "") or "",
        "content": body,
        "published": e.get("published") or "",
        "published_ts": _struct_ts(e.get("published_parsed")),
        "updated_ts": _struct_ts(e.get("updated_parsed")) if "updated_parsed" in e else None,
        "tags": tags,
        "media": media,
    }


def _parse_json_feed(body: bytes) -> dict | None:
    try:
        data = json.loads(body.decode("utf-8-sig", "replace"))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    entries = []
    for item in data["items"]:
        if not isinstance(item, dict):
            continue
        content = item.get("content_html") or ""
        if not content and item.get("content_text"):
            content = html.escape(item["content_text"]).replace("\n", "<br>")
        if not content and item.get("summary"):
            content = html.escape(item["summary"])
        authors = item.get("authors") or ([item["author"]] if isinstance(item.get("author"), dict) else [])
        author = ", ".join(a.get("name", "") for a in authors if isinstance(a, dict) and a.get("name"))
        media = []
        image = item.get("image") or item.get("banner_image")
        if image:
            media.append({"url": image, "type": "image", "rel": "media"})
        for attachment in (item.get("attachments") or [])[:15]:
            if isinstance(attachment, dict):
                media.append({"url": attachment.get("url") or "", "type": attachment.get("mime_type") or "", "rel": "enclosure"})
        entries.append(
            {
                "guid": str(item.get("id") or ""),
                "id": str(item.get("id") or ""),
                "title": item.get("title") or "",
                "author": author,
                "link": item.get("url") or item.get("external_url") or "",
                "content": content,
                "published": item.get("date_published") or "",
                "published_ts": None,
                "updated_ts": _lenient_date(item.get("date_modified") or ""),
                "tags": [t for t in (item.get("tags") or []) if isinstance(t, str)],
                "media": media,
            }
        )
    hubs = [h.get("url") for h in (data.get("hubs") or []) if isinstance(h, dict) and h.get("url")]
    return {
        "version": "json1",
        "bozo": False,
        "title": data.get("title") or "",
        "tagline": data.get("description") or "",
        "link": data.get("home_page_url") or "",
        "hubs": hubs,
        "self": data.get("feed_url") or None,
        "image": data.get("icon") or data.get("favicon") or "",
        "entries": entries,
    }


def parse_feed(body: bytes, content_type: str = "") -> dict | None:
    """Parse downloaded bytes. Returns None when the body cannot be parsed at all (551).
    An empty `version` with no entries means the body is not a feed (552 / 553)."""
    if not body or not body.strip():
        return None
    content_type = content_type or ""
    head = body.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if "json" in content_type.lower() or head == b"{":
        parsed = _parse_json_feed(body)
        if parsed is not None:
            return parsed
        if "json" in content_type.lower():
            return None
    try:
        d = feedparser.parse(
            io.BytesIO(body),
            response_headers={"content-type": content_type} if content_type else None,
            resolve_relative_uris=False,
            sanitize_html=False,
        )
    except Exception:
        return None
    feed = d.get("feed") or {}
    links = feed.get("links") or []
    image = (feed.get("image") or {}).get("href") or feed.get("icon") or feed.get("logo") or ""
    return {
        "version": d.get("version") or "",
        "bozo": bool(d.get("bozo")),
        "title": feed.get("title") or "",
        "tagline": feed.get("subtitle") or "",
        "link": feed.get("link") or "",
        "hubs": [link.get("href") for link in links if link.get("rel") == "hub" and link.get("href")],
        "self": next((link.get("href") for link in links if link.get("rel") == "self" and link.get("href")), None),
        "image": image,
        "entries": [_fp_entry(e) for e in d.get("entries") or []],
    }


def looks_like_html(body: bytes, content_type: str) -> bool:
    head = body[:2048].lstrip().lower()
    return "html" in (content_type or "").lower() or head.startswith((b"<!doctype html", b"<html")) or b"<html" in head


def _clean_tags(raw: list[str]) -> list[str]:
    """Tags split on `,` and `/`, lower-cased, whitespace collapsed, without duplicates; at most 12."""
    result = []
    for value in raw:
        for part in re.split(r"[,/]", value or ""):
            tag = re.sub(r"\s+", " ", part).strip().lower()[:250]
            if tag and tag not in result:
                result.append(tag)
    return result[:12]


def _append_enclosures(content: str, media: list[dict]) -> str:
    """Append audio, video and image enclosures that the content does not already contain."""
    for item in media:
        url = item.get("url") or ""
        kind = (item.get("type") or "").lower()
        rel = (item.get("rel") or "").lower()
        if not url or not url.startswith(("http://", "https://")) or url in content:
            continue
        safe = html.escape(url, quote=True)
        if "audio" in kind:
            content += f'<p><audio controls preload="none" src="{safe}"></audio></p>'
        elif "video" in kind:
            content += f'<p><video controls preload="none" src="{safe}"></video></p>'
        elif "image" in kind:
            content += f'<p><img src="{safe}"></p>'
        elif rel != "enclosure" or kind.startswith(("text/", "application/")):
            continue
        else:
            content += f'<p><a href="{safe}">Download</a></p>'
    return content


def story_date(entry: dict, now_ts: int) -> int:
    """The story date: the entry's published text first, then feedparser's dates, then now. Text whose time
    zone was not understood yields to feedparser's date, which knows more zone names."""
    value, zoned = _text_date(entry.get("published"))
    if value is not None and not zoned and entry.get("published_ts") is not None:
        value = entry["published_ts"]
    if value is None:
        value = entry.get("published_ts")
    if value is None:
        value = entry.get("updated_ts")
    if value is None or value < MIN_DATE or value > now_ts:
        return now_ts - random.randint(0, 59)
    return value


def build_stories(parsed: dict, now_ts: int) -> list[dict]:
    """Normalize parsed entries into story dicts. Content is not yet sanitized."""
    stories = []
    for entry in parsed["entries"]:
        title = strip_tags(entry.get("title") or "")
        content = _append_enclosures(entry.get("content") or "", entry.get("media") or [])
        if not title and not content.strip():
            continue
        date = story_date(entry, now_ts)
        permalink = (entry.get("link") or entry.get("id") or "").strip()
        guid = entry.get("guid") or permalink or datetime.fromtimestamp(date, timezone.utc).isoformat()
        stories.append(
            {
                "guid": guid,
                "title": title,
                "author": strip_tags(entry.get("author") or "")[:255],
                "permalink": permalink,
                "content": content,
                "tags": _clean_tags(entry.get("tags") or []),
                "story_date": date,
            }
        )
    if len(stories) > MAX_ENTRIES:
        stories = sorted(stories, key=lambda s: s["story_date"], reverse=True)[:MAX_ENTRIES]
    # When every entry shares one guid, fall back to the permalink, then to the date.
    if len(stories) > 1 and len({s["guid"] for s in stories}) == 1:
        if len({s["permalink"] for s in stories}) > 1:
            for s in stories:
                s["guid"] = s["permalink"]
        else:
            for s in stories:
                s["guid"] = datetime.fromtimestamp(s["story_date"], timezone.utc).isoformat()
    return stories


_LINK_HEADER = re.compile(r'<([^>]+)>\s*;[^,]*?rel="?([\w\s-]+)"?', re.I)


def link_header_rels(value: str) -> dict[str, str]:
    """rel -> URL of an HTTP Link header."""
    result: dict[str, str] = {}
    for url, rels in _LINK_HEADER.findall(value or ""):
        for rel in rels.lower().split():
            result.setdefault(rel, url.strip())
    return result
