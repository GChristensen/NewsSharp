"""Small shared helpers (feeds/util.py): time, URL normalization, plain text, hashes, image signatures."""
import base64
import hashlib
import hmac
import html
import re
import time
from urllib.parse import urlsplit, urlunsplit


def now() -> int:
    return int(time.time())


def guid_hash(guid: str) -> str:
    """Full SHA-1 hex of the guid."""
    return hashlib.sha1(guid.encode("utf-8", "replace")).hexdigest()


class InvalidUrl(ValueError):
    pass


_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_FEDI = re.compile(r"^@?([\w.-]+)@([\w-]+(?:\.[\w-]+)+)$")
_YT_CHANNEL = re.compile(r"^/channel/([\w-]+)")


def normalize_url(url: str, max_len: int = 764) -> str:
    """Normalize a feed or site URL. Raises InvalidUrl."""
    url = (url or "").strip()
    if not url or any(c.isspace() for c in url):
        raise InvalidUrl("invalid URL")
    fedi = _FEDI.match(url)
    if fedi:
        url = f"https://{fedi.group(2)}/users/{fedi.group(1)}.rss"
    if url.lower().startswith("feed://"):
        url = "http://" + url[7:]
    elif url.lower().startswith("feed:"):
        url = url[5:]
    url = re.sub(r"^(https?):/(?!/)", r"\1://", url, flags=re.I)
    if not _SCHEME.match(url):
        url = "https://" + url
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError as e:
        raise InvalidUrl("invalid URL") from e
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        raise InvalidUrl("invalid URL")
    path = parts.path or "/"
    query = parts.query
    netloc = parts.netloc.lower()
    if host.lower() in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        channel = _YT_CHANNEL.match(path)
        if channel:
            scheme, netloc, path, query = "https", "www.youtube.com", "/feeds/videos.xml", f"channel_id={channel.group(1)}"
    result = urlunsplit((scheme, netloc, path, query, ""))
    if len(result) > max_len:
        raise InvalidUrl("URL too long")
    return result


def site_root(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


_TAG = re.compile(r"<[^>]*>")
_BLOCK_END = re.compile(r"</(?:p|div|li|h[1-6]|blockquote|tr|pre|figure|figcaption)\s*>|<br\s*/?>", re.I)
_WS = re.compile(r"[ \t\r\f\v ]+")
_ANY_WS = re.compile(r"\s+")


def strip_tags(value: str) -> str:
    """Text of an HTML snippet on one line."""
    return _ANY_WS.sub(" ", html.unescape(_TAG.sub("", value or ""))).strip()


def plain_text(value: str, limit: int | None = None, breaks: bool = False) -> str:
    """Plain text of HTML content. With breaks=True paragraph ends become newlines."""
    value = value or ""
    if not breaks:
        text = strip_tags(_BLOCK_END.sub(" ", value))
    else:
        text = html.unescape(_TAG.sub("", _BLOCK_END.sub("\n", value)))
        lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
        text = "\n".join(line for line in lines if line)
    return text[:limit] if limit else text


# Length of the plain-text summary of a story: the list preview, and the text story filters match.
PREVIEW_CHARS = 1500


def story_summary(content: str) -> str:
    """The stored plain-text summary of a story's HTML content (Story.summary)."""
    return plain_text(content, limit=PREVIEW_CHARS)


def image_signature(url: str, secret: str) -> str:
    """The image proxy signature: HMAC-SHA256(image_secret, url), base64url, first 22 characters."""
    digest = hmac.new(secret.encode("ascii"), url.encode("utf-8", "replace"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")[:22]


def chunks(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i : i + size]
