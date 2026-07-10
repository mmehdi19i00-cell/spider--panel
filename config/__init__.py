"""Config package - exports settings and configuration.

Recreated after the flat-rewrite removed the package: every other module
(main, services.xray_service, routers.web, tests) imports from `config`.
Pure configuration only - no FastAPI, no runtime state.
"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Timezone ──────────────────────────────────────────────────────────────
IRAN_TZ = ZoneInfo("Asia/Tehran")

# ── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_FILE = DATA_DIR / "spider_state.json"

XRAY_BINARY_PATH = Path(os.environ.get("XRAY_BINARY_PATH", "/app/xray-core/xray"))
XRAY_CONFIG_PATH = Path(os.environ.get("XRAY_CONFIG_PATH", "/app/xray-config/config.json"))
XRAY_ASSETS_DIR = Path(os.environ.get("XRAY_ASSETS_DIR", "/app/xray-assets"))
XRAY_LOG_DIR = Path(os.environ.get("XRAY_LOG_DIR", "/app/xray-logs"))

# ── Environment / Runtime Config ──────────────────────────────────────────
CONFIG = {
    "port": int(os.environ.get("PORT", 8080)),
    "secret": os.environ.get("SECRET_KEY", "spider-panel-secret-key-v2"),
    "host": os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost"),
}


def get_host() -> str:
    """Get public host from env or config (Railway injects RAILWAY_PUBLIC_DOMAIN at runtime)."""
    return os.environ.get("RAILWAY_PUBLIC_DOMAIN", CONFIG["host"])


# ── Default Settings (admin-tunable defaults) ──────────────────────────────
SETTINGS = {
    "websocket_mode": True,
    "xhttp_mode": True,
    "default_connection_mode": "ws",  # ws, xhttp, tcp
    "max_ip_per_user": 3,
    "bandwidth_limit_mbps": 100,
    "live_monitoring": True,
    "auto_ip_rotation": False,
    "security_token": os.environ.get("SECURITY_TOKEN") or __import__("secrets").token_urlsafe(16),
    "bg_login": "",
    "bg_dashboard": "",
    "bg_sub": "",
    "panel_audio": "",
    "panel_audio_enabled": False,
    "reality": {
        # NOTE: this is only a *suggestion*. The real internal Xray port is
        # allocated at inbound-creation time by services.xray_ports so it never
        # collides with the FastAPI web port or another inbound. Do not treat
        # this literal 1234 as a bind port.
        "port": int(os.environ.get("XRAY_REALITY_PORT", 1234)),
        "dest": "is1-ssl.mzstatic.com:443",
        "sni": "is1-ssl.mzstatic.com",
        "public_key": "",
        "private_key": "",
        "short_id": "",
        "spiderx": "/",
        "fingerprint": "chrome",
        "external_domain": "",
        "external_port": 443,
    },
    "xhttp": {
        "path": "/",
        "host": "",
        "mode": "auto",
        "xPaddingBytes": "100-1000",
        "scMaxEachPostBytes": "1000000",
        "scMaxBufferedPosts": 30,
        "scStreamUpServerSecs": "20-80",
    },
}

# ── Protocol Constants ────────────────────────────────────────────────────
PROTOCOLS = ("vless-ws", "xhttp-packet-up", "xhttp-stream-up", "xhttp-stream-one")
USER_PROTOCOLS = ("vless", "vmess", "trojan", "shadowsocks", "reality")
DEFAULT_PROTOCOL = "vless-ws"

# ── Session/Auth ───────────────────────────────────────────────────────────
SESSION_COOKIE = "spider_session"
SESSION_TTL = 60 * 60 * 24 * 7  # 7 days


def hash_password(pw: str) -> str:
    import hashlib
    return hashlib.sha256(f"{pw}{CONFIG['secret']}".encode()).hexdigest()


# Initial password hash
AUTH = {"password_hash": hash_password(os.environ.get("ADMIN_PASSWORD", "admin"))}

# ── Logging ────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Spider-Gateway")
