# 🕷️ Spider Panel

**Red Neon Futuristic Cyber Xray Management Panel**

A production-ready, Railway-deployable panel to manage **Xray-core** VLESS
inbounds (Reality + XHTTP / WebSocket), users, domains, subscriptions, IP
limits and traffic quotas — all wrapped in a mobile-first glassmorphism dashboard.

> The single source of truth is the **database**. The Xray `config.json`
> builder and the subscription builder both read the SAME inbound/user rows, so
> every generated VLESS link always matches the running server. No hardcoded
> pbk / sid / sni / path / transport anywhere.

---

## ✨ Features

| Area | What you get |
|------|---------------|
| **Auth** | JWT session, password hashing (bcrypt), change username/password |
| **Dashboard** | Total / active / expired / disabled users, online connections, Xray status, CPU/RAM, total traffic |
| **Users** | Create / edit / delete, reset UUID, extend expiry, traffic & IP limits, enable/disable, live sessions view |
| **Inbounds** | VLESS **Reality** + **XHTTP** and legacy **WebSocket**; generate/regenerate Reality keys via `xray x25519` |
| **Domains** | Multiple domains, one active; active domain auto-feeds Reality SNI / TLS / subscription links |
| **Subscriptions** | `/sub/{uuid}` returning validated `vless://` URIs (Reality + XHTTP + WS) — never broken configs |
| **IP limit** | Real per-user device enforcement via `user_sessions` table |
| **Traffic** | GB quota + expiry; users auto-disabled when over quota |
| **Xray control** | start / stop / restart / reload / health — no zombies, no duplicate processes |
| **Theme** | Dark + light, neon-red glow, animated background, fully mobile responsive |

---

## 🧱 Architecture

```
app/
  core/
    config.py        # env-driven settings (PORT, XRAY_INBOUND_PORT, RAILWAY_TCP_PROXY_*, …)
    security.py       # JWT, bcrypt, X25519 Reality keys (cryptography == xray x25519)
    logging.py        # structured audit logging for config generation
  xray/
    builder.py       # THE config.json generator (single source of truth)
    reality.py        # Reality keypair management
    transports.py     # xhttp + ws streamSettings builders
    process.py        # process manager (no zombies / duplicates, safe reload)
    validator.py      # config + VLESS URI validation
  users/
    models.py         # SQLAlchemy models (AdminUser, User, Inbound, Domain, UserSession, Setting)
    service.py        # user CRUD, expiry, traffic, IP-limit logic
  inbounds/service.py # inbound CRUD + Reality key regen
  domains/manager.py  # domain CRUD + active switching
  subscriptions/
    builder.py        # reads SAME inbound rows -> vless URIs
    validator.py      # guarantees valid subscriptions
  api/               # FastAPI routers: auth, users, dashboard, inbounds, domains, subscription, system
  static/            # index.html + css + js (the SPA)
  main.py            # FastAPI app + lifespan (init db, seed, write config, start xray)
  init_admin.py      # standalone admin bootstrap CLI
tests/test_spider_panel.py  # full test suite
```

### Data flow (single source of truth)

```
        ┌────────────┐
        │  Database  │  (Inbound + User rows = truth)
        └─────┬──────┘
              │ read
        ┌─────▼──────┐
        │ Xray Builder│  app/xray/builder.py  ──► config.json ──► xray run
        └─────┬──────┘
              │ read SAME rows
        ┌─────▼────────┐
        │ Sub Builder   │  app/subscriptions/builder.py ──► vless:// URIs
        └──────────────┘
```

---

## 🚀 Deployment on Railway

1. **Create a new Railway project** and link this repo (or use `railway link`).
2. Railway auto-detects the `Dockerfile` and builds it. The build installs
   the **latest official xray-core** to `/usr/local/bin/xray` at build time.
3. **Add a Volume** in the Railway dashboard and mount it at `/app/data`
   (the app stores SQLite + the generated `xray/config.json` there). Railway
   does **not** support the `VOLUME` instruction in Dockerfiles, so the
   volume is configured in the dashboard, not in the image.
4. **Add a TCP Proxy** for the VLESS port. Railway injects
   `RAILWAY_TCP_PROXY_DOMAIN` and `RAILWAY_TCP_PROXY_PORT`; the app reads
   them automatically to build public subscription links. The **web** dashboard
   is served on `PORT` (Railway's HTTP service). The TCP proxy is the ONLY
   public path to Xray — the internal Xray port (24567) is never exposed.
5. **Set environment variables** in the Railway dashboard:
   | Variable | Required | Notes |
   |----------|----------|-------|
   | `ADMIN_PASSWORD` | ✅ | Strong password for first admin |
   | `SECRET_KEY` | ✅ | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
   | `ADMIN_USERNAME` | ⬜ | default `admin` |
   | `XRAY_BINARY_PATH` | ⬜ | default `/usr/local/bin/xray` |
   | `XRAY_INBOUND_PORT` | ⬜ | default `24567` (internal only; never the web `PORT`) |
   | `DATA_DIR` | ⬜ | set to `/app/data` (the mounted volume) |
   | `DATABASE_URL` | ⬜ | default SQLite; use Postgres in prod |
   | `RAILWAY_TCP_PROXY_DOMAIN/PORT` | auto | injected by Railway TCP proxy |
6. **Deploy.** On first boot the app:
   - creates tables,
   - creates the admin account (if none exists),
   - creates a default **VLESS Reality + XHTTP** inbound on the internal port `24567`,
   - writes `config.json`,
   - validates it (`xray run -test`), and starts Xray.

> **Port separation (critical):** FastAPI binds the Railway-injected `PORT` only.
> Xray binds its own internal port (`XRAY_INBOUND_PORT`, default `24567`) — never
> the web `PORT`, never `443`/`8443`. The public VLESS endpoint is **only** the
> Railway TCP proxy (`RAILWAY_TCP_PROXY_DOMAIN:RAILWAY_TCP_PROXY_PORT`) — it
> forwards to `24567` inside the container. Subscription links always use the TCP
> proxy port, never `24567`.

---

## 🐳 Local / VPS with Docker

```bash
# 1. configure
cp .env.example .env
#   edit .env: set SECRET_KEY and ADMIN_PASSWORD (REQUIRED)

# 2. build + run
docker compose up -d --build

# 3. open http://localhost:8000 and log in with your admin creds
#    (default admin / changeme123 unless you changed ADMIN_PASSWORD)
```

Data persists in the `spider-data` volume (`/app/data`): SQLite DB +
generated `xray/config.json`.

### Manual admin bootstrap

```bash
pip install -r requirements.txt
python -m app.init_admin --username admin --password 'your-strong-pass'
```

---

## 🧪 Development & Tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

The test suite validates (no real xray binary required — key generation
falls back to `cryptography`, which is **byte-identical** to `xray x25519`):
- UUID format
- Reality keypair correctness (public key derives from private key)
- XHTTP + WS `streamSettings` shape
- VLESS subscription URI correctness + validation
- Xray `config.json` validity
- User expiry transition
- IP-limit enforcement
- Full API auth + user + subscription flow (FastAPI TestClient)

---

## 🔌 API Reference (selected)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/token` | ❌ | Login (form) → JWT |
| POST | `/api/auth/login` | ❌ | Login (JSON) → JWT |
| GET | `/api/auth/me` | ✅ | Current admin |
| POST | `/api/auth/change-credentials` | ✅ | Change username/password |
| GET | `/api/dashboard/stats` | ✅ | Aggregate stats |
| GET/POST | `/api/users` | ✅ | List / create users |
| GET/PUT/DELETE | `/api/users/{id}` | ✅ | Read / update / delete |
| POST | `/api/users/{id}/reset-uuid` | ✅ | Rotate UUID |
| POST | `/api/users/{id}/extend?days=N` | ✅ | Extend expiry |
| POST | `/api/users/{id}/enable`\|`/disable` | ✅ | Toggle |
| GET/POST | `/api/inbounds` | ✅ | List / create inbounds |
| PUT/DELETE | `/api/inbounds/{id}` | ✅ | Update / delete |
| POST | `/api/inbounds/{id}/regen-keys` | ✅ | New Reality keys |
| GET/POST | `/api/domains` | ✅ | List / add domains |
| POST | `/api/domains/{domain}/activate` | ✅ | Switch active domain (+reload) |
| GET | `/sub/{uuid}` | ❌ | Subscription (`?format=json`) |
| GET/POST | `/api/system/xray/*` | ✅ | health/start/stop/restart/reload |
| GET | `/api/settings/music/list` | ✅ | List audio files in `/musics` |
| GET | `/api/news` | ✅ | Latest Iran news (RSS, text-only, `?query=&limit=`) |

---

## 🎵 Music on open & 📰 News

- **Music:** drop `.mp3`/`.ogg`/`.wav`/`.m4a`/`.webm`/`.aac`/`.flac` files into
  `app/static/musics/`. Every time the panel opens, a **random** track plays
  automatically (toggle in *Settings → Music on open*, on by default). Served at
  `/musics` and listed by `GET /api/settings/music/list`.
- **News:** the sidebar *News* tab opens a scrollable box with the latest **Iran**
  news (fetched live from an RSS feed, text only — long articles scroll inside the
  box, never resize it). Use the search box to query any topic, and Prev/Next to
  browse the fetched items. Results are cached ~10 min.

## 🔐 Security

- Passwords hashed with **bcrypt**.
- Stateless **JWT** (HS256) with configurable expiry.
- All secrets come from the environment — nothing hardcoded.
- Subscription URIs are **validated before being served**; a broken config is
  never returned.
- Reality keypairs are verified (public key must derive from private key);
  invalid pairs are rejected/regenerated.

---

## 📝 Notes

- **No zombies / duplicates:** the process manager tracks the child PID and
  reaps on stop; start refuses if already running.
- **Safe reload:** uses `SIGHUP` when supported, otherwise a clean
  stop→start.
- **SQLite for dev, Postgres for prod** — same async SQLAlchemy engine,
  switched purely by `DATABASE_URL`.
- The frontend is a dependency-free vanilla SPA (mobile-first, glassmorphism,
  animated neon background, dark/light toggle) — no build step required.

**Spider Panel** — built to actually generate working VLESS Reality + XHTTP
configurations, not just look pretty. 🕷️
