"""State module - all runtime state, locks, and state persistence.

Recreated after the flat-rewrite removed the package. Every consumer
(main, services.xray_service, routers.web, tests) imports from `core.state`.
Single source of truth for shared mutable state - no duplicate dicts.

`shared.py` re-exports the relay-relevant subset from here so legacy
`from shared import ...` statements keep working without forking state.
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
GROUPS: Dict = {}
IP_POOL: List = []
IP_BLACKLIST: Set = set()
USER_IP_MAP: Dict = defaultdict(set)  # user_id -> set of IPs used
# ── Domains ──────────────────────────────────────────────────────────────
# Domain Manager: multiple domains, exactly one active. WS/TLS configs use the
# active domain for address/host/sni; Reality keeps its own external_domain.
DOMAINS: Dict = {}  # domain_id -> {domain, description, is_active, created_at, updated_at}
ACTIVE_DOMAIN: str = ""  # cache of the currently active domain (kept in sync)

# ── Live connection / session tracking (real IP-limit enforcement) ────────
# connections: config_uuid -> list of session dicts:
#   {ip, inbound_id, protocol, first_seen, last_seen}
# A session is added when a tunnel opens and removed when it closes. The
# number of DISTINCT ips is compared against the user's concurrent_connections
# (IP limit) before accepting a new tunnel.
# sessions live in memory only (rebuilt on restart from Xray API if available);
# user IP-limit state is not required to survive restart.
_SESSIONS: Dict = defaultdict(list)  # config_uuid -> [session, ...]
_SESSIONS_LOCK = asyncio.Lock()


def get_active_domain() -> str:
    """Return the active domain (used by WS/TLS config generation)."""
    global ACTIVE_DOMAIN
    if ACTIVE_DOMAIN:
        return ACTIVE_DOMAIN
    for d in DOMAINS.values():
        if d.get("is_active"):
            ACTIVE_DOMAIN = d["domain"]
            return ACTIVE_DOMAIN
    return ""


def set_active_domain(domain_id: str) -> bool:
    """Make exactly one domain active. Returns True on success."""
    global ACTIVE_DOMAIN
    if domain_id not in DOMAINS:
        return False
    for did, d in DOMAINS.items():
        d["is_active"] = (did == domain_id)
        d["updated_at"] = datetime.now().isoformat()
    ACTIVE_DOMAIN = DOMAINS[domain_id]["domain"]
    return True


def add_domain(domain: str, description: str = "") -> str:
    """Add a domain. First domain added becomes active automatically."""
    global ACTIVE_DOMAIN
    did = generate_uuid()
    is_first = len(DOMAINS) == 0
    now = datetime.now().isoformat()
    DOMAINS[did] = {
        "domain": domain,
        "description": description,
        "is_active": is_first,  # first domain is auto-active
        "created_at": now,
        "updated_at": now,
    }
    if is_first:
        ACTIVE_DOMAIN = domain
    return did


def ensure_default_domain(panel_host: str):
    """On first run (no domains), seed the panel's own host as the active domain."""
    if not DOMAINS:
        add_domain(panel_host or "localhost", "Default panel domain")


# ── Session helpers ────────────────────────────────────────────────────────
def active_sessions(config_uuid: str) -> list:
    return _SESSIONS.get(config_uuid, [])


def count_connected_ips(config_uuid: str) -> int:
    """Number of distinct client IPs currently connected for this user."""
    return len({s["ip"] for s in _SESSIONS.get(config_uuid, [])})


def add_session(config_uuid: str, ip: str, inbound_id: str, protocol: str) -> dict:
    now = datetime.now().isoformat()
    sess = {"ip": ip, "inbound_id": inbound_id, "protocol": protocol,
            "first_seen": now, "last_seen": now}
    _SESSIONS[config_uuid].append(sess)
    return sess


def remove_session(config_uuid: str, ip: str, inbound_id: str):
    lst = _SESSIONS.get(config_uuid)
    if not lst:
        return
    for i, s in enumerate(lst):
        if s["ip"] == ip and s["inbound_id"] == inbound_id:
            lst.pop(i)
            break


def session_lock():
    return _SESSIONS_LOCK

# Auth
AUTH = {"password_hash": hash_password(os.environ.get("ADMIN_PASSWORD", "admin"))}
SESSIONS: Dict = {}

# ── Helper Functions ──────────────────────────────────────────────────────
def generate_uuid() -> str:
    """Return a STANDARD dashed UUID4 (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).

    Xray/VLESS client ids MUST be standard UUIDs. We never use the 32-char
    hex form (uuid4().hex / token_hex) because it does not match the UUID
    format Xray expects in its client `id` field, which breaks connections.
    """
    import uuid as _uuid
    return str(_uuid.uuid4())

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

# ── Uptime and helpers ──────────────────────────────────────────────────────
def uptime() -> str:
    import time
    secs = int(time.time() - stats["start_time"])
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def find_user_by_uuid(user_uuid: str) -> "tuple[str | None, dict | None]":
    """Resolve a user by subscription uuid. Returns (user_id, user) or (None, None)."""
    for uid, u in USERS.items():
        if u.get("uuid") == user_uuid:
            return uid, u
    return None, None

def find_user_by_config_uuid(config_uuid: str) -> "tuple[str | None, dict | None]":
    """Resolve a user by their VLESS config_uuid. The WS tunnel path is
    /ws/{config_uuid} (from the generated link), so the tunnel must match on
    config_uuid, NOT the subscription uuid. Returns (user_id, user)."""
    for uid, u in USERS.items():
        if u.get("config_uuid") == config_uuid:
            return uid, u
    return None, None

def find_user_by_username(username: str) -> "tuple[str | None, dict | None]":
    for uid, u in USERS.items():
        if u.get("username") == username:
            return uid, u
    return None, None

def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)

def parse_size_to_bytes(value: float, unit: str) -> int:
    unit = unit.upper()
    if unit == "GB": return int(value * 1024 ** 3)
    if unit == "MB": return int(value * 1024 ** 2)
    if unit == "KB": return int(value * 1024)
    return int(value)

# ── Logging Helpers ────────────────────────────────────────────────────────
def log_activity(kind: str, message: str, level: str = "info"):
    activity_logs.append({
        "kind": kind,
        "level": level,
        "message": message,
        "time": datetime.now(IRAN_TZ).isoformat(),
    })

# ── Path Index Management ──────────────────────────────────────────────────
def _rebuild_path_index():
    PATH_INDEX.clear()
    for uid, u in USERS.items():
        path = (u.get("path") or "").strip().lstrip("/")
        if path.startswith("ws/"):
            path = path[3:]
        config_uuid = u.get("config_uuid") or uid
        if path:
            PATH_INDEX[path] = config_uuid
    for lid, link in LINKS.items():
        link_path = (link.get("path") or "").strip().lstrip("/")
        if link_path.startswith("ws/"):
            link_path = link_path[3:]
        if link_path:
            PATH_INDEX[link_path] = lid
    for uid, u in USERS.items():
        config_uuid = u.get("config_uuid") or uid
        PATH_INDEX[config_uuid] = config_uuid

def is_link_allowed(link: dict | None) -> bool:
    if link is None:
        return False
    if not link.get("active", True):
        return False
    exp = link.get("expires_at")
    if exp:
        try:
            return datetime.now() <= datetime.fromisoformat(exp)
        except Exception:
            return False
    lb = link.get("limit_bytes", 0)
    if lb > 0 and link.get("used_bytes", 0) >= lb:
        return False
    return True

def _migrate_user_links():
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

# ── Persistence ────────────────────────────────────────────────────────────
async def load_state():
    """Load state from JSON file."""
    global LINKS, SUBS, USERS, SETTINGS, GROUPS, INBOUNDS, IP_POOL, IP_BLACKLIST, DOMAINS, ACTIVE_DOMAIN
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
            DOMAINS.clear()
            DOMAINS.update(data.get("domains", {}))
            # Rebuild active-domain cache.
            global ACTIVE_DOMAIN
            ACTIVE_DOMAIN = ""
            for d in DOMAINS.values():
                if d.get("is_active"):
                    ACTIVE_DOMAIN = d["domain"]
                    break
            # Seed the panel's own host as the default active domain if none
            # exist (runs on every load_state so the active domain is always
            # available even if the startup lifespan was skipped, e.g. TestClient).
            ensure_default_domain(get_host())
    except Exception as e:
        print(f"Could not load state: {e}")
    # Backfill a stable `uuid` for every user (subscription identifier).
    # Old records may lack it; derive deterministically and persist on next save.
    # ALSO migrate any legacy 32-char hex config_uuid (no dashes) to a standard
    # dashed UUID4 — Xray client `id` fields require the dashed format, otherwise
    # the generated link's UUID never matches a real Xray client (no connection).
    import re as _re
    _HEX32 = _re.compile(r"^[0-9a-fA-F]{32}$")
    for uid, u in USERS.items():
        if not u.get("uuid"):
            u["uuid"] = str(__import__("uuid").uuid4())
        elif _HEX32.match(str(u.get("uuid", ""))):
            # Legacy 32-hex subscription uuid -> standard dashed UUID.
            u["uuid"] = str(__import__("uuid").uuid4())
        cu = u.get("config_uuid", "")
        if _HEX32.match(str(cu)):
            u["config_uuid"] = str(__import__("uuid").uuid4())
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
                "domains": dict(DOMAINS),
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
