"""News endpoint — latest Iran news, text-only, served in a scrollable box.

Fetches an RSS feed (Google News "Iran" search, with a BBC Persian fallback),
parses the items with the stdlib XML parser, strips HTML from the description,
and returns the most recent items. Results are cached in-process for a short
TTL so opening the News panel doesn't hammer the upstream feed.

No external dependencies — only the Python standard library.
"""
from __future__ import annotations

import email.utils
import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.security import get_current_admin
from app.users.models import AdminUser

router = APIRouter(prefix="/api/news", tags=["news"])

# Sources tried in order. The first that returns parseable XML wins.
# Google News search gives the freshest Iran coverage; BBC Persian is the
# reliable fallback.
_FEEDS = (
    "https://news.google.com/rss/search?q=Iran&hl=en-US&gl=US&ceid=US:en",
    "https://feeds.bbci.co.uk/persian/rss.xml",
)

_CACHE_TTL = 600  # seconds (10 minutes)
_cache: dict[str, tuple[float, list[dict]]] = {}


@dataclass
class _Strip(HTMLParser):
    """Accumulate text, ignoring tags and collapsing whitespace."""

    text = ""

    def handle_data(self, data):
        self.text += data + " "

    def get(self) -> str:
        return re.sub(r"\s+", " ", self.text).strip()


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    p = _Strip()
    try:
        p.feed(raw)
    except Exception:
        # fallback: crude tag removal
        return re.sub(r"<[^>]+>", "", raw or "")
    return html.unescape(p.get())


def _text(el, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.iter("item"):
        title = _text(item, "title")
        link = _text(item, "link")
        desc = _strip_html(_text(item, "description"))
        pub = _text(item, "pubDate")
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if not title:
            continue
        # Prefer the bare headline; Google News titles are "Headline - Source".
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        # Sort key: parse RFC-822 date to epoch (fallback to 0 / string).
        ts = 0.0
        try:
            dt = email.utils.parsedate_to_datetime(pub)
            if dt is not None:
                ts = dt.timestamp()
        except Exception:
            ts = 0.0
        items.append(
            {
                "title": title,
                "text": desc or title,
                "link": link,
                "source": source,
                "published": pub,
                "_ts": ts,
            }
        )
    # Newest first (robust against weekday-prefix lexical ordering).
    items.sort(key=lambda it: it.get("_ts", 0), reverse=True)
    for it in items:
        it.pop("_ts", None)
    return items


def _fetch(query: str, limit: int) -> list[dict]:
    q = query.strip() or "Iran"
    cache_key = f"{q}:{limit}"
    now = time.time()
    cached = _CACHE_TTL and _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    if q.lower() != "iran":
        feeds = [
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(q)
            + "&hl=en-US&gl=US&ceid=US:en",
            *_FEEDS,
        ]
    else:
        feeds = list(_FEEDS)

    last_err = None
    for url in feeds:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "SpiderPanel/1.0 (+news)"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
            items = _parse_feed(data)[:limit]
            if items:
                _cache[cache_key] = (now, items)
                return items
        except Exception as e:  # network / parse failure -> try next source
            last_err = str(e)
            continue
    # All sources failed: return a friendly empty result (never crash the panel).
    return []


@router.get("")
async def get_news(
    _: AdminUser = Depends(get_current_admin),
    query: str = Query("Iran", description="Search term, defaults to 'Iran'"),
    limit: int = Query(8, ge=1, le=20),
):
    try:
        items = _fetch(query, limit)
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={"items": [], "query": query, "ok": False, "error": str(e)},
        )
    return {"items": items, "query": query, "ok": True, "count": len(items)}
