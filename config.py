"""
Configuration module - all settings, environment variables, constants.
No FastAPI, no state - pure configuration.
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
    """Get public host from env or config."""
    return os.environ.get("RAILWAY_PUBLIC_DOMAIN", CONFIG["host"])

# ── Default Settings (3x-ui style) ────────────────────────────────────────
SETTINGS = {
    "websocket_mode": True,
    "xhttp_mode": True,
    "default_connection_mode": "ws",  # ws, xhttp, tcp
    "max_ip_per_user": 3,
    "bandwidth_limit_mbps": 100,
    "live_monitoring": True,
    "auto_ip_rotation": False,
    "security_token": os.environ.get("SECURITY_TOKEN") or __import__("secrets").token_urlsafe(16),
    # Custom backgrounds (uploaded by admin)
    "bg_login": "",
    "bg_dashboard": "",
    "bg_sub": "",
    # Panel audio (uploaded by admin)
    "panel_audio": "",
    "panel_audio_enabled": False,
    # Reality defaults (3x-ui style)
    "reality": {
        "port": 1234,
        "dest": "is1-ssl.mzstatic.com:443",
        "sni": "is1-ssl.mzstatic.com",
        "public_key": "",
        "private_key": "",
        "short_id": "5a3ff5a13d",
        "spiderx": "/",
        "fingerprint": "chrome",
        "external_domain": "",
        "external_port": 443,
    },
    # XHTTP settings (3x-ui style)
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