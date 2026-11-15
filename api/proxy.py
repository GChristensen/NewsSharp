"""The image proxy (api/proxy.py), item 14, and the frame check, R9.2. Both only move bytes or
inspect headers; nothing is decoded in the web process (R14.9)."""
import hashlib
import hmac
import logging
import os
import time
from urllib.parse import urlsplit

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse

from feeds.util import image_signature
from reading.stories import image_secret

from . import net
from .http import ApiError, endpoint, error_response

log = logging.getLogger("web.proxy")

IMAGE_LIMIT = 20 * 1024 * 1024
MEDIA_LIMIT = 200 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
CACHE_DIR = settings.DATA_DIR / "image_cache"


def _headers(response: HttpResponse, content_type: str) -> HttpResponse:
    response["Cache-Control"] = "private, max-age=604800"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'"
    if "svg" in content_type:
        response["Content-Disposition"] = "attachment"
    return response


def _cache_paths(url: str):
    key = hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()
    return CACHE_DIR / f"{key}.bin", CACHE_DIR / f"{key}.type"


def _cache_read(url: str):
    data_path, type_path = _cache_paths(url)
    try:
        data, content_type = data_path.read_bytes(), type_path.read_text(encoding="ascii")
        os.utime(data_path)
        return data, content_type
    except OSError:
        return None


def _cache_write(url: str, data: bytes, content_type: str) -> None:
    """R14.7: least-recently-used eviction by file time."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data_path, type_path = _cache_paths(url)
        data_path.write_bytes(data)
        type_path.write_text(content_type, encoding="ascii")
        files = sorted(CACHE_DIR.glob("*.bin"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        limit = settings.IMAGE_CACHE_MB * 1024 * 1024
        for path in files:
            if total <= limit:
                break
            total -= path.stat().st_size
            path.unlink(missing_ok=True)
            path.with_suffix(".type").unlink(missing_ok=True)
    except OSError as e:
        log.info("image cache write failed: %s", e)


async def image_proxy(request):
    """GET /img?u=<url>&sig=<sig> (R14.5)."""
    user = await request.auser()
    if not user.is_authenticated:
        return error_response(401, "unauthenticated", "Log in to continue.")
    url, signature = request.GET.get("u", ""), request.GET.get("sig", "")
    secret = await sync_to_async(image_secret, thread_sensitive=False)()
    if not url or not hmac.compare_digest(image_signature(url, secret), signature):
        return error_response(403, "bad_signature", "Invalid signature.")
    cache_on = settings.IMAGE_CACHE_MB > 0
    if cache_on and "Range" not in request.headers:
        cached = await sync_to_async(_cache_read, thread_sensitive=False)(url)
        if cached:
            return _headers(HttpResponse(cached[0], content_type=cached[1]), cached[1])
    parts = urlsplit(url)
    headers = {"User-Agent": USER_AGENT, "Referer": f"{parts.scheme}://{parts.netloc}/", "Accept": "*/*"}
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    try:
        upstream, _ = await net.open_stream(url, headers)
    except net.Blocked as e:
        return error_response(403, "blocked", str(e))
    except (httpx.HTTPError, OSError):
        return error_response(502, "upstream", "The image could not be loaded.")
    status = upstream.status_code
    content_type = upstream.headers.get("content-type", "").split(";")[0].strip().lower()
    if status == 404:
        await upstream.aclose()
        return error_response(404, "not_found", "Not found.")
    if status not in (200, 206):
        await upstream.aclose()
        return error_response(502, "upstream", f"Upstream answered {status}.")
    if not content_type.startswith(("image/", "video/", "audio/")):
        await upstream.aclose()
        return error_response(415, "unsupported_type", "Only images, video and audio are proxied.")
    is_image = content_type.startswith("image/")
    limit = IMAGE_LIMIT if is_image else MEDIA_LIMIT
    declared = upstream.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        await upstream.aclose()
        return error_response(413, "too_large", "The file is too large.")

    if cache_on and is_image and status == 200:
        body = bytearray()
        try:
            async for chunk in upstream.aiter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    return error_response(413, "too_large", "The file is too large.")
        except httpx.HTTPError:
            return error_response(502, "upstream", "The image could not be loaded.")
        finally:
            await upstream.aclose()
        await sync_to_async(_cache_write, thread_sensitive=False)(url, bytes(body), content_type)
        return _headers(HttpResponse(bytes(body), content_type=content_type), content_type)

    async def stream():
        sent = 0
        try:
            async for chunk in upstream.aiter_bytes():
                sent += len(chunk)
                if sent > limit:
                    break
                yield chunk
        except httpx.HTTPError:
            pass
        finally:
            await upstream.aclose()

    response = StreamingHttpResponse(stream(), status=status, content_type=content_type)
    for name in ("Content-Range", "Accept-Ranges", "Content-Length"):
        if name in upstream.headers and "content-encoding" not in upstream.headers:
            response[name] = upstream.headers[name]
    return _headers(response, content_type)


_frame_cache: dict[str, tuple[float, dict]] = {}


async def _frameable(request, data):
    """R9.2: may this URL be shown in an iframe? Headers only are inspected; cached per host for 24 h."""
    url = request.GET.get("url", "")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ApiError(422, "invalid", "Invalid URL.")
    if parts.scheme == "http" and request.is_secure():
        return {"frameable": False, "reason": "insecure page"}
    key = f"{parts.scheme}://{parts.netloc}"
    cached = _frame_cache.get(key)
    if cached and time.time() - cached[0] < 86400:
        return cached[1]
    result = {"frameable": True, "reason": ""}
    try:
        response, _ = await net.open_stream(url, {"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
        try:
            options = response.headers.get("x-frame-options", "").lower()
            policy = " ".join(response.headers.get_list("content-security-policy")).lower()
            if response.status_code >= 400:
                result = {"frameable": False, "reason": f"status {response.status_code}"}
            elif "deny" in options or "sameorigin" in options:
                result = {"frameable": False, "reason": "X-Frame-Options"}
            elif "frame-ancestors" in policy:
                sources = policy.split("frame-ancestors", 1)[1].split(";", 1)[0].split()
                if not any(source in ("*", "https:", "http:") for source in sources):
                    result = {"frameable": False, "reason": "Content-Security-Policy"}
        finally:
            await response.aclose()
    except net.Blocked as e:
        result = {"frameable": False, "reason": str(e)}
    except (httpx.HTTPError, OSError):
        result = {"frameable": False, "reason": "request failed"}
    if len(_frame_cache) > 2000:
        _frame_cache.clear()
    _frame_cache[key] = (time.time(), result)
    return result


frameable_view = endpoint(GET=_frameable)
