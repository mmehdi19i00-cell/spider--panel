"""Pydantic schemas for request/response validation."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------- Auth ----------------
class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class ChangeCredentials(BaseModel):
    current_password: str
    new_username: str | None = None
    new_password: str | None = None


# ---------------- Users ----------------
class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    expire_days: int | None = None
    traffic_limit_gb: float = 0
    ip_limit: int = 0
    enabled_inbounds: str = ""


class UserUpdate(BaseModel):
    username: str | None = None
    status: str | None = None
    enabled: bool | None = None
    expire_days: int | None = None  # set absolute? we treat as extend
    traffic_limit_gb: float | None = None
    ip_limit: int | None = None
    enabled_inbounds: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in ("active", "disabled", "expired"):
            raise ValueError("status must be active|disabled|expired")
        return v


class UserOut(BaseModel):
    id: int
    username: str
    uuid: str
    status: str
    enabled: bool
    expire_at: datetime | None
    traffic_limit_bytes: int
    used_traffic_bytes: int
    ip_limit: int
    enabled_inbounds: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------- Subscription ----------------
class SubscriptionOut(BaseModel):
    username: str
    uris: list[str]


# ---------------- Domains ----------------
class DomainCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=255)
    note: str = ""


class DomainOut(BaseModel):
    id: int
    domain: str
    is_active: bool
    note: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------- Inbounds ----------------
class InboundCreate(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    name: str = ""
    port: int = Field(ge=1, le=65535)
    domain: str = ""  # per-inbound domain (used for SNI + subscription host); blank=active domain
    external_port: int | None = None  # client-facing port (differs from `port` behind NAT/proxy)
    security: str = "reality"  # reality | tls | none
    network: str = "xhttp"  # xhttp | ws
    server_name: str = ""  # Reality SNI shown to clients (default is1-ssl.mzstatic.com)
    spider_x: str = "/"
    transport_path: str = "/"
    ws_host: str = ""
    xhttp_mode: str = "auto"
    xhttp_x_padding_bytes: str = "100-1000"
    xhttp_sc_max_each_post_bytes: str = "1000000-2000000"
    xhttp_sc_max_concurrent_posts: int = 100
    xhttp_extra: str = ""
    cert_path: str = ""
    key_path: str = ""
    alpn: str = "h2,http/1.1"
    uuid: str | None = None


class InboundUpdate(BaseModel):
    name: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    domain: str | None = None
    external_port: int | None = Field(default=None, ge=1, le=65535)
    security: str | None = None
    network: str | None = None
    server_name: str | None = None
    spider_x: str | None = None
    transport_path: str | None = None
    ws_host: str | None = None
    xhttp_mode: str | None = None
    xhttp_x_padding_bytes: str | None = None
    xhttp_sc_max_each_post_bytes: str | None = None
    xhttp_sc_max_concurrent_posts: int | None = None
    xhttp_extra: str | None = None
    cert_path: str | None = None
    key_path: str | None = None
    alpn: str | None = None
    enabled: bool | None = None
    uuid: str | None = None


class RealityKeys(BaseModel):
    private_key: str
    public_key: str
    short_id: str


class InboundOut(BaseModel):
    id: int
    tag: str
    name: str
    protocol: str
    port: int
    domain: str = ""
    external_port: int | None = None
    security: str
    network: str
    uuid: str
    private_key: str
    public_key: str
    short_id: str
    server_name: str
    spider_x: str
    cert_path: str
    key_path: str
    alpn: str
    transport_path: str
    ws_host: str
    xhttp_mode: str
    xhttp_x_padding_bytes: str
    xhttp_sc_max_each_post_bytes: str
    xhttp_sc_max_concurrent_posts: int
    xhttp_extra: str
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------- Dashboard ----------------
class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    expired_users: int
    disabled_users: int
    online_connections: int
    xray_running: bool
    xray_pid: int | None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    total_traffic_bytes: int
    server_time: datetime
    extra: dict[str, Any] = {}
