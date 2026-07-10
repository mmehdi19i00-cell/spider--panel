"""
Pages Router - Web page routes for Spider Panel
Serves login, dashboard, and public subscription pages
"""
from fastapi import APIRouter, Request, HTTPException, Depends, Form, Query
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os

# Import state and config
from core.state import (
    LINKS, LINKS_LOCK, SUBS, SUBS_LOCK, USERS, USERS_LOCK,
    is_link_allowed, get_host
)
from config import logger, SESSION_COOKIE, SESSION_TTL, AUTH, SETTINGS
from services.xray_service import generate_vless_link as svc_generate_vless_link

router = APIRouter()

# ── Jinja2 Templates ────────────────────────────────────────────────────────
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
if not TEMPLATES_DIR.exists():
    TEMPLATES_DIR = Path(__file__).parent.parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ── Route Handlers ──────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def root_page():
    """Root page - redirect to login"""
    return RedirectResponse(url="/login")

@router.get("/login", response_class=HTMLResponse)
async def login_page():
    """Login page"""
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ورود · Spider Panel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root { --spider-blue: #2B7FFF; --spider-purple: #7B61FF; --spider-red: #FF2352; }
* { margin:0; padding:0; box-sizing:border-box }
body { font-family: 'Vazirmatn', sans-serif; background: #F8FAFC; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
.card { background: white; padding: 2rem; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); width: 100%; max-width: 400px; }
h1 { color: #2B7FFF; text-align: center; margin-bottom: 1.5rem; }
input { width: 100%; padding: 12px; border: 1px solid #E2E8F0; border-radius: 8px; font-size: 14px; margin-bottom: 1rem; }
button { width: 100%; padding: 12px; background: linear-gradient(90deg, #2B7FFF, #7B61FF); color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
</style>
</head>
<body>
<div class="card">
<h1>ورود · Spider Panel</h1>
<form id="form">
<input type="password" id="pw" placeholder="رمز عبور" autofocus required>
<button type="submit">ورود به داشبورد</button>
</form>
<div id="err" style="color: #DC2626; margin-top: 1rem; display: none;"></div>
</div>
<script>
document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: document.getElementById('pw').value}) });
  if (r.ok) location.href = '/dashboard';
  else { const d = await r.json().catch(() => ({})); document.getElementById('err').textContent = d.detail || 'خطا در ورود'; }
});
</script>
</body></html>""")

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spider Panel · داشبورد</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root { --spider-blue: #2B7FFF; --spider-purple: #7B61FF; --spider-red: #FF2352; }
* { margin:0; padding:0; box-sizing:border-box }
body { font-family: 'Vazirmatn', sans-serif; background: #F8FAFC; min-height: 100vh; }
.sidebar { width: 260px; min-height: 100vh; background: white; border-left: 1px solid #E2E8F0; position: fixed; right: 0; top: 0; bottom: 0; }
.main { margin-right: 260px; padding: 2rem; }
h1 { color: #2B7FFF; margin-bottom: 2rem; }
</style>
</head>
<body>
<aside class="sidebar"><h2 style="padding: 1rem; color: #2B7FFF;">Spider Panel</h2></aside>
<main class="main"><h1>داشبورد</h1><p>به پنل مدیریت خوش آمدید</p></main>
</body></html>"""

SPIDER_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spider Panel</title>
<style>
* { margin:0; padding:0; box-sizing:border-box }
body { font-family: sans-serif; background: #0B1121; color: white; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
h1 { font-size: 3rem; background: linear-gradient(90deg, #2B7FFF, #7B61FF); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
a { display: inline-block; margin-top: 2rem; padding: 1rem 2rem; background: #2B7FFF; color: white; border-radius: 8px; text-decoration: none; }
</style>
</head>
<body><div style="text-align:center"><h1>Spider Panel</h1><p>پنل مدیریت سرور و اشتراک‌های VPN</p><a href="/login">ورود به پنل</a></div></body></html>"""

@router.get("/", response_class=HTMLResponse)
async def root_page():
    return RedirectResponse(url="/login")

@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ورود · Spider Panel</title>
<style>
* { margin:0; padding:0; box-sizing:border-box }
body { font-family: sans-serif; background: #F8FAFC; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
.card { background: white; padding: 2rem; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); width: 100%; max-width: 400px; }
h1 { color: #2B7FFF; text-align: center; margin-bottom: 1.5rem; }
input { width: 100%; padding: 12px; border: 1px solid #E2E8F0; border-radius: 8px; font-size: 14px; margin-bottom: 1rem; }
button { width: 100%; padding: 12px; background: linear-gradient(90deg, #2B7FFF, #7B61FF); color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
</style>
</head>
<body>
<div class="card">
<h1>ورود · Spider Panel</h1>
<form id="form"><input type="password" id="pw" placeholder="رمز عبور" autofocus required><button type="submit">ورود به داشبورد</button></form>
<div id="err" style="color: #DC2626; margin-top: 1rem; display: none;"></div>
</div>
<script>
document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: document.getElementById('pw').value}) });
  if (r.ok) location.href = '/dashboard';
  else { const d = await r.json().catch(() => ({})); document.getElementById('err').textContent = d.detail || 'خطا در ورود'; }
});
</script>
</body></html>""")

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spider Panel · داشبورد</title>
<style>
* { margin:0; padding:0; box-sizing:border-box }
body { font-family: sans-serif; background: #F8FAFC; min-height: 100vh; }
.sidebar { width: 260px; min-height: 100vh; background: white; border-left: 1px solid #E2E8F0; position: fixed; right: 0; top: 0; bottom: 0; }
.main { margin-right: 260px; padding: 2rem; }
h1 { color: #2B7FFF; margin-bottom: 2rem; }
</style>
</head>
<body>
<aside class="sidebar"><h2 style="padding: 1rem; color: #2B7FFF;">Spider Panel</h2></aside>
<main class="main"><h1>داشبورد</h1><p>به پنل مدیریت خوش آمدید</p></main>
</body></html>""")

@router.get("/spider", response_class=HTMLResponse)
async def spider_page():
    return HTMLResponse(SPIDER_HTML)

@router.get("/favicon.ico")
async def favicon():
    return Response(content='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><ellipse cx="50" cy="48" rx="17" ry="20" fill="#2B7FFF"/><ellipse cx="50" cy="26" rx="11" ry="9" fill="#7B61FF"/></svg>', media_type="image/svg+xml")

# ── Public Subscription API ──────────────────────────────────────────────────
from core.state import LINKS, LINKS_LOCK, SUBS, SUBS_LOCK, is_link_allowed
from config import logger
from services.xray_service import generate_vless_link as svc_generate_vless_link

@router.get("/api/public/sub/{uuid_key}")
async def public_subscription(uuid_key: str, request: Request, pw: str = None):
    from core.state import SUBS, SUBS_LOCK
    async with SUBS_LOCK:
        sub = SUBS.get(uuid_key)
    if not sub:
        raise HTTPException(status_code=404, detail="اشتراک یافت نشد")
    if sub.get("password") and pw != sub.get("password"):
        return HTMLResponse(f"<html><body><h1>رمز عبور مورد نیاز</h1><form method='get'><input name='pw' placeholder='رمز عبور'><button type='submit'>ورود</button></form></body></html>")
    return HTMLResponse(f"<html><body><h1>اشتراک {uuid_key}</h1><p>Welcome to Spider Panel</p></body></html>")

@router.get("/sub-group/{uuid_key}")
async def subscription_group(uuid_key: str, pw: str = None):
    from core.state import SUBS, SUBS_LOCK, LINKS, LINKS_LOCK, is_link_allowed
    from services.xray_service import generate_vless_link
    async with SUBS_LOCK:
        sub = SUBS.get(uuid_key)
    if not sub:
        raise HTTPException(status_code=404, detail="اشتراک یافت نشد")
    if sub.get("password") and pw != sub.get("password"):
        raise HTTPException(status_code=401, detail="رمز عبور اشتباه است")
    links = []
    for link_id in sub.get("links", []):
        async with LINKS_LOCK:
            link = LINKS.get(link_id)
        if link and is_link_allowed(link):
            from services.xray_service import generate_vless_link
            try:
                config = generate_vless_link(uuid=link_id, remark=link.get("label", "Spider"), inbound_id=link.get("inbound_id"), user=link)
                links.append(config)
            except: pass
    return Response(content="\n".join(links), media_type="text/plain")

# ── Login/Logout API ─────────────────────────────────────────────────────────
@router.post("/api/login")
async def api_login(request: Request, password: str = Form(...)):
    import hashlib
    expected_hash = AUTH.get("password_hash")
    if expected_hash:
        provided_hash = hashlib.sha256(f"{password}{AUTH.get('secret', 'spider-panel-secret-key-v2')}".encode()).hexdigest()
        if provided_hash != expected_hash:
            return JSONResponse({"detail": "رمز عبور اشتباه است"}, status_code=401)
    import secrets
    from core.state import SESSIONS, SESSIONS_LOCK, SESSION_TTL
    import asyncio
    token = secrets.token_urlsafe(32)
    async with SESSIONS_LOCK:
        SESSIONS[token] = asyncio.get_event_loop().time() + SESSION_TTL
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(key=SESSION_COOKIE, value=token, httponly=True, max_age=SESSION_TTL, samesite="lax")
    return response

@router.post("/api/logout")
async def api_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        from core.state import SESSIONS, SESSIONS_LOCK
        async with SESSIONS_LOCK:
            SESSIONS.pop(token, None)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response

@router.get("/api/me")
async def api_me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        from core.state import SESSIONS, SESSIONS_LOCK
        import asyncio
        async with SESSIONS_LOCK:
            exp = SESSIONS.get(token)
            if exp and exp > asyncio.get_event_loop().time():
                return {"authenticated": True}
    return {"authenticated": False}

# ── Print all registered routes at startup ──────────────────────────────────
def print_routes(app):
    print("\n" + "="*60)
    print("REGISTERED ROUTES:")
    print("="*60)
    for route in app.routes:
        if hasattr(route, "methods"):
            methods = ",".join(route.methods)
            print(f"  {methods:6s} {route.path}")
        else:
            print(f"  {'*':6s} {route.path}")
    print("="*60 + "\n")
