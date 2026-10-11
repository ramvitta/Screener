"""Encryption at rest for broker session keys (Fernet, key from .env)."""

from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_PREFIX = "enc:"


def _fernet() -> Optional[Fernet]:
    return Fernet(settings.session_encryption_key.encode()) if settings.session_encryption_key else None


def encryption_enabled() -> bool:
    return bool(settings.session_encryption_key)


def encrypt(value: str) -> str:
    f = _fernet()
    if f is None:
        raise RuntimeError("SESSION_ENCRYPTION_KEY is not set")
    return _PREFIX + f.encrypt(value.encode()).decode()


def decrypt(stored: str) -> Optional[str]:
    """Returns None if the value isn't encrypted by this key (e.g. a legacy
    plaintext row, or the key was rotated) — callers treat that as 'no
    restorable session' and fall back to a fresh login."""
    f = _fernet()
    if f is None or not stored.startswith(_PREFIX):
        return None
    try:
        return f.decrypt(stored[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        return None
