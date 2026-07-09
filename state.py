"""
State module - all runtime state, locks, and state persistence.
No FastAPI, no main.py dependencies.
"""
import asyncio
import json
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Import config for paths and settings
from config import (
    DATA_DIR, DATA_FILE, IRAN_TZ, SETTINGS, CONFIG,
    hash_password, get_host,
)

# ── Locks ──────────────────────────────────────────────────────────────────
SAVE_LOCK = asyncio.Lock()
LINKS_LOCK = asyncio.Lock()
PATH_INDEX_LOCK = asyncio.Lock()
SUBS_LOCK = asyncio.Lock()
USERS_LOCK = asyncio.Lock()
SETTINGS_LOCK = asyncio.Lock()
INBOUNDS_LOCK = asyncio.Lock()
GROUPS_LOCK = asyncio.Lock()
IP_POOL_LOCK = asyncio.Lock()
IP_BLACKLIST_LOCK = asyncio.Lock()
USER_IP_MAP_LOCK = asyncio.Lock()
SESSIONS_LOCK = asyncio.Lock()

# ── In-memory State ────────────────────────────────────────────────────────
connections: Dict = {}
stats = {
    "total_bytes": 0,
    "total_requests": 0,
    "total_errors": 0,
    "start_time": 0,
}
error_logs: deque = deque(maxlen=50)
activity_logs: deque = deque(maxlen=200)
hourly_traffic: Dict = defaultdict(int)

LINKS: Dict = {}
PATH_INDEX: Dict = {}  # random_path -> uuid
SUBS: Dict = {}
USERS: Dict = {}
INBOUNDS: Dict = {}  # inbound_id -> {name, protocol, port, network, security, domain, sni, external_port, fingerprint, reality_settings, xhttp_settings, created_at}
GROUPS: Dict = {}  # group_id -> {name, description, user_ids, ip_pool, rules, created_at}
IP_POOL: List = []  # list of {ip, status, latency_ms, location, assigned_user, last_check}
IP_BLACKLIST: Set = set()
USER_IP_MAP: Dict = defaultdict(set)  # user_id -> set of IPs used

# Auth
AUTH = {"password_hash": hash_password(os.environ.get("ADMIN_PASSWORD", "admin"))}
SESSIONS: Dict = {}

# ── Helper Functions ──────────────────────────────────────────────────────
def generate_uuid() -> str:
    import secrets
    return secrets.token_hex(16)

def generate_short_id() -> str:
    import secrets
    return secrets.token_hex(6)

def generate_random_path(prefix: str = "", length: int = 6) -> str:
    import secrets
    if prefix:
        return f"/{prefix}-{secrets.token_hex(length)}"
    return f"/{secrets.token_hex(length)}"

def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)

# ── Logging Helpers ────────────────────────────────────────────────────────
def log_activity(kind: str, message: str, level: str = "info"):
    """Log an activity event."""
    activity_logs.append({
        "kind": kind,
        "level": level,
        "message": message,
        "time": datetime.now(IRAN_TZ).isoformat(),
    })

# ── Path Index Management ──────────────────────────────────────────────────
def _rebuild_path_index():
    """Rebuild PATH_INDEX from all USERS and LINKS with stored paths."""
    PATH_INDEX.clear()
    # From users — store clean path (no /ws/ prefix)
    for uid, u in USERS.items():
        path = (u.get("path") or "").strip().lstrip("/")
        # Strip any old /ws/ prefix from stored paths
        if path.startswith("ws/"):
            path = path[3:]
        config_uuid = u.get("config_uuid") or uid
        if path:
            PATH_INDEX[path] = config_uuid
    # From legacy links
    for lid, link in LINKS.items():
        link_path = (link.get("path") or "").strip().lstrip("/")
        if link_path.startswith("ws/"):
            link_path = link_path[3:]
        if link_path:
            PATH_INDEX[link_path] = lid
    # Backward compat: index by config_uuid for old /ws/{uuid} clients
    for uid, u in USERS.items():
        config_uuid = u.get("config_uuid") or uid
        PATH_INDEX[config_uuid] = config_uuid
    print(f"PATH_INDEX rebuilt: {len(PATH_INDEX)} entries")

def _migrate_user_links():
    """Ensure every user with a config_uuid has a corresponding link in LINKS."""
    created = 0
    for uid, u in USERS.items():
        cuuid = u.get("config_uuid")
        if not cuuid:
            continue
        if cuuid in LINKS:
            continue
        LINKS[cuuid] = {
            "label": u.get("username", uid),
            "limit_bytes": u.get("traffic_limit_bytes", 0),
            "used_bytes": u.get("traffic_used_bytes", 0),
            "created_at": u.get("created_at", datetime.now().isoformat()),
            "active": (u.get("status", "active") == "active"),
            "expires_at": u.get("expire_at"),
            "note": f"لینک کاربر {u.get('username', uid)}",
            "is_default": False,
            "sub_id": None,
            "protocol": u.get("protocol", "vless"),
            "path": (u.get("path") or "").strip().lstrip("/"),
            "user_id": uid,
        }
        created += 1
    if created:
        print(f"_migrate_user_links: created {created} missing links for existing users")

# ── Persistence ────────────────────────────────────────────────────────────
async def load_state():
    """Load state from JSON file."""
    global LINKS, SUBS, USERS, SETTINGS, GROUPS, INBOUNDS, IP_POOL, IP_BLACKLIST
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if DATA_FILE.exists():
            import aiofiles
            async with aiofiles.open(DATA_FILE, "r", encoding="utf-8") as f:
                raw = await f.read()
            data = json.loads(raw)
            LINKS.update(data.get("links", {}))
            SUBS.update(data.get("subs", {}))
            USERS.update(data.get("users", {}))
            if "password_hash" in data:
                AUTH["password_hash"] = data["password_hash"]
            if "saved_secret" in data:
                CONFIG["secret"] = data["saved_secret"]
            if "settings" in data:
                SETTINGS.update(data["settings"])
            GROUPS.update(data.get("groups", {}))
            INBOUNDS.update(data.get("inbounds", {}))
            IP_POOL.clear()
            IP_POOL.extend(data.get("ip_pool", []))
            IP_BLACKLIST.clear()
            IP_BLACKLIST.update(data.get("ip_blacklist", []))
            print(f"State loaded: {len(LINKS)} links, {len(SUBS)} subs, {len(USERS)} users, {len(GROUPS)} groups, {len(IP_POOL)} ips, {len(INBOUNDS)} inbounds")
    except Exception as e:
        print(f"Could not load state: {e}")
    _rebuild_path_index()
    _migrate_user_links()

async def save_state():
    """Save state to JSON file atomically."""
    async with SAVE_LOCK:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "links": dict(LINKS),
                "users": dict(USERS),
                "subs": dict(SUBS),
                "settings": dict(SETTINGS),
                "groups": dict(GROUPS),
                "inbounds": dict(INBOUNDS),
                "ip_pool": list(IP_POOL),
                "ip_blacklist": list(IP_BLACKLIST),
                "password_hash": AUTH["password_hash"],
                "saved_secret": CONFIG["secret"],
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            import aiofiles
            tmp = DATA_FILE.with_suffix(".tmp")
            async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            tmp.replace(DATA_FILE)
        except Exception as e:
            print(f"Could not save state: {e}")