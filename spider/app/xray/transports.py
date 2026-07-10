"""Transport settings builders.

These produce the exact `streamSettings` blocks Xray expects. Subscription
builders later read the SAME field values back out, so the client link always
matches the server config (no duplicated/hand-built URIs).
"""
from __future__ import annotations

import json
from typing import Any

from app.users.models import Inbound


def build_xhttp_stream(inbound: Inbound) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "mode": inbound.xhttp_mode or "auto",
        "xPaddingBytes": inbound.xhttp_x_padding_bytes or "100-1000",
        "scMaxEachPostBytes": inbound.xhttp_sc_max_each_post_bytes or "1000000-2000000",
        "scMaxConcurrentPosts": inbound.xhttp_sc_max_concurrent_posts or 100,
    }
    # Merge any extra JSON the operator supplied.
    if inbound.xhttp_extra and inbound.xhttp_extra.strip():
        try:
            extra = json.loads(inbound.xhttp_extra)
            if isinstance(extra, dict):
                settings.update(extra)
        except json.JSONDecodeError:
            pass
    return {
        "network": "xhttp",
        "security": "none",
        "xhttpSettings": settings,
    }


def build_ws_stream(inbound: Inbound) -> dict[str, Any]:
    headers = {}
    if inbound.ws_host:
        headers["Host"] = inbound.ws_host
    return {
        "network": "ws",
        "security": "none",
        "wsSettings": {
            "path": inbound.transport_path or "/",
            "headers": headers,
        },
    }


def build_stream_settings(inbound: Inbound) -> dict[str, Any]:
    """Dispatch on inbound.network."""
    if inbound.network == "xhttp":
        return build_xhttp_stream(inbound)
    if inbound.network == "ws":
        return build_ws_stream(inbound)
    # raw tcp / none
    return {"network": "tcp", "security": "none"}
