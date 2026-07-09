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

from config import (
    IRAN_TZ, SETTINGS, CONFIG, get_host,
    XRAY_BINARY_PATH, XRAY_CONFIG_PATH, XRAY_ASSETS_DIR, XRAY_LOG_DIR,
    hash_password,
)
import aiofiles

logger = logging.getLogger("xray_service")

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
async def generate_reality_keys() -> Tuple[str, str, str]:
    """Generate Reality x25519 key pair using Xray binary.
    Returns (private_key_b64, public_key_b64, short_id).
    """
    result = await run_cmd([str(XRAY_BINARY_PATH), "x25519"])
    if result["code"] != 0:
        raise RuntimeError(f"x25519 generation failed: {result['stderr']}")
    
    output = result["stdout"]
    private_key = ""
    public_key = ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("PrivateKey:"):
            private_key = line.split(":", 1)[1].strip()
        elif line.startswith("PublicKey:"):
            public_key = line.split(":", 1)[1].strip()
    
    if not private_key or not public_key:
        raise RuntimeError(f"Failed to parse x25519 output: {output}")
    
    short_id = secrets.token_hex(5)[:10]
    return private_key, public_key, short_id

def resolve_reality_settings(inbound: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve Reality settings for an inbound, using cache if available."""
    inbound_id = inbound.get("id", "default")
    
    if inbound_id in _reality_settings_cache:
        return _reality_settings_cache[inbound_id]
    
    rs = inbound.get("reality_settings", {})
    protocol = inbound.get("protocol", "reality")
    
    if not rs.get("sni") and not rs.get("serverNames"):
        rs["sni"] = "is1-ssl.mzstatic.com"
        rs["serverNames"] = ["is1-ssl.mzstatic.com"]
    
    rs.setdefault("dest", "is1-ssl.mzstatic.com:443")
    rs.setdefault("spiderx", "/")
    rs.setdefault("fingerprint", "chrome")
    rs.setdefault("short_id", secrets.token_hex(5)[:10])
    
    _reality_settings_cache[inbound_id] = rs
    return rs

def persist_reality_keys(inbound_id: str, private_key: str, public_key: str, short_id: str):
    """Persist Reality keys to inbound config and cache."""
    from state import INBOUNDS, INBOUNDS_LOCK, save_state
    
    if inbound_id in INBOUNDS:
        rs = INBOUNDS[inbound_id].setdefault("reality_settings", {})
        rs["private_key"] = private_key
        rs["public_key"] = public_key
        rs["short_id"] = short_id
        
        _reality_settings_cache[inbound_id] = rs
        asyncio.create_task(save_state())

# ── VLESS Link Generation ────────────────────────────────────────────────────
def generate_vless_link(uuid: str, remark: str = "Spider", inbound_id: str | None = None, user: dict | None = None, protocol: str | None = None) -> str:
    """Generate a VLESS share-link strictly based on the real Xray inbound config.

    The link reflects:
    - external domain & external port (public facing values)
    - network (ws, xhttp, grpc, etc.)
    - security (tls, reality) and all required reality params (pbk, sid, spx)
    - transport-specific fields (path, mode, serviceName, extra)
    - fingerprint, sni, alpn where appropriate

    If `user` is provided, user-specific overrides for transport/sni are respected
    but the inbound remains the primary source of truth.
    """
    import json
    from urllib.parse import quote

    # Resolve inbound: use provided id, otherwise fall back to a deterministic default (first inbound)
    inbound = None
    if inbound_id:
        inbound = INBOUNDS.get(inbound_id)
    if not inbound:
        inbound = next(iter(INBOUNDS.values())) if INBOUNDS else {}

    # Resolve host and port (public values) - NEVER use internal port
    host = inbound.get("external_domain") or inbound.get("domain") or SETTINGS.get("domain") or get_host()
    port = inbound.get("external_port", 443)
    network = inbound.get("network", "ws")
    security = inbound.get("security", "tls")
    sni = inbound.get("sni") or host
    fp = inbound.get("fingerprint", "chrome")

    # Allow user-specific overrides for transport/sni if provided
    if user:
        if user.get("transport_type"):
            network = user["transport_type"]
        if user.get("sni"):
            sni = user["sni"]

    params: dict[str, str] = {
        "encryption": "none",
        "security": security,
        "type": network,
        "host": host,
        "sni": sni,
        "fp": fp,
    }

    # Transport specific handling
    if network == "ws":
        ws_path = inbound.get("ws_settings", {}).get("path", f"/ws/{uuid}")
        params["path"] = ws_path
        params["alpn"] = "http/1.1"
    elif network == "xhttp":
        xh = inbound.get("xhttp_settings", {})
        params["mode"] = xh.get("mode", "auto")
        params["path"] = xh.get("path", "/")
        params["alpn"] = "h2,http/1.1"
        # Extra settings (excluding the ones already used)
        extra_dict = {k: v for k, v in xh.items() if k not in ("mode", "path")}
        if extra_dict:
            params["extra"] = quote(json.dumps(extra_dict, separators=(",", ":")))
    elif network == "grpc":
        params["serviceName"] = inbound.get("grpc_settings", {}).get("serviceName", "")
    elif network == "tcp":
        params["alpn"] = "h2,http/1.1"

    # Reality protocol – ensure required fields exist
    if security == "reality":
        rs = inbound.get("reality_settings", {})
        # Required fields validation – if missing, raise clear error
        missing = []
        # Use private_key/public_key from inbound config (persisted)
        if not rs.get("private_key") and not rs.get("public_key"):
            missing.append("pbk")
        if not rs.get("short_id"):
            missing.append("sid")
        if not rs.get("sni") and not rs.get("serverNames"):
            missing.append("sni")
        if missing:
            raise ValueError(f"Reality configuration incomplete: missing {', '.join(missing)}")
        # Use the persisted keys for the link
        params["pbk"] = rs.get("public_key", "")  # PublicKey goes to pbk
        params["sid"] = rs.get("short_id", "")
        params["spx"] = quote(rs.get("spiderx", "/"))

    # Build query string, skipping empty values
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v)
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark)}"

# ── Xray Config Generation ─────────────────────────────────────────────────
def generate_xray_server_config(inbound_id: Optional[str] = None) -> Dict[str, Any]:
    """Generate complete Xray server config.json from inbounds."""
    from state import INBOUNDS
    
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

def _add_inbound_to_xray(cfg: Dict, ib: Dict, iid: str, host: str):
    protocol = ib.get("protocol", "vless")
    port = int(ib.get("port", 443))
    network = ib.get("network", "ws")
    security = ib.get("security", "tls")
    domain = ib.get("domain", host)
    sni_val = ib.get("sni", domain)
    fingerprint = ib.get("fingerprint", "chrome")
    rs = ib.get("reality_settings", {}) if protocol == "reality" else {}
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
        client_count = 10
        clients = []
        for i in range(client_count):
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
    
    if protocol == "reality":
        inbound_obj["streamSettings"] = {
            "network": network if network in ("tcp", "xhttp", "grpc") else "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": rs.get("dest", "is1-ssl.mzstatic.com:443"),
                "xver": 0,
                "serverNames": [rs.get("sni", "is1-ssl.mzstatic.com")],
                "privateKey": rs.get("private_key", ""),
                "shortIds": [rs.get("short_id", "5a3ff5a13d")],
                "spiderX": rs.get("spiderx", "/"),
            }
        }
        if network == "xhttp":
            inbound_obj["streamSettings"]["xhttpSettings"] = {
                "path": xh_settings.get("path", "/"),
                "host": xh_settings.get("host", domain),
                "mode": xh_settings.get("mode", "auto"),
                "xPaddingBytes": xh_settings.get("xPaddingBytes", "100-1000"),
                "scMaxEachPostBytes": xh_settings.get("scMaxEachPostBytes", "1000000"),
                "scMaxBufferedPosts": xh_settings.get("scMaxBufferedPosts", 30),
                "scStreamUpServerSecs": xh_settings.get("scStreamUpServerSecs", "20-80"),
            }
    elif security == "tls":
        inbound_obj["streamSettings"] = {
            "network": network,
            "security": "tls",
            "tlsSettings": {
                "certificates": [{
                    "certificateFile": "/etc/xray/cert.pem",
                    "keyFile": "/etc/xray/key.pem"
                }]
            }
        }
        if network == "ws":
            inbound_obj["streamSettings"]["wsSettings"] = {
                "path": ws_settings.get("path", "/"),
                "headers": {"Host": ws_settings.get("host", domain)}
            }
        elif network == "grpc":
            inbound_obj["streamSettings"]["grpcSettings"] = {
                "serviceName": grpc_settings.get("serviceName", "")
            }
        elif network == "xhttp":
            inbound_obj["streamSettings"]["xhttpSettings"] = {
                "path": xh_settings.get("path", "/"),
                "host": xh_settings.get("host", domain),
                "mode": xh_settings.get("mode", "auto"),
                "xPaddingBytes": xh_settings.get("xPaddingBytes", "100-1000"),
                "scMaxEachPostBytes": xh_settings.get("scMaxEachPostBytes", "1000000"),
            }
    else:
        inbound_obj["streamSettings"] = {"network": network}
        if network == "ws":
            inbound_obj["streamSettings"]["wsSettings"] = {"path": ws_settings.get("path", "/")}
    
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
        result = await run_cmd([str(XRAY_BINARY_PATH), "-test", "-config", tmp_path])
        return result["code"] == 0, result["stderr"]
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
            return {"ok": False, "error": f"Invalid config: {error}"}
        
        if not await write_xray_config(config):
            return {"ok": False, "error": "Failed to write config"}
        
        XRAY_LOG_DIR.mkdir(parents=True, exist_ok=True)
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
from state import INBOUNDS, INBOUNDS_LOCK, save_state