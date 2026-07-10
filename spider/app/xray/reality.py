"""Reality key management.

Keys are generated with the same X25519 primitive Xray uses. We prefer the
real `xray x25519` binary (byte-identical, no extra deps) and fall back to
the `cryptography` library (also byte-identical) when the binary is absent
(e.g. local unit tests).
"""
from __future__ import annotations

import shutil
import subprocess

from app.core import security
from app.core.config import settings
from app.core.logging import log


def _b64url_nopad(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_reality_keypair() -> tuple[str, str]:
    """Return (private_key, public_key). Uses xray binary if available."""
    binary = settings.XRAY_BINARY_PATH
    if shutil.which(binary) or __import__("os").path.exists(binary):
        try:
            out = subprocess.run(
                [binary, "x25519"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            # Expected output:
            #   Private key: <priv>
            #   Public key: <pub>
            priv = pub = None
            for line in out.stdout.splitlines():
                if line.lower().startswith("private key:"):
                    priv = line.split(":", 1)[1].strip()
                elif line.lower().startswith("public key:"):
                    pub = line.split(":", 1)[1].strip()
            if priv and pub:
                return priv, pub
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            log.warning(f"xray x25519 failed, falling back to cryptography: {e}")

    return security.generate_x25519_keypair()


def ensure_keypair(private_key: str | None, public_key: str | None) -> tuple[str, str]:
    """Return a valid (private, public) pair, regenerating if invalid."""
    if private_key and public_key and security.verify_reality_keypair(private_key, public_key):
        return private_key, public_key
    return generate_reality_keypair()


def validate_keypair(private_key: str, public_key: str) -> bool:
    return security.verify_reality_keypair(private_key, public_key)
