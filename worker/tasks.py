"""Pool tasks (worker/tasks.py): the CPU-bound stages. They run in pool children and make
no network requests."""
import io
import logging
import re
from urllib.parse import urljoin

import lxml.html
import numpy as np
from django.db import transaction
from PIL import Image

from feeds.models import Feed, FeedIcon, Story
from feeds.parsing import parse_feed
from feeds.sanitize import extract_image_urls, sanitize
from feeds.util import now, plain_text, strip_tags
from reading.models import SavedStory
from worker.models import WorkerEvent
from worker.pipeline import process_feed

log = logging.getLogger("worker.tasks")

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json", "application/json")
ICON_FORMATS = {"ICO", "PNG", "GIF", "JPEG", "WEBP"}
Image.MAX_IMAGE_PIXELS = 4096 * 4096


# ---------------------------------------------------------------- parse_page


def _icon_size(link) -> int:
    match = re.search(r"(\d+)\s*[xX]\s*(\d+)", link.get("sizes") or "")
    return int(match.group(1)) if match else 0


def parse_page(body: bytes, base_url: str, content_type: str = "") -> dict:
    """Decide whether the body is a feed; otherwise list feed links and icon links."""
    parsed = parse_feed(body, content_type)
    if parsed and parsed["version"]:
        return {
            "is_feed": True,
            "title": strip_tags(parsed["title"])[:255],
            "site_link": parsed["link"] if parsed["link"].startswith(("http://", "https://")) else "",
            "image": parsed.get("image") or "",
        }
    result = {"is_feed": False, "title": "", "feed_links": [], "icon_links": []}
    try:
        tree = lxml.html.fromstring(body)
    except Exception:
        return result
    title = tree.findtext(".//title")
    result["title"] = strip_tags(title or "")[:255]
    icons = []
    for link in tree.iter("link"):
        rel = (link.get("rel") or "").lower()
        href = (link.get("href") or "").strip()
        if not href:
            continue
        kind = (link.get("type") or "").lower().strip()
        if "alternate" in rel.split() and kind in FEED_TYPES:
            result["feed_links"].append(urljoin(base_url, href))
        elif "icon" in rel and "mask-icon" not in rel:
            if "svg" in kind or href.lower().split("?")[0].endswith(".svg"):
                continue  # SVG icons are skipped
            icons.append((urljoin(base_url, href), _icon_size(link)))
    # Prefer the largest declared size up to 64 px, else the first.
    sized = sorted((i for i in icons if 0 < i[1] <= 64), key=lambda i: -i[1])
    ordered = [url for url, _ in sized] + [url for url, _ in icons]
    result["icon_links"] = list(dict.fromkeys(ordered))
    return result


# ---------------------------------------------------------------- process_icon


def _kmeans(points: np.ndarray, k: int, iterations: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """A short fixed-seed k-means: the same image always gives the same color."""
    rng = np.random.RandomState(0)
    unique = np.unique(points, axis=0)
    k = min(k, len(unique))
    centroids = unique[rng.choice(len(unique), k, replace=False)].astype(np.float64)
    labels = np.zeros(len(points), dtype=np.int64)
    for _ in range(iterations):
        distances = ((points[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        new_labels = distances.argmin(axis=1)
        for i in range(k):
            members = points[new_labels == i]
            if len(members):
                centroids[i] = members.mean(axis=0)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
    return centroids, np.bincount(labels, minlength=k)


def dominant_color(image: Image.Image) -> str | None:
    """Port of NewsBlur's determine_dominant_color_in_image with one change: pixels with alpha below 128
    are ignored. K-means uses a fixed seed, so the same image always gives the same color."""
    pixels = np.asarray(image.convert("RGBA"), dtype=np.float64).reshape(-1, 4)
    points = pixels[pixels[:, 3] >= 128][:, :3]
    if len(points) == 0:
        return None
    centroids, counts = _kmeans(points, 5)
    keep = np.ones(len(centroids), dtype=bool)
    for low, high in ((60, 200), (35, 230), (10, 250)):
        mask = ~((centroids < low).all(axis=1) | (centroids > high).all(axis=1))
        if mask.any():
            keep = mask
            break
    counts = np.where(keep, counts, -1)
    peak = centroids[counts.argmax()]
    return "".join(f"{min(255, int(channel) + 21):02x}" for channel in peak)


def process_icon(feed_id: int, data: bytes, icon_url: str) -> dict:
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in ICON_FORMATS:
            return {"ok": False, "reason": f"unsupported format {image.format}"}
        image = image.convert("RGBA")
        image.thumbnail((64, 64), Image.LANCZOS)
        color = dominant_color(image)
    except Exception as e:
        return {"ok": False, "reason": f"cannot decode: {e}"}
    if color is None:
        return {"ok": False, "reason": "empty image"}
    out = io.BytesIO()
    image.save(out, format="PNG")
    ts = now()
    with transaction.atomic():
        if not Feed.objects.filter(pk=feed_id).exists():
            return {"ok": False, "reason": "feed gone"}
        FeedIcon.objects.update_or_create(
            feed_id=feed_id,
            defaults={"data": out.getvalue(), "content_type": "image/png", "icon_url": icon_url[:1000], "updated_at": ts},
        )
        Feed.objects.filter(pk=feed_id).update(favicon_color=color, favicon_not_found=False, favicon_checked_at=ts)
        WorkerEvent.objects.create(at=ts, type="icon", feed_id=feed_id, payload={"favicon_color": color})
    return {"ok": True, "color": color}


# ---------------------------------------------------------------- extract_text


def _extract_html(page: str, url: str) -> str:
    import trafilatura

    try:
        text = trafilatura.extract(
            page, url=url, output_format="html", include_images=True, include_links=True, include_tables=True
        )
    except Exception as e:
        log.info("trafilatura failed for %s: %s", url, e)
        text = None
    if text and plain_text(text):
        return text
    try:
        from readability import Document

        return Document(page).summary(html_partial=True) or ""
    except Exception as e:
        log.info("readability failed for %s: %s", url, e)
        return ""


def extract_text(story_id: int | None, saved_id: int | None, body: bytes, url: str) -> dict:
    story = Story.objects.filter(pk=story_id).first() if story_id else None
    saved = SavedStory.objects.filter(pk=saved_id).first() if saved_id else None
    source = story or saved
    if source is None:
        return {"failed": True, "reason": "story not found", "url": url}
    try:
        tree = lxml.html.fromstring(body)
        for noscript in list(tree.iter("noscript")):
            noscript.drop_tag()
        page = lxml.html.tostring(tree, encoding="unicode")
    except Exception:
        return {"failed": True, "reason": "page could not be parsed", "url": url}
    text = sanitize(_extract_html(page, url), url)
    images = source.image_urls or []
    if images:
        present = extract_image_urls(text)
        if not any(image in present for image in images):
            text = f'<p><img src="{images[0]}"></p>' + text
    length = len(plain_text(text))
    if length < 100:
        return {"failed": True, "reason": "no article text found", "url": url}
    if length <= len(plain_text(source.content)):
        return {"failed": True, "reason": "the page has no more text than the feed", "url": url}
    ts = now()
    with transaction.atomic():
        if story is not None:
            Story.objects.filter(pk=story.pk).update(original_text=text, original_text_at=ts)
            SavedStory.objects.filter(feed_id=story.feed_id, guid_hash=story.guid_hash).update(original_text=text)
        if saved is not None:
            SavedStory.objects.filter(pk=saved.pk).update(original_text=text)
    return {"failed": False, "reason": "", "url": url}


TASKS = {
    "process_feed": process_feed,
    "extract_text": extract_text,
    "process_icon": process_icon,
    "parse_page": parse_page,
}
TIME_LIMITS = {"process_feed": 45, "extract_text": 20, "process_icon": 15, "parse_page": 10}
