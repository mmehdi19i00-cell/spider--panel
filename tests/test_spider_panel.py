"""End-to-end + unit tests for Spider Panel.

Run:  pytest -q
Requires deps installed. Uses an isolated temp SQLite DB and a fake xray
binary (the reality key generation falls back to the cryptography lib, which is
byte-identical to `xray x25519`).

These tests encode the invariants from the spec:
  * UUID format
  * Reality keypair correctness (pubkey derives from privatekey)
  * XHTTP + WS config shape
  * Subscription VLESS URI correctness + validation
  * Config JSON validity
  * User expiration transition
  * IP limit enforcement
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

# Make the app importable from repo root
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

from app.core import security  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.database import Base, get_sessionmaker, init_db  # noqa: E402
from app.users import models  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def tmp_db(monkeypatch):
    """Point the app at a temp sqlite file and reset engine + settings."""
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "test.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("DATA_DIR", d)
    monkeypatch.setenv("XRAY_BINARY_PATH", "/nonexistent/xray")  # disable real binary
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass123")
    # mutate the shared settings object in place (modules import the module, not the value)
    import app.core.config as cfgmod

    s = cfgmod.settings
    s.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
    s.DATA_DIR = d
    s.XRAY_BINARY_PATH = "/nonexistent/xray"
    s.ADMIN_PASSWORD = "testpass123"
    # reset cached engine
    import app.database as dbmod

    dbmod._engine = None
    dbmod._sessionmaker = None
    yield d
    dbmod._engine = None
    dbmod._sessionmaker = None


@pytest_asyncio.fixture()
async def session(tmp_db):
    await init_db()
    maker = get_sessionmaker()
    async with maker() as s:
        yield s


# ---------------------------------------------------------------------------
# Tests: template rendering (regression for unhashable type: 'dict')
# ---------------------------------------------------------------------------
def test_login_page_renders(tmp_db):
    """GET /login must render 200, not 500 (Starlette 1.x TemplateResponse)."""
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        maker = get_sessionmaker()
        async with maker() as s:
            await ensure_admin(s)

    asyncio.run(_seed())
    c = TestClient(app)
    r = c.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Login - Spider Panel" in r.text


def test_unauthenticated_api_is_blocked(tmp_db):
    """Regression: no route may serve admin data without a valid token.

    A previous bug put '/' in PUBLIC_PATHS, which made is_public_path() match
    every path and disabled AuthMiddleware entirely, plus several routers defined
    _protected=[Depends(get_current_admin)] but never attached it. The result was
    GET /api/users and /api/inbounds returning 200 with full data to anonymous
    users. This guards against that ever returning.
    """
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        maker = get_sessionmaker()
        async with maker() as s:
            await ensure_admin(s)

    asyncio.run(_seed())
    c = TestClient(app, follow_redirects=False)
    for path in ("/api/users", "/api/inbounds", "/api/domains", "/api/dashboard/stats",
                 "/api/xray/config", "/settings", "/dashboard"):
        r = c.get(path)
        assert r.status_code in (401, 302), f"UNAUTH {path} -> {r.status_code} (must be blocked)"


def test_authenticated_pages_render(tmp_db):
    """Dashboard SPA renders 200; legacy section routes 302-redirect into it."""
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        maker = get_sessionmaker()
        async with maker() as s:
            await ensure_admin(s)

    asyncio.run(_seed())
    c = TestClient(app)
    tok = c.post(
        "/api/auth/token",
        data={"username": "admin", "password": "testpass123"},
    ).json()["access_token"]
    # The SPA stores the token in the spider_token cookie; require_auth reads the
    # cookie (AuthMiddleware validates it). Bearer header alone is not enough for
    # the page routes, so authenticate via the cookie like a real browser does.
    c.cookies.set("spider_token", tok)
    h = {"Accept": "text/html"}

    # The single consolidated console shell.
    r = c.get("/dashboard", headers=h, follow_redirects=False)
    assert r.status_code == 200, f"/dashboard -> {r.status_code}"
    assert "Console" in r.text

    # Legacy per-section URLs redirect INTO the SPA (each opening a tab).
    for path, tab in (
        ("/users", "users"),
        ("/inbounds", "inbounds"),
        ("/domains", "domains"),
        ("/settings", "settings"),
        ("/system", "system"),
        ("/xray", "logs"),
    ):
        r = c.get(path, headers=h, follow_redirects=False)
        assert r.status_code == 302, f"{path} -> {r.status_code}"
        assert r.headers["location"] == f"/dashboard?tab={tab}", r.headers.get("location")

    # Public subscription page (no auth) renders its own template.
    r = c.get("/sub", headers=h, follow_redirects=False)
    assert r.status_code == 200, f"/sub -> {r.status_code}"
    assert "Spider" in r.text

    # Unauthenticated access to the console redirects to /login
    # (verified with a fresh client that has NO session cookie).
    c2 = TestClient(app)
    r2 = c2.get("/dashboard", headers=h, follow_redirects=False)
    assert r2.status_code in (307, 308, 302), r2.status_code
    assert r2.headers["location"] == "/login", r2.headers.get("location")


def test_modal_container_present_for_add_flows(tmp_db):
    """Regression: the console must contain a #modal / #modal-card element,
    otherwise openModal() throws and Add User / Add Inbound crash
    (the originally reported 'can't add user or inbound')."""
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        async with get_sessionmaker()() as s:
            await ensure_admin(s)

    asyncio.run(_seed())
    c = TestClient(app, follow_redirects=False)
    tok = c.post("/api/auth/token", data={"username": "admin", "password": "testpass123"}).json()["access_token"]
    c.cookies.set("spider_token", tok)
    r = c.get("/dashboard", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 200
    assert 'id="modal"' in r.text, "missing #modal container (Add User/Inbound modal would crash)"
    assert 'id="modal-card"' in r.text, "missing #modal-card container"


def test_sub_page_self_contained_modal(tmp_db):
    """The public /sub page is served raw (not via Jinja inheritance), so it
    must carry its own modal container + openModal() helper."""
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        async with get_sessionmaker()() as s:
            await ensure_admin(s)

    asyncio.run(_seed())
    c = TestClient(app, follow_redirects=False)
    r = c.get("/sub", follow_redirects=False)
    assert r.status_code == 200
    assert 'id="modal"' in r.text and 'id="modal-card"' in r.text
    assert "function openModal" in r.text, "/sub must define its own openModal (served raw)"



# ---------------------------------------------------------------------------
# Tests: security / reality
# ---------------------------------------------------------------------------
def test_uuid_format():
    u = security.generate_uuid()
    assert security.is_valid_uuid(u)
    assert len(u) == 36


def test_reality_keypair_valid():
    priv, pub = security.generate_x25519_keypair()
    assert priv and pub
    assert security.verify_reality_keypair(priv, pub)


def test_reality_keypair_mismatch():
    a, _ = security.generate_x25519_keypair()
    _, b2 = security.generate_x25519_keypair()
    assert not security.verify_reality_keypair(a, b2)


def test_short_id_hex():
    sid = security.generate_short_id(8)
    assert len(sid) == 8
    int(sid, 16)  # must be hex


def test_password_hashing():
    h = security.hash_password("secret")
    assert h != "secret"
    assert security.verify_password("secret", h)
    assert not security.verify_password("wrong", h)


def test_jwt_roundtrip():
    tok = security.create_access_token("admin1")
    payload = security.decode_access_token(tok)
    assert payload["sub"] == "admin1"
    assert security.decode_access_token("garbage") is None


# ---------------------------------------------------------------------------
# Tests: xray builder / validator (no real binary needed)
# ---------------------------------------------------------------------------
async def test_build_config_and_validate(session):
    from app.inbounds import service as ib_service
    from app.xray.builder import build_config, write_config, validate_config_on_disk
    from app.xray.validator import validate_config, validate_vless_uri

    ib = await ib_service.create_inbound(
        session, tag="vless-reality-xhttp", name="t", port=443,
        sec="reality", network="xhttp", server_name="example.com",
    )
    cfg = await build_config(session)
    assert cfg["inbounds"], "should have at least one inbound"
    ok, errs = validate_config(cfg)
    assert ok, f"config invalid: {errs}"
    # nobody allowed -> 0 clients (expected, no users yet)
    path = await write_config(session)
    assert os.path.exists(path)


async def test_config_reality_fields(session):
    from app.inbounds import service as ib_service
    from app.xray.builder import build_config

    ib = await ib_service.create_inbound(
        session, tag="r", name="r", port=8443,
        sec="reality", network="xhttp", server_name="dest.example.com",
    )
    cfg = await build_config(session)
    inbound = next(i for i in cfg["inbounds"] if i["tag"] == "r")
    ss = inbound["streamSettings"]
    assert ss["network"] == "xhttp"
    assert ss["security"] == "reality"
    rs = ss["realitySettings"]
    assert rs["privateKey"] and rs["serverNames"]
    assert rs["serverNames"] == ["dest.example.com"]
    assert rs["shortIds"]


async def test_xhttp_stream_shape(session):
    from app.inbounds import service as ib_service
    from app.xray.transports import build_xhttp_stream

    ib = await ib_service.create_inbound(
        session, tag="x", name="x", port=8444, sec="reality", network="xhttp",
        xhttp_mode="packet-up",
    )
    st = build_xhttp_stream(ib)
    assert st["network"] == "xhttp"
    assert st["xhttpSettings"]["mode"] == "packet-up"


async def test_ws_stream_shape(session):
    from app.inbounds import service as ib_service
    from app.xray.transports import build_ws_stream

    ib = await ib_service.create_inbound(
        session, tag="w", name="w", port=8445, sec="reality", network="ws",
        transport_path="/ray", ws_host="host.example.com",
    )
    st = build_ws_stream(ib)
    assert st["network"] == "ws"
    assert st["wsSettings"]["path"] == "/ray"
    assert st["wsSettings"]["headers"]["Host"] == "host.example.com"


async def test_subscription_uri_reality(session):
    from app.domains import manager as dm
    from app.inbounds import service as ib_service
    from app.subscriptions import builder as sub
    from app.subscriptions.validator import assert_valid_subscription
    from app.users import service as us

    ib = await ib_service.create_inbound(
        session, tag="reality-xhttp", name="t", port=443,
        sec="reality", network="xhttp", server_name="example.com",
    )
    await dm.add_domain(session, "example.com")
    await dm.set_active(session, "example.com")
    u = await us.create_user(session, username="alice", expire_days=30)
    uris = await sub.build_subscription(session, u)
    assert len(uris) == 1
    uri = uris[0]
    assert uri.startswith("vless://")
    assert "security=reality" in uri
    assert "type=xhttp" in uri
    assert "pbk=" in uri and "sid=" in uri and "sni=example.com" in uri and "fp=" in uri
    # validation must pass
    assert_valid_subscription(uris)


async def test_subscription_uri_ws(session):
    from app.domains import manager as dm
    from app.inbounds import service as ib_service
    from app.subscriptions import builder as sub
    from app.subscriptions.validator import assert_valid_subscription
    from app.users import service as us

    ib = await ib_service.create_inbound(
        session, tag="ws1", name="w", port=443, sec="reality",
        network="ws", transport_path="/ws",
    )
    await dm.add_domain(session, "ws.example.com")
    await dm.set_active(session, "ws.example.com")
    u = await us.create_user(session, username="bob", expire_days=30)
    uris = await sub.build_subscription(session, u)
    assert "type=ws" in uris[0] and "path=%2Fws" in uris[0]
    assert_valid_subscription(uris)


# ---------------------------------------------------------------------------
# Tests: Railway port separation (the core architecture fix)
# ---------------------------------------------------------------------------
def test_ports_are_separated_by_default():
    from app.core.config import Settings

    s = Settings(XRAY_BINARY_PATH="/usr/local/bin/xray", XRAY_INBOUND_PORT=24567)
    # FastAPI binds the Railway-injected PORT; Xray binds its own internal port.
    assert s.xray_inbound_port != s.panel_port, "xray must not share the web PORT"
    assert s.xray_inbound_port == 24567
    # public (client-facing) port comes from the Railway TCP proxy, NOT 24567.
    s2 = Settings(
        XRAY_BINARY_PATH="/usr/local/bin/xray",
        XRAY_INBOUND_PORT=24567,
        RAILWAY_TCP_PROXY_PORT="12345",
        RAILWAY_TCP_PROXY_DOMAIN="x.example.com",
    )
    assert s2.public_port == 12345
    assert s2.public_port != s2.xray_inbound_port
    assert s2.public_host == "x.example.com"


def test_fastapi_uses_railway_port_only(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("PORT", "8080")
    s = Settings(XRAY_BINARY_PATH="/usr/local/bin/xray", XRAY_INBOUND_PORT=24567)
    # FastAPI must use the injected PORT and never the xray internal port.
    assert s.panel_port == 8080
    assert s.xray_inbound_port == 24567
    assert s.panel_port != s.xray_inbound_port


async def test_subscription_uses_tcp_proxy_port_not_internal(session):
    from app.core import config as cfgmod
    from app.domains import manager as dm
    from app.inbounds import service as ib_service
    from app.subscriptions import builder as sub
    from app.subscriptions.validator import assert_valid_subscription
    from app.users import service as us

    # Pin the Railway TCP proxy env (what the app would see on Railway).
    import os
    monkeypatch = _mp()
    monkeypatch.setenv("XRAY_BINARY_PATH", "/nonexistent/xray")
    monkeypatch.setenv("XRAY_INBOUND_PORT", "24567")
    monkeypatch.setenv("RAILWAY_TCP_PROXY_DOMAIN", "real.example.com")
    monkeypatch.setenv("RAILWAY_TCP_PROXY_PORT", "54321")

    ib = await ib_service.create_inbound(
        session, tag="reality-xhttp", name="t", port=24567,
        sec="reality", network="xhttp", server_name="example.com",
    )
    await dm.add_domain(session, "real.example.com")
    await dm.set_active(session, "real.example.com")
    u = await us.create_user(session, username="carol", expire_days=30)

    # re-read settings object the builder uses
    cfgmod.settings.RAILWAY_TCP_PROXY_DOMAIN = "real.example.com"
    cfgmod.settings.RAILWAY_TCP_PROXY_PORT = "54321"
    cfgmod.settings.XRAY_INBOUND_PORT = 24567

    uris = await sub.build_subscription(session, u)
    assert_valid_subscription(uris)
    uri = uris[0]
    # host = active domain, port = TCP proxy port (NEVER the internal 24567)
    assert "@real.example.com:54321" in uri, uri
    assert ":24567" not in uri, "link must not expose internal xray port"
    # xhttp reality canonical params present
    assert "type=xhttp" in uri and "path=%2F" in uri and "mode=auto" in uri
    assert "extra=" in uri
    # extra decodes to the spec shape
    import json
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(uri).query, keep_blank_values=True)
    extra = json.loads(qs["extra"][0])
    assert extra.get("xPaddingBytes") == "100-1000"
    assert extra.get("mode") == "auto"
    assert "scMaxEachPostBytes" in extra


class _MP:
    def __init__(self):
        import os
        self._os = os
        self._saved = {}

    def setenv(self, k, v):
        self._saved[k] = self._os.environ.get(k)
        self._os.environ[k] = v


def _mp():
    return _MP()


async def test_default_extra_matches_spec():
    import json

    from app.subscriptions import builder as sub

    extra = json.loads(sub._default_extra())
    assert extra == {"xPaddingBytes": "100-1000", "mode": "auto",
                     "scMaxEachPostBytes": "1000000"}


# ---------------------------------------------------------------------------
# Tests: users / expiration / ip limit
# ---------------------------------------------------------------------------
async def test_user_lifecycle(session):
    from app.users import service as us

    u = await us.create_user(session, username="carol", expire_days=1, traffic_limit_gb=10, ip_limit=2)
    assert u.status == "active"
    assert u.is_active
    # extend
    u = await us.extend_expiry(session, u, 30)
    exp = u.expire_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert (exp - datetime.now(timezone.utc)).days >= 29
    # disable
    u = await us.set_enabled(session, u, False)
    assert u.status == "disabled"
    # reset uuid changes
    old = u.uuid
    u = await us.reset_uuid(session, u)
    assert u.uuid != old and security.is_valid_uuid(u.uuid)


async def test_expiry_transition(session):
    from app.users import service as us

    u = await us.create_user(session, username="dave", expire_days=None)
    # force expired
    u.expire_at = datetime.now(timezone.utc) - timedelta(days=1)
    await session.commit()
    n = await us.deactivate_expired(session)
    assert n >= 1
    await session.refresh(u)
    assert u.status == "expired"
    assert not u.is_active


async def test_ip_limit_enforced(session):
    from app.users import service as us

    u = await us.create_user(session, username="erin", expire_days=30, ip_limit=2)
    ok1, _ = await us.register_session(session, u, "1.1.1.1")
    ok2, _ = await us.register_session(session, u, "2.2.2.2")
    ok3, reason = await us.register_session(session, u, "3.3.3.3")
    assert ok1 and ok2
    assert not ok3 and reason == "ip_limit_reached"
    # same ip reuses session (no new device)
    ok4, _ = await us.register_session(session, u, "1.1.1.1")
    assert ok4


async def test_traffic_limit_disable(session):
    from app.users import service as us

    u = await us.create_user(session, username="frank", expire_days=30, traffic_limit_gb=1)
    u.used_traffic_bytes = 2 * 1024 ** 3
    await session.commit()
    n = await us.enforce_traffic_limit(session)
    assert n >= 1
    await session.refresh(u)
    assert not u.enabled


# ---------------------------------------------------------------------------
# Tests: API (TestClient, ephemeral)
# ---------------------------------------------------------------------------
def test_auth_flow(tmp_db):
    import app.database as dbmod
    import asyncio

    async def _seed():
        await init_db()
        from app.bootstrap import ensure_admin
        async with dbmod.get_sessionmaker()() as s:
            await ensure_admin(s)
    asyncio.run(_seed())

    from app.main import app

    client = TestClient(app)
    # wrong password
    r = client.post("/api/auth/token", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    # correct
    r = client.post("/api/auth/token", data={"username": "admin", "password": "testpass123"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    # protected
    r = client.get("/api/dashboard/stats", headers=h)
    assert r.status_code == 200
    # create user via API
    r = client.post("/api/users", json={"username": "apiuser", "expire_days": 5}, headers=h)
    assert r.status_code == 201, r.text
    # seed an inbound + active domain so the subscription has a URI
    r = client.post("/api/inbounds", json={"tag": "vless-reality-xhttp", "name": "t",
                                        "port": 443, "security": "reality", "network": "xhttp",
                                        "server_name": "example.com"}, headers=h)
    assert r.status_code == 201, r.text
    r = client.post("/api/domains", json={"domain": "example.com"}, headers=h)
    assert r.status_code == 201, r.text
    # subscription returns valid text
    uuid = client.get("/api/users", headers=h).json()[0]["uuid"]
    r = client.get(f"/sub/{uuid}")
    assert r.status_code == 200
    assert "vless://" in r.text


# ---------------------------------------------------------------------------
# Tests: News endpoint (RSS parsing, mocked network)
# ---------------------------------------------------------------------------
def test_news_parses_rss_and_strips_html(monkeypatch):
    import io
    from app.api import news as news_mod

    sample = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Iran holds talks - Reuters</title>
        <link>https://example.com/a</link>
        <description>&lt;p&gt;Officials &lt;b&gt;met&lt;/b&gt; today to discuss &lt;i&gt;the deal&lt;/i&gt;.&lt;/p&gt;</description>
        <pubDate>Mon, 10 Jul 2026 10:00:00 GMT</pubDate>
        <source url="https://reuters.com">Reuters</source>
      </item>
      <item>
        <title>Markets react</title>
        <link>https://example.com/b</link>
        <description>Stocks moved.</description>
        <pubDate>Sun, 09 Jul 2026 10:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    class _Resp:
        def read(self):
            return sample
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(news_mod.urllib.request, "urlopen", lambda *a, **k: _Resp())
    news_mod._cache.clear()
    items = news_mod._fetch("Iran", 8)
    assert len(items) == 2
    # newest first (10 Jul before 09 Jul)
    assert items[0]["title"] == "Iran holds talks"
    # source suffix stripped, HTML stripped, entities unescaped
    assert items[0]["source"] == "Reuters"
    assert "Officials met today to discuss the deal." in items[0]["text"]
    assert "<" not in items[0]["text"]


def test_news_endpoint_requires_auth(tmp_db):
    from app.main import app
    from fastapi.testclient import TestClient

    c = TestClient(app)
    r = c.get("/api/news")
    assert r.status_code in (401, 403)


def test_news_endpoint_with_auth(tmp_db, monkeypatch):
    import asyncio
    from app.bootstrap import ensure_admin
    from app.database import get_sessionmaker
    from app.main import app
    from fastapi.testclient import TestClient

    async def _seed():
        await init_db()
        async with get_sessionmaker()() as s:
            await ensure_admin(s)
    asyncio.run(_seed())

    import io
    from app.api import news as news_mod
    sample = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Headline - Src</title><link>https://x/1</link>
      <description>&lt;p&gt;Body text here.&lt;/p&gt;</description>
      <pubDate>Mon, 10 Jul 2026 10:00:00 GMT</pubDate>
      <source url="https://x">Src</source></item></channel></rss>"""

    class _Resp:
        def read(self):
            return sample
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(news_mod.urllib.request, "urlopen", lambda *a, **k: _Resp())
    news_mod._cache.clear()

    c = TestClient(app)
    tok = c.post("/api/auth/token", data={"username": "admin", "password": "testpass123"}).json()["access_token"]
    r = c.get("/api/news", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["count"] == 1
    assert data["items"][0]["title"] == "Headline"
    assert "Body text here." in data["items"][0]["text"]


# ---------------------------------------------------------------------------
# Tests: Music folder listing
# ---------------------------------------------------------------------------
def test_music_list_empty(tmp_db):
    from app.api import settings as settings_router
    from app.main import app
    from fastapi.testclient import TestClient
    import asyncio
    from app.bootstrap import ensure_admin
    from app.database import get_sessionmaker

    async def _seed():
        await init_db()
        async with get_sessionmaker()() as s:
            await ensure_admin(s)
    asyncio.run(_seed())

    c = TestClient(app)
    tok = c.post("/api/auth/token", data={"username": "admin", "password": "testpass123"}).json()["access_token"]
    r = c.get("/api/settings", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    # musics dir exists (generated tracks now present) -> files listed
    assert "music" in r.json()
    assert "files" in r.json()["music"]
    assert r.json()["music"]["enabled"] is False
    assert r.json()["music"]["volume"] == 70


def test_music_list_lists_audio(tmp_db, monkeypatch):
    import os
    from pathlib import Path
    from app.api import settings as settings_router

    d = Path(tempfile.mkdtemp())
    (d / "song one.mp3").write_bytes(b"\x00")
    (d / "song two.ogg").write_bytes(b"\x00")
    (d / "ignore.txt").write_text("nope")
    monkeypatch.setattr(settings_router, "_MUSICS_DIR", d)
    files = settings_router.list_music_files()
    assert set(files) == {"song one.mp3", "song two.ogg"}


# ---------------------------------------------------------------------------
# Regression: DB path resolution (the data/data doubling + missing-dir crash)
# ---------------------------------------------------------------------------
def test_db_url_resolves_under_data_dir_and_creates_parent(monkeypatch):
    import tempfile
    from pathlib import Path

    from app.core.config import Settings

    # No DATABASE_URL env -> exercise the default path.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    d = tempfile.mkdtemp(prefix="spider_dbtest_")
    s = Settings(DATA_DIR=d)

    assert s.db_url.startswith("sqlite")
    db_file = s.db_url.split(":///", 1)[1]
    # The db must sit directly in DATA_DIR, NOT DATA_DIR/data (the old bug).
    assert db_file == str(Path(d) / "spider.db"), db_file
    # db_url access must create the parent directory so the engine can connect.
    assert Path(db_file).parent.is_dir()


def test_db_url_absolute_path_is_preserved(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////abs/path/spider.db")
    import tempfile

    d = tempfile.mkdtemp(prefix="spider_dbtest_abs_")
    s = Settings(DATA_DIR=d)
    # Absolute sqlite urls must be returned unchanged.
    assert s.db_url == "sqlite+aiosqlite:////abs/path/spider.db"


def test_db_url_postgres_passthrough(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/spider"
    )
    import tempfile

    d = tempfile.mkdtemp(prefix="spider_dbtest_pg_")
    s = Settings(DATA_DIR=d)
    assert s.db_url == "postgresql+asyncpg://u:p@db:5432/spider"


# ---------------------------------------------------------------------------
# Tests: console SPA shell (/app) + remote control API
# ---------------------------------------------------------------------------
def _auth_headers(tmp_db):
    """Seed the DB and return (client, bearer_headers, token_cookie).

    The /app page route is gated by require_auth (reads the spider_token
    COOKIE); the JSON APIs use the Bearer header. A real browser has
    both (cookie set at login + localStorage token in api()). We set both.
    """
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        maker = get_sessionmaker()
        async with maker() as s:
            await ensure_admin(s)

    asyncio.run(_seed())
    c = TestClient(app)
    r = c.post(
        "/api/auth/token",
        data={"username": "admin", "password": "testpass123"},
    )
    tok = r.json()["access_token"]
    # Cookie is set by set_auth_cookie on the login response.
    c.cookies.set("spider_token", tok)
    return c, {"Authorization": f"Bearer {tok}"}


def test_app_shell_requires_auth(tmp_db):
    from app.main import app

    c = TestClient(app)
    r = c.get("/dashboard", follow_redirects=False)
    # Unauthenticated: AuthMiddleware returns 302 to /login for text/html.
    assert r.status_code in (401, 302)


def test_app_shell_renders_with_auth(tmp_db):
    from app.main import app

    c, h = _auth_headers(tmp_db)
    r = c.get("/dashboard")
    assert r.status_code == 200
    assert "sidebar-fixed" in r.text
    assert "app_shell.js" in r.text


def test_remote_status_and_mouse(tmp_db):
    from app.main import app

    c, h = _auth_headers(tmp_db)
    assert c.get("/api/remote/status", headers=h).json()["mode"] == "loopback"
    assert c.post("/api/remote/mouse/click", json={"button": "right"}, headers=h).json() == {"ok": True}
    assert c.post("/api/remote/mouse/scroll", json={"dx": 0, "dy": 120}, headers=h).json() == {"ok": True}
    # clipboard round-trip via server mirror
    assert c.post("/api/remote/clipboard", json={"text": "xyz"}, headers=h).json() == {"ok": True}
    assert c.get("/api/remote/clipboard", headers=h).json()["text"] == "xyz"


def test_remote_rejects_unauthenticated(tmp_db):
    from app.main import app

    c = TestClient(app)
    # No token at all -> 401
    assert c.get("/api/remote/status").status_code == 401


# ---------------------------------------------------------------------------
# Tests: login page must be self-contained (no api/TOKEN/ME globals)
# ---------------------------------------------------------------------------
def test_login_page_uses_native_fetch(tmp_db):
    """login.html must POST JSON to /api/login via fetch and NOT depend on the
    SPA globals (api/TOKEN/ME) that are undefined on this standalone page."""
    from app.main import app

    c = TestClient(app)
    html = c.get("/login").text
    assert "fetch(\"/api/login\"" in html or "fetch('/api/login'" in html
    # The broken global references must be gone from the handler code.
    assert "api('/auth/token'" not in html
    # No bare references to the SPA globals the old handler relied on.
    assert "TOKEN = data" not in html
    assert "ME = data" not in html


def test_api_login_authenticates(tmp_db):
    """POST /api/login returns a token for valid creds and 401 otherwise."""
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        maker = get_sessionmaker()
        async with maker() as s:
            await ensure_admin(s)

    asyncio.run(_seed())

    c = TestClient(app)
    ok = c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    assert ok.status_code == 200
    assert "access_token" in ok.json()

    bad = c.post("/api/login", json={"username": "admin", "password": "nope"})
    assert bad.status_code == 401
    # Generic message (no username enumeration), not a raw JS error.
    assert bad.json()["detail"] == "Invalid username or password"


def test_browser_proxy_blocks_ssrf_and_bad_scheme(tmp_db):
    """The embedded Chrome tab proxies pages server-side so they render in the
    iframe. The proxy must reject private/loopback hosts (SSRF) and non-http(s)
    schemes, and must return a permissive framing header so the iframe accepts it.
    """
    from app.main import app
    from app.bootstrap import ensure_admin
    import asyncio

    async def _seed():
        await init_db()
        async with get_sessionmaker()() as s:
            await ensure_admin(s)

    asyncio.run(_seed())
    c = TestClient(app, follow_redirects=False)
    tok = c.post("/api/auth/token", data={"username": "admin", "password": "testpass123"}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}
    r = c.get("/api/browser/proxy?url=http://127.0.0.1:1/", headers=H, follow_redirects=False)
    assert r.status_code == 403, r.status_code
    r = c.get("/api/browser/proxy?url=file:///etc/passwd", headers=H, follow_redirects=False)
    assert r.status_code == 400, r.status_code
    r = c.get("/api/browser/proxy?url=https://example.com/", headers=H, follow_redirects=False)
    assert r.status_code == 200, r.status_code
    assert "Example Domain" in r.text
    assert r.headers.get("x-frame-options", "").upper() == "ALLOWALL"
    assert "api/browser/proxy" in r.text, "relative links must route through the proxy"


def test_chrome_tab_loads_through_proxy(tmp_db):
    """Regression: the Chrome section must route the iframe through the proxy
    (not load the remote URL directly, which most sites block -> white page)."""
    # The proxy URL is built inside app_shell.js (deferred script), so verify it
    # there rather than in the dashboard HTML shell.
    js_path = os.path.join(os.path.dirname(__file__), "..", "app", "static", "assets", "js", "app_shell.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()
    assert '"/api/browser/proxy?url=" + encodeURIComponent' in js, \
        "Chrome tab must proxy pages through /api/browser/proxy"


