"""Embedded browser proxy.

The admin "Chrome" tab renders remote sites inside an iframe. Most sites send
``X-Frame-Options`` / ``Content-Security-Policy: frame-ancestors`` which
*forbid* being embedded in a cross-origin iframe, so they show a blank (white)
page. This proxy fetches the page server-side, strips the framing headers, and
serves it from the panel's OWN origin so the iframe accepts it.

Security notes
-------------
* Only http/https targets are allowed.
* SSRF protection: the resolved IP must not be private/loopback/link-local/
  multicast or the cloud metadata address (169.254.169.254).
* Response size is capped so a huge page can't exhaust memory.
* Rewritten ``<base href>`` + absolute URL rewriting keep in-page links and
  assets flowing back through the proxy.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import quote, urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.core.security import get_current_admin
from app.users.models import AdminUser

router = APIRouter(prefix="/api/browser", tags=["browser"])

_MAX_BYTES = 8 * 1024 * 1024  # 8 MB cap
_TIMEOUT = 20.0

# Headers we never forward from the upstream response (framing / encoding /
# trust headers that would break or be unsafe inside our origin).
_DROP_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
    "content-encoding",   # we decode manually
    "content-length",
    "transfer-encoding",
    "connection",
    "set-cookie",         # cookies can't persist cross-origin anyway
    "strict-transport-security",
    "cross-origin-resource-policy",
    "cross-origin-opener-policy",
}

# Absolute http(s) URL producer for rewritten attributes.
_URL_RE = re.compile(
    r"""(?P<pre>(?:src|href|action)\s*=\s*)(?P<q>["'])(?P<url>[^"']+)(?P<post>["'])""",
    re.IGNORECASE,
)


def _is_safe_host(hostname: str) -> bool:
    """Block private / loopback / link-local / metadata addresses (SSRF)."""
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return True  # not an IP literal; DNS resolved + re-checked by httpx below
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return False
    if addr == ipaddress.ip_address("169.254.169.254"):
        return False
    return True


def _check_response_ips(response: httpx.Response) -> None:
    """Best-effort SSRF guard: reject if any resolved IP is internal."""
    for addr in getattr(response, "_ip_addresses", []) or []:
        if isinstance(addr, str):
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                raise httpx.HTTPError("Blocked: target resolves to a private address")


def _proxy_base(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}/api/browser/proxy?url="


def _rewrite_html(html: str, base_url: str, request: Request) -> str:
    """Rewrite absolute URLs to flow through this proxy; inject <base href>."""
    proxy = _proxy_base(request)

    def repl(m):
        pre, q, url, post = m.group("pre", "q", "url", "post")
        if url.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            return m.group(0)
        abs_url = urljoin(base_url, url)
        p = urlparse(abs_url)
        if p.scheme not in ("http", "https"):
            return m.group(0)
        return f"{pre}{q}{proxy}{quote(abs_url, safe='')}{q}{post}"

    out = _URL_RE.sub(repl, html)
    base_tag = f'<base href="{proxy}{quote(base_url, safe="")}">'
    if re.search(r"<head[^>]*>", out, re.IGNORECASE):
        out = re.sub(
            r"(<head[^>]*>)",
            lambda m: m.group(1) + base_tag,
            out,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        out = base_tag + out
    return out


@router.get("/proxy")
async def browser_proxy(
    request: Request,
    url: str = Query(..., description="Fully-qualified http(s) URL to load"),
    _: AdminUser = Depends(get_current_admin),
):
    """Fetch ``url`` server-side and return it from the panel's origin.

    HTML is rewritten so links/assets stay routed through the proxy and the
    framing-blocking headers are stripped, letting the admin iframe render it.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return Response("Only http(s) URLs are supported", status_code=400)
    if not _is_safe_host(parsed.hostname or ""):
        return Response("Blocked: disallowed host", status_code=403)

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SpiderPanelEmbeddedBrowser/1.0)",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, max_redirects=5, verify=True
        ) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                _check_response_ips(resp)
                if resp.status_code >= 400:
                    return Response(f"Upstream returned HTTP {resp.status_code}", status_code=502)
                ctype = (resp.headers.get("content-type") or "text/html").lower()
                data = b""
                async for chunk in resp.aiter_bytes():
                    data += chunk
                    if len(data) > _MAX_BYTES:
                        return Response("Response too large to proxy", status_code=502)
    except httpx.HTTPError as e:
        return Response(f"Failed to load URL: {e}", status_code=502)

    out_headers = {
        k: v for k, v in resp.headers.items() if k.lower() not in _DROP_HEADERS
    }
    out_headers["X-Frame-Options"] = "ALLOWALL"
    out_headers["Content-Security-Policy"] = "frame-ancestors *"

    if "text/html" in ctype:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.decode("latin-1", errors="replace")
        return HTMLResponse(_rewrite_html(text, str(resp.url), request), headers=out_headers)
    return Response(data, media_type=ctype.split(";")[0], headers=out_headers)
