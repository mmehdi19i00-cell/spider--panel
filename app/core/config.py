"""Central configuration for Spider Panel.

Every value is environment driven so the app is Railway-native and never
hardcodes ports, paths, or secrets. See `.env.example` for the full list.
"""
from __future__ import annotations

import secrets
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- FastAPI / web ---
    # FastAPI MUST always bind the Railway-injected PORT. It is never shared
    # with Xray (see XRAY_INBOUND_PORT below). Never hardcode 443/8443/8000.
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    PANEL_PORT: int = 0  # 0 -> mirror PORT (Railway injects PORT)
    LOG_LEVEL: str = "INFO"

    # --- Database ---
    # SQLite for dev, postgresql+asyncpg://... for prod (Railway provides DATABASE_URL).
    # Relative sqlite paths resolve under DATA_DIR (see db_url below), so the db
    # file lives directly in DATA_DIR, e.g. <DATA_DIR>/spider.db.
    DATABASE_URL: str = "sqlite+aiosqlite:///./spider.db"

    # --- Secrets / auth ---
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # --- First-run admin ---
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"
    ADMIN_EMAIL: str = ""

    # --- Xray core ---
    # XRAY_BINARY_PATH: the xray binary installed in the image.
    XRAY_BINARY_PATH: str = "/usr/local/bin/xray"
    # XRAY_INBOUND_PORT: the INTERNAL port Xray binds INSIDE the container.
    # It MUST never equal the FastAPI PORT (Railway injects PORT for the web
    # dashboard). Xray is reached from the outside only through the Railway
    # TCP proxy (RAILWAY_TCP_PROXY_PORT), so this is purely server-side.
    # Fixed default 24567 — do NOT repurpose PORT/443/8443 here.
    XRAY_INBOUND_PORT: int = 24567
    # Backwards-compatible alias kept for old deploy configs/tests.
    XRAY_PORT: int = 0  # 0 -> use XRAY_INBOUND_PORT
    XRAY_API_PORT: int = 10085  # internal stats/control API (loopback only)
    XRAY_CONFIG_PATH: str = ""  # defaults to <DATA_DIR>/xray/config.json

    # --- Railway proxy detection (auto-injected by Railway) ---
    RAILWAY_TCP_PROXY_DOMAIN: str = ""
    RAILWAY_TCP_PROXY_PORT: str = ""
    RAILWAY_ENVIRONMENT: str = ""

    # --- Misc ---
    DATA_DIR: str = ""          # defaults to <repo>/data
    PUBLIC_URL: str = ""        # optional vanity base for subscription links
    CORS_ORIGINS: str = ""      # comma separated, optional

    # ------------------------------------------------------------------
    # Derived paths / values
    # ------------------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_dir(self) -> str:
        p = Path(self.DATA_DIR) if self.DATA_DIR else (
            Path(__file__).resolve().parent.parent.parent / "data"
        )
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def xray_config_path(self) -> str:
        if self.XRAY_CONFIG_PATH:
            return self.XRAY_CONFIG_PATH
        return str(Path(self.data_dir) / "xray" / "config.json")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_url(self) -> str:
        """Return an absolute DB url so relative sqlite paths resolve under DATA_DIR.

        The db file's parent directory is created on access so the engine never
        fails with "unable to open database file" due to a missing directory.
        """
        url = self.DATABASE_URL
        if url.startswith("sqlite"):
            prefix, sep, rel = url.partition(":///")
            if sep:
                # Absolute path already (sqlite:////abs or sqlite+aiosqlite:////abs).
                if rel.startswith("/"):
                    return url
                db_file = Path(self.data_dir) / rel
                db_file.parent.mkdir(parents=True, exist_ok=True)
                return f"{prefix}:///{db_file}"
        return url

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_postgres(self) -> bool:
        return self.db_url.startswith("postgresql")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def public_host(self) -> str:
        """Host used in client (subscription) links."""
        return self.RAILWAY_TCP_PROXY_DOMAIN or self.PUBLIC_URL or "127.0.0.1"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def panel_port(self) -> int:
        """Port FastAPI binds — always the Railway-injected PORT.

        We never invent a separate web port; PANEL_PORT mirrors PORT when set.
        """
        return self.PANEL_PORT or self.PORT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def xray_inbound_port(self) -> int:
        """Port Xray actually binds INSIDE the container.

        Fixed internal port (default 24567). It MUST never equal the FastAPI
        PORT, so the two processes never fight over the same socket. The
        client-facing port is the Railway TCP proxy port (public_port), never
        this one. XRAY_PORT is a legacy alias for XRAY_INBOUND_PORT.
        """
        if self.XRAY_PORT and self.XRAY_PORT > 0:
            return self.XRAY_PORT
        return self.XRAY_INBOUND_PORT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def public_port(self) -> int:
        """Port used in client (subscription) links — the EXTERNALLY
        reachable Railway TCP proxy port. NEVER the internal Xray port and
        NEVER the FastAPI web port. Falls back to internal_xray_port only
        when no TCP proxy is configured (local/dev)."""
        if self.RAILWAY_TCP_PROXY_PORT:
            try:
                return int(self.RAILWAY_TCP_PROXY_PORT)
            except ValueError:
                pass
        return self.xray_inbound_port

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_railway(self) -> bool:
        return bool(self.RAILWAY_ENVIRONMENT or self.RAILWAY_TCP_PROXY_DOMAIN)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_secret(self) -> str:
        return self.SECRET_KEY or "insecure-dev-secret-change-me"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_list(self) -> list[str]:
        if not self.CORS_ORIGINS:
            return ["*"]
        return [c.strip() for c in self.CORS_ORIGINS.split(",") if c.strip()]


settings = Settings()

# Ensure a default admin password exists for first run (dev convenience).
# Default is "admin" (set above); only generate a random one if it was
# explicitly emptied via env and no override is provided. Real deployments
# MUST set ADMIN_PASSWORD (and should change it via Settings after login).
if not settings.ADMIN_PASSWORD:
    # Persist a generated one to the process; real deployments MUST set ADMIN_PASSWORD.
    settings.ADMIN_PASSWORD = secrets.token_urlsafe(16)
