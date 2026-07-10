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
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # --- Database ---
    # SQLite for dev, postgresql+asyncpg://... for prod (Railway provides DATABASE_URL)
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/spider.db"

    # --- Secrets / auth ---
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # --- First-run admin ---
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""
    ADMIN_EMAIL: str = ""

    # --- Xray core ---
    XRAY_BINARY_PATH: str = "/usr/local/bin/xray"
    XRAY_PORT: int = 443
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
        """Return an absolute DB url so relative sqlite paths resolve under DATA_DIR."""
        url = self.DATABASE_URL
        if url.startswith("sqlite") and ":///" not in url.replace("sqlite+aiosqlite://", "sqlite://"):
            # already absolute (sqlite:////abs)
            return url
        if url.startswith("sqlite") and ":///" in url:
            prefix, rel = url.split(":///", 1)
            if not rel.startswith("/"):
                return f"{prefix}:///{Path(self.data_dir) / rel}"
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
    def public_port(self) -> int:
        """Port used in client (subscription) links."""
        if self.RAILWAY_TCP_PROXY_PORT:
            try:
                return int(self.RAILWAY_TCP_PROXY_PORT)
            except ValueError:
                pass
        return self.XRAY_PORT

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
if not settings.ADMIN_PASSWORD:
    # Persist a generated one to the process; real deployments MUST set ADMIN_PASSWORD.
    settings.ADMIN_PASSWORD = secrets.token_urlsafe(16)
