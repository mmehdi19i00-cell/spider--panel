"""
Xray Service - Xray configuration generation, Reality key management, and process control.
No FastAPI dependencies - pure service layer.
"""
import asyncio
import base64
import json
import logging
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Defaults shared between the VLESS link generator and the Xray server config
# so the link's `extra` JSON exactly mirrors xray's xhttpSettings.
XHTTP_DEFAULTS: Dict[str, Any] = {
    "path": "/",
    "mode": "auto",
    "xPaddingBytes": "100-1000",
    "scMaxEachPostBytes": "1000000",
}

from config import (
    IRAN_TZ, SETTINGS, CONFIG, get_host,
    XRAY_BINARY_PATH, XRAY_CONFIG_PATH, XRAY_ASSETS_DIR, XRAY_LOG_DIR,
    hash_password,
)
from core.state import generate_uuid, INBOUNDS, INBOUNDS_LOCK, save_state, USERS, get_active_domain
import aiofiles

logger = logging.getLogger("xray_service")


class RealityIncompleteError(Exception):
    """Raised when a Reality inbound is missing required fields (pbk/sid/sni).

    The caller is expected to convert this into a JSON 4xx response, never let
    it bubble up as an uncaught 500.
    """

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__("Reality configuration incomplete: missing " + ", ".join(self.missing))

# ── Global Xray process ────────────────────────────────────────────────────
_xray_process: Optional[asyncio.subprocess.Process] = None
_xray_lock = asyncio.Lock()

# ── Runtime cache for Reality settings ─────────────────────────────────────
_reality_settings_cache: Dict[str, Dict[str, Any]] = {}

# ── Helper: run shell command ──────────────────────────────────────────────
async def run_cmd(cmd: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
    """Run a command and return {code, stdout, stderr}."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        return {
            "code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
        }
    except Exception as e:
        return {"code": -1, "stdout": "", "stderr": str(e)}

# ── Architecture detection ─────────────────────────────────────────────────
def detect_arch() -> str:
    import platform
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return "64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    if arch in ("armv7l", "armhf"):
        return "arm"
    return "64"

XRAY_VERSION = os.environ.get("XRAY_VERSION", "latest")

def get_xray_download_url(version: str, arch: str) -> str:
    return f"https://github.com/XTLS/Xray-core/releases/{version}/download/Xray-linux-{arch}.zip"

# ── Xray Installation ──────────────────────────────────────────────────────
async def is_xray_installed() -> bool:
    return XRAY_BINARY_PATH.exists() and os.access(XRAY_BINARY_PATH, os.X_OK)

async def get_xray_version() -> Optional[str]:
    if not await is_xray_installed():
        return None
    result = await run_cmd([str(XRAY_BINARY_PATH), "version"])
    if result["code"] == 0 and result["stdout"]:
        return result["stdout"].splitlines()[0].split()[1]
    return None

async def download_file(url: str, dest: Path) -> bool:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False
                async with aiofiles.open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        await f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return False

async def extract_xray(zip_path: Path, extract_dir: Path) -> bool:
    try:
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        return True
    except Exception as e:
        logger.error(f"Extract failed: {e}")
        return False

async def install_geo_assets(extract_dir: Path) -> bool:
    try:
        XRAY_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        for asset in ["geoip.dat", "geosite.dat"]:
            src = extract_dir / asset
            dst = XRAY_ASSETS_DIR / asset
            if src.exists():
                shutil.copy2(src, dst)
                logger.info(f"Installed {asset} to {dst}")
            else:
                logger.warning(f"{asset} not found in release archive")
        return True
    except Exception as e:
        logger.error(f"Failed to install geo assets: {e}")
        return False

async def install_xray_core(force: bool = False, version: str = XRAY_VERSION) -> bool:
    global XRAY_VERSION
    if version:
        XRAY_VERSION = version
    
    async with _xray_lock:
        if not force and await is_xray_installed():
            current = await get_xray_version()
            if current and current == XRAY_VERSION:
                logger.info(f"Xray v{XRAY_VERSION} already installed")
                return True
            logger.info(f"Version mismatch: installed={current}, required={XRAY_VERSION}")
        
        arch = detect_arch()
        url = get_xray_download_url(XRAY_VERSION, arch)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            zip_path = tmpdir / f"Xray-linux-{arch}.zip"
            extract_dir = tmpdir / "extracted"
            extract_dir.mkdir()
            
            logger.info(f"Downloading Xray from {url}")
            if not await download_file(url, zip_path):
                return False
            
            if not await extract_xray(zip_path, extract_dir):
                return False
            
            src_bin = extract_dir / "xray"
            if not src_bin.exists():
                logger.error("Xray binary not found after extraction")
                return False
            
            XRAY_BINARY_PATH.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_bin, XRAY_BINARY_PATH)
                XRAY_BINARY_PATH.chmod(0o755)
                logger.info(f"Xray installed to {XRAY_BINARY_PATH}")
            except PermissionError:
                logger.warning("Permission denied, trying with sudo...")
                result = await run_cmd(["sudo", "cp", str(src_bin), str(XRAY_BINARY_PATH)])
                if result["code"] != 0:
                    logger.error(f"Sudo copy failed: {result['stderr']}")
                    return False
                result = await run_cmd(["sudo", "chmod", "755", str(XRAY_BINARY_PATH)])
                if result["code"] != 0:
                    logger.error(f"Sudo chmod failed: {result['stderr']}")
                    return False
            
            await install_geo_assets(extract_dir)
            
            if not await is_xray_installed():
                logger.error("Installation verification failed")
                return False
            
            installed_version = await get_xray_version()
            logger.info(f"Xray v{installed_version} installed successfully")
            return True

# ── Reality Key Generation ─────────────────────────────────────────────────
def _looks_like_key(s: str) -> bool:
    """True if s is a plausible x25519 key.

    Xray 26.x emits base64url keys (~43 chars, e.g.
    'mJs9OOZeOVaU5DZ4bzjR6KdlRc_nXRv2gWNnviVU43Y'), but older builds or other
    tooling may emit 64-char hex. Accept either so the parser never fails on a
    valid key just because the character class differs.
    """
    s = s.strip()
    if not s:
        return False
    if len(s) in (43, 64) and all(c in "0123456789abcdefABCDEF" for c in s):
        return True  # hex
    # base64url: A-Z a-z 0-9 - _ , length 40-64
    return (40 <= len(s) <= 64) and all(c in
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in s)


async def generate_reality_keypair() -> Dict[str, str]:
    """Generate a Reality x25519 key pair using the installed Xray binary.

    Runs `xray x25519` and parses PrivateKey/PublicKey (and Hash32 if present)
    from stdout. Never uses Python crypto or random bytes for the keypair.

    Xray 26.x emits (note the `Password (PublicKey):` alias and `Hash32:`):
        PrivateKey: <base64url private>
        Password (PublicKey): <base64url public>
        Hash32: <base64url hash>

    Older builds may emit `Public key:` on its own line, or two-line output.
    Returns {"private_key", "public_key", "hash32"}.
    """
    if not await is_xray_installed():
        raise RuntimeError("Xray binary not installed; cannot generate Reality keys")
    result = await run_cmd([str(XRAY_BINARY_PATH), "x25519"])
    if result["code"] != 0:
        raise RuntimeError(f"x25519 generation failed: {result['stderr'] or result['stdout']}")

    output = result["stdout"]
    parsed: Dict[str, str] = {"private_key": "", "public_key": "", "hash32": ""}
    pending_label = None  # label seen on a previous line (two-line Xray format)

    def _classify(label: str):
        l = label.strip().lower().rstrip(":")
        if "private" in l:
            return "private"
        if "public" in l or "password" in l:
            return "public"
        if "hash32" in l or "hash" in l:
            return "hash"
        return None

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        # "Label: value" on one line — value may be empty (two-line form).
        if ":" in line:
            head, _, tail = line.partition(":")
            kind = _classify(head)
            tail = tail.strip()
            if kind and tail:
                parsed[kind + "_key" if kind != "hash" else "hash32"] = tail
                continue
            if kind and not tail:
                pending_label = kind
                continue
        # Bare value line following a label (two-line Xray format).
        if pending_label and _looks_like_key(line):
            key = pending_label + "_key" if pending_label != "hash" else "hash32"
            parsed[key] = line
            pending_label = None

    # Backstop: if something still missing, scan for base64url/hex tokens.
    if not (parsed["private_key"] and parsed["public_key"]):
        import re
        tokens = [t for t in re.findall(r"[A-Za-z0-9_\-]{40,64}", output) if _looks_like_key(t)]
        if parsed["private_key"] and len(tokens) >= 1:
            parsed["public_key"] = tokens[-1]
        elif len(tokens) >= 2:
            parsed["private_key"], parsed["public_key"] = tokens[0], tokens[1]

    if not parsed["private_key"] or not parsed["public_key"]:
        raise RuntimeError(f"Failed to parse x25519 output: {output}")
    return parsed


async def generate_reality_keys() -> Tuple[str, str, str]:
    """Back-compat wrapper: (private_key, public_key, short_id).

    The keypair is produced by `xray x25519`. The short_id is a real random
    hex string (NOT a keypair, so randomness is acceptable) but it is always
    persisted via ensure_reality_keys — never regenerated on restart.
    """
    keys = await generate_reality_keypair()
    private_key, public_key = keys["private_key"], keys["public_key"]
    short_id = secrets.token_hex(8)  # 16 hex chars (valid Reality shortId)
    return private_key, public_key, short_id


async def ensure_reality_keys(inbound_id: str) -> Dict[str, Any]:
    """Ensure a Reality inbound has real, persisted keys (idempotent).

    Reality settings are stored with snake_case keys (private_key, public_key,
    short_ids, server_names, sni, spiderx, dest) — matching the dashboard UI
    and the Xray config we generate. Backward-compatible reads of the old
    singular/camelCase keys are kept so existing state files still load.
    """
    inbound = INBOUNDS.get(inbound_id)
    if inbound is None:
        raise RealityIncompleteError(["inbound"])

    rs = inbound.setdefault("reality_settings", {})

    # Normalize old key shapes into the canonical snake_case form.
    if rs.get("private_key") is None and rs.get("privateKey"):
        rs["private_key"] = rs.pop("privateKey")
    if rs.get("public_key") is None and rs.get("publicKey"):
        rs["public_key"] = rs.pop("publicKey")
    if rs.get("short_ids") is None:
        if rs.get("short_id"):
            rs["short_ids"] = rs["short_id"]
        elif rs.get("shortIds"):
            rs["short_ids"] = rs["shortIds"][0] if isinstance(rs["shortIds"], list) else rs["shortIds"]
    if rs.get("server_names") is None and rs.get("serverNames"):
        rs["server_names"] = rs["serverNames"]
    if rs.get("spiderx") is None and rs.get("spiderX"):
        rs["spiderx"] = rs["spiderX"]

    if not rs.get("private_key") or not rs.get("public_key"):
        keys = await generate_reality_keypair()
        rs["private_key"] = keys["private_key"]
        rs["public_key"] = keys["public_key"]
        if not rs.get("short_ids"):
            rs["short_ids"] = secrets.token_hex(8)

    # sni / server_names are real, operator-provided facts. We must not invent them.
    if not rs.get("sni") and not rs.get("server_names"):
        raise RealityIncompleteError(["sni"])
    if not rs.get("server_names"):
        rs["server_names"] = [rs["sni"]]
    if not rs.get("sni"):
        rs["sni"] = rs["server_names"][0]

    rs.setdefault("dest", f"{rs['sni']}:443")
    rs.setdefault("spiderx", "/")
    rs.setdefault("fingerprint", "chrome")

    _reality_settings_cache[inbound_id] = rs

    # Persist so a container restart reuses the SAME keys (persistence rule).
    async with INBOUNDS_LOCK:
        INBOUNDS[inbound_id]["reality_settings"] = rs
    await save_state()
    return rs


def get_reality_export(inbound_id: str) -> Dict[str, str]:
    """Return the public Reality parameters for link generation.

    Reads the persisted reality_settings from INBOUNDS (source of truth).
    Raises RealityIncompleteError if pbk/sid/sni are missing — so the caller
    returns a clear JSON error instead of a broken link.
    """
    rs = INBOUNDS.get(inbound_id, {}).get("reality_settings", {})
    missing = []
    if not rs.get("public_key"):
        missing.append("pbk")
    if not rs.get("short_ids"):
        missing.append("sid")
    if not rs.get("sni") and not rs.get("server_names"):
        missing.append("sni")
    if missing:
        raise RealityIncompleteError(missing)
    return {
        "pbk": rs["public_key"],
        "sid": rs["short_ids"][0] if isinstance(rs["short_ids"], list) else rs["short_ids"],
        "sni": rs.get("sni") or (rs["server_names"][0] if rs.get("server_names") else ""),
        "spx": rs.get("spiderx", "/"),
        "fp": rs.get("fingerprint", "chrome"),
    }

# ── VLESS Link Generation ────────────────────────────────────────────────────
def generate_vless_link(
    uuid: str,
    remark: str = "Spider",
    inbound_id: str | None = None,
    user: dict | None = None,
    protocol: str | None = None,
) -> str:
    """Generate a VLESS share-link STRICTLY from the real Xray inbound config.

    Source of truth is the inbound stored in INBOUNDS (populated by
    generate_xray_server_config from the active Xray inbound). Nothing is
    hardcoded and nothing is randomly generated:

      - host / port  -> external_domain / external_port (public endpoint)
      - network       -> inbound network (ws / xhttp / grpc / tcp)
      - security      -> inbound security (tls / reality)
      - sni / fp      -> from inbound (Reality: from reality_settings)
      - pbk / sid / spx -> from the REAL xray-generated Reality keys
      - xhttp extra   -> encoded JSON from inbound xhttp_settings

    Raises RealityIncompleteError if a Reality inbound is missing pbk/sid/sni,
    so callers return a clean JSON error rather than a broken link.
    """
    import json
    import re as _re
    from urllib.parse import quote

    # ── Resolve inbound (the single source of truth) ──────────────────────
    if inbound_id:
        inbound = INBOUNDS.get(inbound_id)
    else:
        inbound = next(iter(INBOUNDS.values())) if INBOUNDS else None
    if not inbound:
        raise RuntimeError("No Xray inbound configured; cannot generate VLESS link")
    security = inbound.get("security", "tls")
    network = inbound.get("network", "ws")

    # ── Validation (بخش هشتم): refuse to build a broken config ────────────
    # UUID must be a valid (dashed) VLESS uuid.
    if not _re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", str(uuid or "")):
        raise ValueError(f"Invalid VLESS uuid: {uuid!r}")
    # Inbound must have an id.
    if not inbound.get("id") and not inbound_id:
        raise RuntimeError("Inbound has no id; cannot generate VLESS link")
    # IP-limit must be a non-negative integer.
    ip_limit = int(user.get("concurrent_connections", 2) if user else 2)
    if ip_limit < 0:
        raise ValueError(f"Invalid IP limit: {ip_limit}")
    # WS/TLS requires an active domain (Domain Manager) to point the link at.
    if security != "reality" and network in ("ws", "xhttp"):
        if not get_active_domain():
            raise RuntimeError("No active domain configured; cannot generate WS/TLS link")

    # ── Public endpoint (NEVER internal listen port) ──────────────────────
    # For WS/TLS the address/host/sni MUST come from the ACTIVE domain manager
    # domain (so changing the active domain re-points all ws configs on the
    # next generate). Reality keeps its own external_domain/serverNames.
    if security == "reality":
        host = inbound.get("external_domain") or inbound.get("domain") or SETTINGS.get("domain") or get_host()
    else:
        host = get_active_domain() or inbound.get("external_domain") or inbound.get("domain") or SETTINGS.get("domain") or get_host()
    port = inbound.get("external_port", 443)

    # ── Validation: external endpoint must exist ──────────────────────────
    if not host:
        raise RuntimeError("External domain is not configured; cannot generate VLESS link")
    if not port:
        raise RuntimeError("External port is not configured; cannot generate VLESS link")

    # ── Base params (no hardcoded security/transport) ─────────────────────
    params: dict[str, str] = {
        "encryption": "none",
        "security": security,
        "type": network,
        "fp": inbound.get("fingerprint", "chrome"),
    }

    # ── Reality: pull REAL keys; fail loudly if incomplete ─────────────────
    if security == "reality":
        rid = inbound_id or _first_inbound_id()
        if not rid:
            raise RuntimeError("No inbound id available for Reality link generation")
        rk = get_reality_export(rid)
        params["sni"] = rk["sni"]
        params["pbk"] = rk["pbk"]
        params["sid"] = rk["sid"]
        params["spx"] = rk["spx"]  # raw value; final builder url-encodes it
        # fingerprint for Reality comes from reality_settings when present
        params["fp"] = rk["fp"]
    else:
        # Non-reality: for WS/TLS the active Domain-Manager domain is the SNI
        # (so changing the active domain re-points every ws config). Reality
        # uses its own external_domain/serverNames and never reaches here.
        sni = host if (network in ("ws", "xhttp")) else (inbound.get("sni") or host)
        params["sni"] = sni

    # ── Transport-specific params, strictly from inbound ───────────────────
    if network == "ws":
        ws = inbound.get("ws_settings", {})
        # For WS/TLS the Host header must equal the active Domain-Manager
        # domain (same as address/sni). Falls back to the inbound's own
        # external_domain only if no active domain is configured.
        params["host"] = host if (host == get_active_domain() or not inbound.get("external_domain")) else (inbound.get("external_domain") or host)
        # ws path must EXACTLY match the server-side wsSettings.path.
        # Shared per-inbound path /ws/{inbound id}; Xray authenticates the user
        # by the VLESS client uuid, so a single path serves all users.
        params["path"] = ws.get("path") or f"/ws/{inbound_id or iid}"
        params["alpn"] = "http/1.1"
    elif network == "xhttp":
        xh = inbound.get("xhttp_settings", {})
        # Apply the same defaults the SERVER config uses, so the link's `extra`
        # exactly mirrors xray's xhttpSettings (no mismatch that breaks xhttp).
        xh = {**XHTTP_DEFAULTS, **xh}
        params["path"] = xh.get("path", "/")
        params["mode"] = xh.get("mode", "auto")
        # Encode the remaining xhttp settings as `extra` (URL-encoded JSON),
        # mirroring what Xray uses. `path`/`mode` are separate params.
        extra_dict = {k: v for k, v in xh.items() if k not in ("path", "mode")}
        if extra_dict:
            # Store RAW JSON here; the final builder URL-encodes it exactly once.
            params["extra"] = json.dumps(extra_dict, separators=(",", ":"))
    elif network == "grpc":
        grpc = inbound.get("grpc_settings", {})
        params["serviceName"] = grpc.get("serviceName", "")
    elif network == "tcp":
        pass

    # ── Build the link, skipping empty values ──────────────────────────────
    # safe="" forces encoding of '/', so spx=/ and path=/ become %2F (VLESS spec).
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v)
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark, safe='')}"


def _first_inbound_id() -> str | None:
    return next(iter(INBOUNDS), None)

# ── Xray Config Generation ─────────────────────────────────────────────────
def generate_xray_server_config(inbound_id: Optional[str] = None) -> Dict[str, Any]:
    """Generate complete Xray server config.json from inbounds."""
    from core.state import INBOUNDS
    
    host = get_host()
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": []
        }
    }
    
    if inbound_id:
        inbound = INBOUNDS.get(inbound_id)
        if inbound:
            _add_inbound_to_xray(config, inbound, inbound_id, host)
    else:
        for iid, ib in INBOUNDS.items():
            _add_inbound_to_xray(config, ib, iid, host)
    
    return config

def _ws_path_for_inbound(iid: str, ws_settings: dict) -> str:
    """ws path that EXACTLY matches the client link.

    The path is shared per-inbound (/ws/{inbound id}). Xray authenticates the
    user via the VLESS client uuid inside the frame, NOT via the path, so a
    single shared inbound path works for every user and — critically — always
    matches the generated subscription link. A per-user path here would only
    match the FIRST linked user's link and silently break everyone else.
    """
    if ws_settings.get("path"):
        return ws_settings["path"]
    return f"/ws/{iid}"


def _add_inbound_to_xray(cfg: Dict, ib: Dict, iid: str, host: str):
    protocol = ib.get("protocol", "vless")
    port = int(ib.get("port", 443))
    network = ib.get("network", "ws")
    security = ib.get("security", "tls")
    domain = ib.get("domain", host)
    sni_val = ib.get("sni", domain)
    fingerprint = ib.get("fingerprint", "chrome")
    rs = ib.get("reality_settings", {}) if security == "reality" else {}
    ws_settings = ib.get("ws_settings", {})
    xh_settings = ib.get("xhttp_settings", {})
    grpc_settings = ib.get("grpc_settings", {})
    
    inbound_obj = {
        "tag": f"inbound-{iid}",
        "port": port,
        "protocol": protocol,
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {}
    }
    
    if protocol in ("vless", "vmess", "trojan"):
        # Clients MUST be the real users' config_uuid — the exact UUID that
        # generate_vless_link() puts into the subscription link. Generating
        # random UUIDs here would make every link connect to a non-existent
        # client (the "fake uuid / config not working" bug).
        clients = []
        seen = set()
        for u in USERS.values():
            if u.get("inbound_id") != iid:
                continue
            uid = u.get("config_uuid") or u.get("user_id")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            client = {"id": uid}
            if protocol == "vless":
                client["flow"] = ""
            elif protocol == "vmess":
                client["alterId"] = 0
            elif protocol == "trojan":
                client["password"] = u.get("config_uuid") or secrets.token_urlsafe(16)
            clients.append(client)
        # Fallback: if no users are linked yet, still emit one valid client so
        # the inbound can start and a manually-created link works.
        if not clients:
            uid = generate_uuid()
            client = {"id": uid}
            if protocol == "vless":
                client["flow"] = ""
            elif protocol == "vmess":
                client["alterId"] = 0
            elif protocol == "trojan":
                client["password"] = secrets.token_urlsafe(16)
            clients.append(client)
        inbound_obj["settings"]["clients"] = clients
    
    if security == "reality":
        # Use the REAL persisted Reality keys. Never fabricate a shortId or sni.
        if not rs.get("private_key") or not rs.get("public_key"):
            raise RealityIncompleteError(["pbk"])
        if not rs.get("short_ids"):
            raise RealityIncompleteError(["sid"])
        sni_for_reality = rs.get("sni") or (rs.get("server_names") or [None])[0]
        if not sni_for_reality:
            raise RealityIncompleteError(["sni"])
        server_names = rs.get("server_names") or [sni_for_reality]
        short_ids_raw = rs.get("short_ids")
        short_ids_list = short_ids_raw if isinstance(short_ids_raw, list) else [short_ids_raw]
        inbound_obj["streamSettings"] = {
            "network": network if network in ("tcp", "xhttp", "grpc", "ws") else "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": rs.get("dest", f"{sni_for_reality}:443"),
                "xver": 0,
                "serverNames": server_names,
                "privateKey": rs["private_key"],
                "shortIds": short_ids_list,
                "spiderX": rs.get("spiderx", "/"),
            }
        }
        if network == "xhttp":
            merged_xh = {**XHTTP_DEFAULTS, **xh_settings}
            inbound_obj["streamSettings"]["xhttpSettings"] = {
                "path": merged_xh.get("path", "/"),
                "mode": merged_xh.get("mode", "auto"),
                "xPaddingBytes": merged_xh.get("xPaddingBytes", "100-1000"),
                "scMaxEachPostBytes": merged_xh.get("scMaxEachPostBytes", "1000000"),
            }
    elif security == "tls":
        # Behind a TLS-terminating reverse proxy (Railway / Cloudflare / Nginx),
        # the proxy already does TLS and forwards a *plain* WebSocket to this
        # process. Xray must therefore listen with security:"none" on its
        # internal port — otherwise it expects a TLS handshake it never
        # receives (the client sees wss at the edge, plain ws internally).
        # The generated VLESS link still advertises security=tls (client->edge).
        xray_security = "none" if network in ("ws", "xhttp", "grpc") else "tls"
        stream = {
            "network": network,
            "security": xray_security,
        }
        # Only reference certificate files if they actually exist. Xray auto-
        # generates a self-signed cert at runtime when tlsSettings is omitted,
        # so hardcoding /etc/xray/cert.pem (which doesn't exist on Railway)
        # makes the config invalid and Xray refuses to start.
        tls_settings = ib.get("tls_settings") or {}
        cert_file = tls_settings.get("certificateFile")
        key_file = tls_settings.get("keyFile")
        if xray_security == "tls" and cert_file and key_file and Path(cert_file).exists() and Path(key_file).exists():
            stream["tlsSettings"] = {"certificates": [{"certificateFile": cert_file, "keyFile": key_file}]}
        inbound_obj["streamSettings"] = stream
        if network == "ws":
            inbound_obj["streamSettings"]["wsSettings"] = {
                "path": _ws_path_for_inbound(iid, ws_settings),
                "headers": {"Host": ws_settings.get("host", domain)}
            }
        elif network == "grpc":
            inbound_obj["streamSettings"]["grpcSettings"] = {
                "serviceName": grpc_settings.get("serviceName", "")
            }
        elif network == "xhttp":
            merged_xh = {**XHTTP_DEFAULTS, **xh_settings}
            # Only emit the keys that appear in the link's `extra` (plus path/mode
            # which are separate link params), so the server xhttpSettings is a
            # 1:1 mirror of the generated subscription link.
            inbound_obj["streamSettings"]["xhttpSettings"] = {
                "path": merged_xh.get("path", "/"),
                "mode": merged_xh.get("mode", "auto"),
                "xPaddingBytes": merged_xh.get("xPaddingBytes", "100-1000"),
                "scMaxEachPostBytes": merged_xh.get("scMaxEachPostBytes", "1000000"),
            }
    else:
        inbound_obj["streamSettings"] = {"network": network}
        if network == "ws":
            inbound_obj["streamSettings"]["wsSettings"] = {"path": _ws_path_for_inbound(iid, ws_settings)}
    
    inbound_obj["sniffing"] = {
        "enabled": True,
        "destOverride": ["http", "tls", "quic"]
    }
    
    cfg["inbounds"].append(inbound_obj)

# ── Config Validation & Writing ────────────────────────────────────────────
async def validate_xray_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    if not await is_xray_installed():
        return False, "Xray not installed"
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        tmp_path = f.name
    
    try:
        # Xray prints validation errors to STDOUT (not stderr), so include both.
        result = await run_cmd([str(XRAY_BINARY_PATH), "-test", "-config", tmp_path])
        detail = (result["stderr"] or result["stdout"]).strip() or "unknown validation error"
        return result["code"] == 0, detail
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

async def write_xray_config(config: Dict[str, Any]) -> bool:
    try:
        XRAY_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = XRAY_CONFIG_PATH.with_suffix(".tmp")
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(config, indent=2, ensure_ascii=False))
        tmp_path.replace(XRAY_CONFIG_PATH)
        logger.info(f"Xray config written to {XRAY_CONFIG_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to write Xray config: {e}")
        return False

# ── Xray Process Control ───────────────────────────────────────────────────
async def start_xray(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _xray_process
    
    async with _xray_lock:
        if _xray_process and _xray_process.returncode is None:
            return {"ok": True, "message": "Xray already running", "pid": _xray_process.pid}
        
        if not await is_xray_installed():
            if not await install_xray_core():
                return {"ok": False, "error": "Failed to install Xray Core"}
        
        if config is None:
            config = generate_xray_server_config()
        
        valid, error = await validate_xray_config(config)
        if not valid:
            # Write the bad config to disk so it can be inspected manually:
            #   cat /app/xray-config/config.json
            #   /app/xray-core/xray -test -config /app/xray-config/config.json
            try:
                await write_xray_config(config)
                logger.critical(f"Xray config validation FAILED. Config written to {XRAY_CONFIG_PATH} for inspection.")
            except Exception:
                pass
            logger.critical(f"Xray config validation FAILED:\n{error}")
            return {"ok": False, "error": f"Invalid config: {error}"}
        
        if not await write_xray_config(config):
            return {"ok": False, "error": "Failed to write config"}
        
        try:
            XRAY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            # Log dir may be read-only in some environments (e.g. sandbox).
            # Xray can still run; we just won't capture its logs to disk.
            logger.warning(f"Cannot create Xray log dir {XRAY_LOG_DIR}: {e}")
        log_file = XRAY_LOG_DIR / "xray.log"
        
        try:
            _xray_process = await asyncio.create_subprocess_exec(
                str(XRAY_BINARY_PATH),
                "run",
                "-config", str(XRAY_CONFIG_PATH),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            asyncio.create_task(_read_xray_logs(_xray_process.stdout, "stdout"))
            asyncio.create_task(_read_xray_logs(_xray_process.stderr, "stderr"))
            
            logger.info(f"Xray started with PID {_xray_process.pid}")
            return {"ok": True, "pid": _xray_process.pid, "message": "Xray started"}
        except Exception as e:
            logger.error(f"Failed to start Xray: {e}")
            return {"ok": False, "error": str(e)}

async def stop_xray() -> Dict[str, Any]:
    global _xray_process
    async with _xray_lock:
        if not _xray_process or _xray_process.returncode is not None:
            return {"ok": True, "message": "Xray not running"}
        
        _xray_process.terminate()
        try:
            await asyncio.wait_for(_xray_process.wait(), timeout=10)
        except asyncio.TimeoutError:
            _xray_process.kill()
            await _xray_process.wait()
        
        pid = _xray_process.pid
        _xray_process = None
        logger.info(f"Xray stopped (PID: {pid})")
        return {"ok": True, "message": f"Xray stopped (PID: {pid})"}

async def restart_xray(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    await stop_xray()
    return await start_xray(config)

async def _read_xray_logs(stream: asyncio.StreamReader, prefix: str):
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            logger.info(f"Xray [{prefix}]: {line.decode().strip()}")
    except Exception as e:
        logger.error(f"Xray log reader error: {e}")

async def get_xray_status() -> Dict[str, Any]:
    global _xray_process
    if _xray_process and _xray_process.returncode is None:
        return {"running": True, "pid": _xray_process.pid}
    return {"running": False, "pid": None}

# ── Import state for INBOUNDS ────────────────────────────────────────────────
from core.state import INBOUNDS, INBOUNDS_LOCK, save_state