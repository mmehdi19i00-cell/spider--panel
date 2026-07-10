"""Port allocation and Railway networking detection for Spider Panel.

Centralises everything about *which* port Xray listens on internally, how that
relates to the FastAPI web port, and how the public (Railway TCP-proxy) port is
discovered — so the three never collide and the generated subscription always
points at the correct external endpoint.

Key invariant:
  * FastAPI (web)  -> CONFIG["port"]  (the value Railway injects as $PORT)
  * Xray WS/TLS    -> a private internal port (loopback-only, never public)
  * Xray Reality   -> a private internal port that Railway forwards via a TCP
                      proxy to a *different* public port.

The internal Xray ports MUST NOT equal the web port, and must be unique per
inbound. We allocate them here and persist them on the inbound as ``port``.
"""
import os
import socket
from typing import Dict, List, Optional, Set

# Web (FastAPI) port — Railway injects $PORT for the HTTP process.
WEB_PORT_ENV = int(os.environ.get("PORT", 8080))

# Configurable internal Xray ports (all distinct from the web port).
# Override via env if you need fixed values; otherwise they are auto-allocated.
XRAY_WS_PORT = int(os.environ.get("XRAY_WS_PORT", 8443))
XRAY_REALITY_PORT = int(os.environ.get("XRAY_REALITY_PORT", 1234))
# Fallback base the allocator scans from when no fixed port is configured.
XRAY_PORT_POOL_BASE = int(os.environ.get("XRAY_PORT_POOL_BASE", 12000))


def detect_railway_tcp_proxies() -> Dict[int, int]:
    """Map internal container port -> public Railway TCP-proxy port.

    Railway exposes TCP proxies as env vars of the form:
        RAILWAY_TCP_PROXY_PORT_<internal>_<public>
    e.g. RAILWAY_TCP_PROXY_PORT_1234_29362  (container 1234 -> public 29362).
    Returns {1234: 29362, ...}. Empty dict when not on Railway / no TCP proxy.
    """
    proxies: Dict[int, int] = {}
    for key, val in os.environ.items():
        if not key.startswith("RAILWAY_TCP_PROXY_PORT_"):
            continue
        # RAILWAY_TCP_PROXY_PORT_<internal>_<public>
        parts = key[len("RAILWAY_TCP_PROXY_PORT_"):].split("_")
        if len(parts) == 2:
            try:
                internal, public = int(parts[0]), int(parts[1])
                proxies[internal] = public
            except ValueError:
                continue
    return proxies


# Discovered once at import; cheap and stable for a container lifetime.
RAILWAY_TCP_PROXIES: Dict[int, int] = detect_railway_tcp_proxies()


def public_port_for_internal(internal_port: int, configured_external: Optional[int] = None) -> int:
    """Resolve the PUBLIC port a client should connect to.

    Priority:
      1. An explicitly configured external_port on the inbound.
      2. The Railway TCP-proxy public port mapped to this internal port.
      3. 443 (standard TLS / HTTPS edge — used by WS/TLS behind Railway HTTPS).
    """
    if configured_external:
        return int(configured_external)
    if internal_port in RAILWAY_TCP_PROXIES:
        return RAILWAY_TCP_PROXIES[internal_port]
    # WS/TLS rides the HTTPS edge (443) when no explicit proxy exists.
    return 443


def _is_port_free(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex((host, port)) != 0
    except OSError:
        return True


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """True if nothing is listening on (host, port) right now."""
    return _is_port_free(host, port)


def allocate_internal_port(
    purpose: str,
    used: Set[int],
    fixed: Optional[int] = None,
) -> int:
    """Return a free internal Xray port that never collides with the web port
    or any already-used internal port.

    ``purpose`` is one of "ws" / "reality" / "grpc" and selects the configured
    fixed default when present; otherwise we scan upward from the pool base.
    """
    candidates: List[int] = []
    if fixed is not None:
        candidates.append(fixed)
    if purpose == "ws":
        candidates.append(XRAY_WS_PORT)
    elif purpose == "reality":
        candidates.append(XRAY_REALITY_PORT)
    candidates.append(XRAY_PORT_POOL_BASE)

    for c in candidates:
        if c == WEB_PORT_ENV:
            continue
        if c in used:
            continue
        if is_port_available(c):
            return c

    # Scan upward from the pool base for a genuinely free port.
    port = XRAY_PORT_POOL_BASE
    while port < XRAY_PORT_POOL_BASE + 2000:
        if port == WEB_PORT_ENV or port in used:
            port += 1
            continue
        if is_port_available(port):
            return port
        port += 1
    # Extremely unlikely fallback.
    return XRAY_PORT_POOL_BASE + 1


def collect_used_internal_ports(inbounds: Dict) -> Set[int]:
    """All internal Xray ports currently claimed by inbounds + the web port."""
    used: Set[int] = {WEB_PORT_ENV}
    for ib in inbounds.values():
        p = ib.get("port")
        if isinstance(p, int) and p > 0:
            used.add(p)
    return used
