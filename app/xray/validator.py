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
    if not config["inbounds"]:
        errors.append("no inbounds defined")
        return False, errors
    for ib in config["inbounds"]:
        if not ib.get("tag"):
            errors.append("inbound missing tag")
        port = ib.get("port")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            errors.append(f"{ib.get('tag')}: invalid port {port}")
        # Privileged ports (1-1023) inside the container conflict with Railway's
        # proxy edge — but that's a deployment concern, not a config-validity
        # bug (xray itself accepts them). The manager/builder enforce the safe
        # xray_inbound_port at runtime.
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
                sids = rs.get("shortIds") or []
                if not any(sids):
                    errors.append(f"{ib.get('tag')}: reality missing shortIds")
        elif sec == "tls":
            certs = (ss.get("tlsSettings") or {}).get("certificates") or ss.get("certificates")
            if not certs:
                errors.append(f"{ib.get('tag')}: tls missing certificates")
        # NOTE: "no clients" and privileged ports are deployment concerns, not
        # config-validity bugs — xray accepts them. The Railway port safety is
        # enforced at runtime via settings.xray_inbound_port / bootstrap.
        clients = (ib.get("settings") or {}).get("clients") or []
        for c in clients:
            if not c.get("id"):
                errors.append(f"{ib.get('tag')}: client missing id(uuid)")
            if c.get("flow") == "xtls-rprx-vision" and sec != "reality":
                errors.append(f"{ib.get('tag')}: xtls-rprx-vision flow only valid with reality")
    # routing + outbounds present
    if "routing" not in config:
        errors.append("missing routing section")
    if "outbounds" not in config or not config["outbounds"]:
        errors.append("missing outbounds")
    else:
        tags = {o.get("tag") for o in config["outbounds"]}
        for rule in config.get("routing", {}).get("rules", []):
            out = rule.get("outboundTag")
            if out and out not in tags:
                errors.append(f"routing rule points to unknown outboundTag '{out}'")
    if "dns" not in config:
        errors.append("missing dns section")
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

    qs = parse_qs(parsed.query, keep_blank_values=True)
    get = lambda k: (qs.get(k) or [""])[0]

    if get("encryption") != "none":
        errors.append("encryption must be none")
    security = get("security")
    if security == "reality":
        for k in ("pbk", "sid", "sni", "fp", "spx"):
            if not get(k):
                errors.append(f"reality missing {k}")
        if get("type") == "xhttp":
            if get("path") != "/":
                errors.append("reality xhttp path must be /")
            if get("mode") != "auto":
                errors.append("reality xhttp mode must be auto")
            # extra must be valid JSON
            try:
                import json
                json.loads(get("extra") or "{}")
            except (ValueError, TypeError):
                errors.append("reality extra is not valid JSON")
        elif get("type") == "ws":
            if not get("path"):
                errors.append("reality ws missing path")
        elif get("type") not in ("tcp",):
            errors.append(f"reality unsupported type {get('type')}")
    elif security == "tls":
        if not get("sni"):
            errors.append("tls missing sni")
        if get("type") == "ws" and not get("path"):
            errors.append("ws missing path")
    elif security == "none":
        if not get("type"):
            errors.append("type required")
    return (len(errors) == 0, errors)


def validate_config_file(path: str | None = None) -> tuple[bool, str]:
    return validate_config_on_disk(path)
