"""Xray validation: config JSON sanity + VLESS URI syntax validation.

`validate_config` runs structural checks (independent of the xray binary so
unit tests work without it). `validate_vless_uri` guarantees every link we
return actually parses back into the expected fields.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from app.xray.builder import validate_config_on_disk


def validate_config(config: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if "inbounds" not in config or not isinstance(config["inbounds"], list):
        errors.append("missing inbounds")
        return False, errors
    for ib in config["inbounds"]:
        if not ib.get("tag"):
            errors.append("inbound missing tag")
        port = ib.get("port")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            errors.append(f"{ib.get('tag')}: invalid port {port}")
        ss = ib.get("streamSettings", {})
        sec = ss.get("security")
        if sec == "reality":
            rs = ss.get("realitySettings")
            if not rs:
                errors.append(f"{ib.get('tag')}: reality missing realitySettings")
            else:
                if not rs.get("privateKey"):
                    errors.append(f"{ib.get('tag')}: reality missing privateKey")
                if not rs.get("serverNames"):
                    errors.append(f"{ib.get('tag')}: reality missing serverNames")
        clients = ib.get("settings", {}).get("clients", [])
        for c in clients:
            if not c.get("id"):
                errors.append(f"{ib.get('tag')}: client missing id(uuid)")
    return (len(errors) == 0, errors)


def validate_vless_uri(uri: str) -> tuple[bool, list[str]]:
    """Validate a generated vless:// URI. Returns (ok, errors)."""
    errors: list[str] = []
    if not uri.startswith("vless://"):
        errors.append("not a vless uri")
        return False, errors
    try:
        parsed = urlparse(uri)
    except ValueError as e:
        errors.append(f"urlparse error: {e}")
        return False, errors

    uuid = parsed.username
    if not uuid or len(uuid) < 32:
        errors.append("missing/invalid uuid")
    host = parsed.hostname
    if not host:
        errors.append("missing host")
    if not parsed.port or not (1 <= (parsed.port or 0) <= 65535):
        errors.append("missing/invalid port")

    qs = parse_qs(parsed.query)
    security = qs.get("security", [""])[0]
    if security == "reality":
        if not qs.get("pbk"):
            errors.append("reality missing pbk")
        if not qs.get("sni"):
            errors.append("reality missing sni")
        if not qs.get("sid"):
            errors.append("reality missing sid")
        if not qs.get("fp"):
            errors.append("reality missing fp")
        if not qs.get("type"):
            errors.append("reality missing type")
    elif security == "tls":
        if not qs.get("sni"):
            errors.append("tls missing sni")
    typ = qs.get("type", [""])[0]
    if typ == "xhttp":
        if not qs.get("path"):
            errors.append("xhttp missing path")
    elif typ == "ws":
        if not qs.get("path"):
            errors.append("ws missing path")

    return (len(errors) == 0, errors)


def validate_config_file(path: str | None = None) -> tuple[bool, str]:
    return validate_config_on_disk(path)
