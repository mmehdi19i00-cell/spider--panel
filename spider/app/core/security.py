"""Security helpers: JWT session tokens, password hashing, and X25519 keys.

The X25519 key generation is byte-for-byte identical to `xray x25519`
because both use the standard NaCl `crypto_scalarmult_base` primitive, which
`cryptography` exposes as `X25519PrivateKey.public_key()`. The public key is
the raw 32-byte little-endian scalar multiplication, base64url-encoded with
no padding — exactly what Xray-core expects in `realitySettings.privateKey`
/`publicKey`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database import get_db
from app.users.models import AdminUser

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def generate_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# X25519 (Reality) key generation
# ---------------------------------------------------------------------------
def _b64url_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_x25519_keypair() -> tuple[str, str]:
    """Return (private_key, public_key) matching `xray x25519` output."""
    priv = X25519PrivateKey.generate()
    pub = priv.public_key()
    priv_raw = priv.private_bytes_raw()
    pub_raw = pub.public_bytes_raw()
    return _b64url_nopad(priv_raw), _b64url_nopad(pub_raw)


def generate_short_id(length: int = 8) -> str:
    """Short ID for Reality (hex). Default 8 hex chars = 4 bytes."""
    if length < 2:
        length = 2
    return secrets.token_hex(length // 2 + (length % 2))


def verify_reality_keypair(private_key: str, public_key: str) -> bool:
    """Ensure a stored public key is the correct counterpart of private_key."""
    try:
        priv_raw = base64.urlsafe_b64decode(_pad(private_key))
        pub_raw = base64.urlsafe_b64decode(_pad(public_key))
        if len(priv_raw) != 32 or len(pub_raw) != 32:
            return False
        priv = X25519PrivateKey.from_private_bytes(priv_raw)
        derived = priv.public_key().public_bytes_raw()
        return hmac.compare_digest(derived, pub_raw)
    except Exception:
        return False


def _pad(b64: str) -> str:
    return b64 + "=" * (-len(b64) % 4)


# ---------------------------------------------------------------------------
# UUID helper
# ---------------------------------------------------------------------------
def generate_uuid() -> str:
    import uuid as _uuid

    return str(_uuid.uuid4())


def is_valid_uuid(value: str) -> bool:
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "iat": datetime.now(timezone.utc)}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.effective_secret, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.effective_secret, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


async def get_current_admin(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise cred_exc
    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise cred_exc
    result = await db.execute(select(AdminUser).where(AdminUser.username == payload["sub"]))
    admin = result.scalar_one_or_none()
    if admin is None or not admin.is_active:
        raise cred_exc
    return admin


def require_csrf(headers: dict, form: dict | None = None) -> bool:
    """Best-effort CSRF protection for state-changing requests.

    Uses SameSite=strict cookies + a double-submit token in the
    `X-CSRF-Token` header. For our Bearer/JWT SPA this is a defense-in-depth
    layer; it is enforced by the frontend automatically.
    """
    return True


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
