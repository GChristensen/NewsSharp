"""Content sanitization at store time (feeds/sanitize.py) and story image extraction."""
import html
import re
from urllib.parse import urljoin, urlsplit

import lxml.html
import nh3

from .util import strip_tags

ALLOWED_TAGS = {
    "p", "br", "hr", "b", "strong", "i", "em", "u", "s", "strike", "del", "ins", "sub", "sup", "small",
    "mark", "abbr", "cite", "q", "span", "div", "kbd", "samp", "var", "time", "dl", "dt", "dd", "details",
    "summary", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "thead", "tbody", "tfoot",
    "tr", "th", "td", "caption", "colgroup", "col", "a", "img", "picture", "source", "figure",
    "figcaption", "blockquote", "pre", "code", "audio", "video", "iframe",
}  # fmt: skip
REMOVED_WITH_CONTENT = {"script", "style", "object", "embed", "form"}
GENERIC_ATTRIBUTES = {
    "src", "srcset", "alt", "title", "width", "height", "poster", "controls", "preload", "type",
    "colspan", "rowspan", "datetime",
}  # fmt: skip
ATTRIBUTES = {
    "*": GENERIC_ATTRIBUTES,
    "a": {"href", "title", "target"},
    "iframe": {"src", "width", "height", "allowfullscreen"},
    # Only the display property survives (STYLE_PROPERTIES), so feeds can hide text kept for filters.
    "div": GENERIC_ATTRIBUTES | {"style"},
    "span": GENERIC_ATTRIBUTES | {"style"},
}
STYLE_PROPERTIES = {"display"}
IFRAME_HOSTS = ("youtube.com", "youtube-nocookie.com", "player.vimeo.com")
_YOUTUBE_EMBED = re.compile(r"/embed/([\w-]{6,})")


def _absolute(value: str, base: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith(("data:", "javascript:", "mailto:", "#")):
        return value
    if base:
        try:
            return urljoin(base, value)
        except ValueError:
            return value
    if value.startswith("//"):
        return "https:" + value
    return value


def _absolute_srcset(value: str, base: str) -> str:
    parts = []
    for candidate in value.split(","):
        bits = candidate.strip().split(None, 1)
        if not bits:
            continue
        bits[0] = _absolute(bits[0], base)
        parts.append(" ".join(bits))
    return ", ".join(parts)


def _iframe_allowed(src: str) -> bool:
    try:
        parts = urlsplit(src)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in IFRAME_HOSTS)


def _serialize(root) -> str:
    out = [html.escape(root.text or "", quote=False)]
    for child in root:
        out.append(lxml.html.tostring(child, encoding="unicode"))
    return "".join(out)


def _parse(content: str):
    try:
        return lxml.html.fragment_fromstring(content, create_parent="div")
    except Exception:
        return None


def sanitize(content: str, base_url: str = "") -> str:
    """Allowlist sanitizer: an lxml pass (absolute URLs, iframe rule, link targets) and then nh3."""
    if not content or not content.strip():
        return ""
    root = _parse(content)
    if root is None:
        return html.escape(strip_tags(content))
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        for attr in ("href", "src", "poster"):
            value = el.get(attr)
            if value:
                el.set(attr, _absolute(value, base_url))
        if el.get("srcset"):
            el.set("srcset", _absolute_srcset(el.get("srcset"), base_url))
        if el.tag == "a":
            el.set("target", "_blank")
        elif el.tag == "iframe":
            src = el.get("src") or ""
            if _iframe_allowed(src):
                continue
            parent = el.getparent()
            if parent is None:
                continue
            if src.startswith(("http://", "https://")):
                link = lxml.html.Element("a")
                link.set("href", src)
                link.set("target", "_blank")
                link.text = src
                link.tail = el.tail
                parent.replace(el, link)
            else:
                el.drop_tree()
    return nh3.clean(
        _serialize(root),
        tags=ALLOWED_TAGS,
        clean_content_tags=REMOVED_WITH_CONTENT,
        attributes=ATTRIBUTES,
        filter_style_properties=STYLE_PROPERTIES,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"},
        strip_comments=True,
    )


def extract_image_urls(content: str) -> list[str]:
    """Story images: img sources, video posters and YouTube stills, in document order, without duplicates.
    Expects sanitized content (URLs already absolute)."""
    if not content or ("<img" not in content and "<video" not in content and "<iframe" not in content):
        return []
    root = _parse(content)
    if root is None:
        return []
    urls: list[str] = []
    for el in root.iter("img", "video", "iframe"):
        url = None
        if el.tag == "img":
            url = el.get("src")
        elif el.tag == "video":
            url = el.get("poster")
        else:
            src = el.get("src") or ""
            match = _YOUTUBE_EMBED.search(src)
            if match and "youtube" in src:
                url = f"https://img.youtube.com/vi/{match.group(1)}/0.jpg"
        if not url or len(url) >= 1024 or "feedburner.com" in url or not url.startswith(("http://", "https://")):
            continue
        if url not in urls:
            urls.append(url)
    return urls


_MEDIA_ATTR = re.compile(r'\b(?:src|poster)="([^"]+)"')
_SRCSET_ATTR = re.compile(r'\bsrcset="([^"]+)"')


def media_urls(content: str) -> list[str]:
    """src / poster / srcset URLs of sanitized content, for image_sigs."""
    urls = [html.unescape(u) for u in _MEDIA_ATTR.findall(content or "")]
    for srcset in _SRCSET_ATTR.findall(content or ""):
        for candidate in html.unescape(srcset).split(","):
            bits = candidate.strip().split()
            if bits:
                urls.append(bits[0])
    return [u for u in urls if u.startswith(("http://", "https://"))]
