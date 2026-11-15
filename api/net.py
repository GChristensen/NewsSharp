"""Guarded outbound requests of the web process (api/net.py): the image proxy (item 14) and the
frame check (R9.2). The destination is checked on the resolved address used for the connection (R14.6)."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class Blocked(Exception):
    pass


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(10),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=10),
        )
    return _client


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


async def resolve_public(url: str) -> tuple[str, str, dict]:
    """Resolve the URL's host and return (url with the address pinned, host, request extensions).
    Raises Blocked for non-public addresses and refused ports."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Blocked("unsupported URL")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port < 1024 and port not in (80, 443):
        raise Blocked("port not allowed")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise Blocked("host not found") from e
    addresses = [info[4][0] for info in infos]
    if not addresses or not all(_public(a) for a in addresses):
        raise Blocked("address not allowed")
    address = addresses[0]
    host_literal = f"[{address}]" if ":" in address else address
    netloc = host_literal if parts.port is None else f"{host_literal}:{parts.port}"
    pinned = parts._replace(netloc=netloc).geturl()
    extensions = {"sni_hostname": parts.hostname} if parts.scheme == "https" else {}
    return pinned, parts.netloc, extensions


async def open_stream(url: str, headers: dict, max_redirects: int = 3):
    """Open a GET response stream, following up to `max_redirects` redirects with every hop checked.
    Returns (response, final url); the caller must `await response.aclose()`."""
    for _ in range(max_redirects + 1):
        pinned, host, extensions = await resolve_public(url)
        request = client().build_request("GET", pinned, headers={**headers, "Host": host}, extensions=extensions)
        response = await client().send(request, stream=True)
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
            target = urljoin(url, response.headers["location"])
            await response.aclose()
            url = target
            continue
        return response, url
    raise Blocked("too many redirects")
