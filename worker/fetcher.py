"""HTTP downloads of the worker's coordinator (worker/fetcher.py), R2.1-R2.8, R2.16 and feed
discovery, R2.20-R2.22. Nothing is parsed here; parsing is a pool task."""
import asyncio
import logging
import ssl
import time
from dataclasses import dataclass, field
from email.utils import formatdate, parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx
from django.conf import settings

from feeds.util import InvalidUrl, normalize_url, site_root

log = logging.getLogger("worker.fetcher")

NEUTRAL_UA = f"{settings.APP_NAME}/{settings.APP_VERSION} feed fetcher"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
FEED_ACCEPT = "application/atom+xml, application/rss+xml, application/xml;q=0.8, text/xml;q=0.6, */*;q=0.2"
FEED_BODY_LIMIT = 10 * 1024 * 1024
PROBE_PATHS = ("/feed", "/feed/", "/rss", "/rss.xml", "/atom.xml", "/index.xml", "/feed.xml")
NO_FEED_MESSAGE = "This address does not point to an RSS feed or a website with an RSS feed."


@dataclass
class Download:
    status: int = 0
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    url: str = ""
    permanent_url: str | None = None  # the address after a leading chain of 301 / 308 redirects
    error_code: int = 0
    error: str = ""
    ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.error_code and self.status < 400

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")


def _proxy() -> httpx.Proxy:
    """config.yaml's proxy. `proxy.verify` is about the TLS connection to an https:// proxy itself;
    the TLS of the sites behind it is always checked."""
    if not settings.PROXY_URL.lower().startswith("https://"):
        return httpx.Proxy(settings.PROXY_URL)  # httpcore refuses a TLS context for other schemes
    verify = settings.PROXY_VERIFY
    if verify is False:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif isinstance(verify, str):
        context = ssl.create_default_context(cafile=verify)
    else:
        context = ssl.create_default_context()
    return httpx.Proxy(settings.PROXY_URL, ssl_context=context)


def make_client(proxied: bool = False) -> httpx.AsyncClient:
    """The coordinator's client; with `proxied`, one that sends every request through the proxy."""
    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(connect=10, read=15, write=15, pool=30),
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=10),
        proxy=_proxy() if proxied else None,
    )


async def _get(client: httpx.AsyncClient, url: str, headers: dict, max_bytes: int, max_redirects: int) -> Download:
    result = Download(url=url)
    permanent_chain = True
    for _ in range(max_redirects + 1):
        async with client.stream("GET", result.url, headers=headers) as response:
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                target = urljoin(result.url, response.headers["location"])
                if response.status_code in (301, 308) and permanent_chain:
                    result.permanent_url = target
                else:
                    permanent_chain = False
                result.url = target
                continue
            result.status = response.status_code
            result.headers = {k.lower(): v for k, v in response.headers.items()}
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    result.error_code, result.error = 413, "Body too large"
                    return result
            result.body = bytes(body)
            return result
    result.error_code, result.error = 502, "Too many redirects"
    return result


async def download(
    client: httpx.AsyncClient, url: str, *, headers: dict | None = None, max_bytes: int = FEED_BODY_LIMIT,
    total_timeout: float = 30, max_redirects: int = 10,
) -> Download:  # fmt: skip
    """One GET with manual redirect handling, a body limit and a total time limit (R2.4)."""
    started = time.monotonic()
    request_headers = {"Accept-Encoding": "gzip, deflate", **(headers or {})}
    try:
        result = await asyncio.wait_for(_get(client, url, request_headers, max_bytes, max_redirects), total_timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        result = Download(url=url, error_code=504, error="Timeout")
    except httpx.InvalidURL:
        # Not an HTTPError: an address (or a redirect target) httpx cannot build a request for.
        result = Download(url=url, error_code=502, error="Invalid URL")
    except (httpx.HTTPError, ValueError, OSError) as e:
        result = Download(url=url, error_code=502, error=f"Connection error: {type(e).__name__}"[:200])
    result.ms = int((time.monotonic() - started) * 1000)
    return result


def redirected_elsewhere(result: Download, address: str) -> bool:
    """Whether a download went through a permanent redirect to an address other than `address`
    (the comparison process_feed makes, R2.8)."""
    if not result.permanent_url:
        return False
    try:
        return normalize_url(result.permanent_url) != address
    except InvalidUrl:
        return False


def _http_date(value: str) -> str:
    try:
        return formatdate(parsedate_to_datetime(value).timestamp(), usegmt=True)
    except (TypeError, ValueError):
        return value


def conditional_headers(feed: dict, forced: bool) -> dict:
    """R2.6: If-None-Match / If-Modified-Since from the last good response, unless forced. A value
    that is not ASCII cannot be sent as a header and is left out."""
    if not feed["fetched_once"] or forced:
        return {}
    headers = {}
    etag = feed["etag"] or ""
    if etag and etag.isascii():
        headers["If-None-Match"] = etag
    last_modified = _http_date(feed["last_modified"]) if feed["last_modified"] else ""
    if last_modified and last_modified.isascii():
        headers["If-Modified-Since"] = last_modified
    if headers:
        headers["A-IM"] = "feed"
    return headers


async def download_feed(client: httpx.AsyncClient, feed: dict, forced: bool) -> Download:
    """R2.1-R2.5: neutral user agent, conditional headers unless forced, one retry as a browser."""
    headers = {"User-Agent": NEUTRAL_UA, "Accept": FEED_ACCEPT, **conditional_headers(feed, forced)}
    result = await download(client, feed["feed_address"], headers=headers)
    if result.error_code in (502, 504) or result.status >= 400:
        first_ms = result.ms
        result = await download(client, feed["feed_address"], headers={**headers, "User-Agent": BROWSER_UA})
        result.ms += first_ms
    return result


async def download_page(client: httpx.AsyncClient, url: str, max_bytes: int, timeout: float) -> Download:
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    return await download(client, url, headers=headers, max_bytes=max_bytes, total_timeout=timeout)


class DiscoveryError(Exception):
    pass


async def discover(client: httpx.AsyncClient, pool, url: str, interactive: bool = True) -> dict:
    """R2.20: find the feed behind a feed URL or a site URL."""
    try:
        url = normalize_url(url)
    except InvalidUrl:
        raise DiscoveryError(NO_FEED_MESSAGE) from None

    async def inspect(target: str) -> tuple[Download, dict | None]:
        headers = {"User-Agent": BROWSER_UA, "Accept": FEED_ACCEPT.replace("*/*;q=0.2", "text/html;q=0.5, */*;q=0.2")}
        result = await download(client, target, headers=headers, total_timeout=15)
        log.info("discover %s -> %s (%d bytes)", target, result.error or result.status, len(result.body))
        if not result.ok or not result.body:
            return result, None
        page = await pool.run(
            "parse_page", {"body": result.body, "base_url": result.url, "content_type": result.content_type},
            limit=10, interactive=interactive,
        )  # fmt: skip
        return result, page

    def found(target: str, result: Download, page: dict) -> dict:
        address = result.permanent_url or target
        try:
            address = normalize_url(address)
        except InvalidUrl:
            address = target
        return {"feed_address": address, "title": page["title"], "site_link": page.get("site_link") or ""}

    result, page = await inspect(url)
    if page and page["is_feed"]:
        return found(url, result, page)
    candidates = []
    if page:
        candidates.extend(page["feed_links"][:1])  # first match in document order
    base = site_root(result.url if result.ok else url)
    candidates.extend(base + path for path in PROBE_PATHS)
    for candidate in candidates:
        if urlsplit(candidate).scheme not in ("http", "https"):
            continue
        result, page = await inspect(candidate)
        if page and page["is_feed"]:
            return found(candidate, result, page)
    raise DiscoveryError(NO_FEED_MESSAGE)
