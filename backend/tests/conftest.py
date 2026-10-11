"""Test environment. Env vars are set BEFORE `app` is imported, because
app.config.settings is read once at import time."""

import os
import tempfile

from cryptography.fernet import Fernet

_TMP = tempfile.mkdtemp(prefix="options-tests-")
os.environ.update(
    APP_ENV="test",
    API_AUTH_TOKEN="test-token-" + "x" * 32,
    SESSION_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    SQLITE_PATH=os.path.join(_TMP, "bootstrap.db"),
    DEFINEDGE_API_TOKEN="11111111-2222-3333-4444-555555555555",
    DEFINEDGE_API_SECRET="super-secret-api-secret-value",
    DEFINEDGE_TOTP_SECRET="JBSWY3DPEHPK3PXP",
    TRADING_MODE="disabled",
    ALLOW_SAMPLE_DATA="false",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.logging import register_settings_secrets  # noqa: E402
from app.core.security import reset_rate_limiters  # noqa: E402
from app.db import sqlite  # noqa: E402

AUTH = {"Authorization": f"Bearer {settings.api_auth_token}"}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite database and clean rate limits."""
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "app.db"))
    sqlite.init_db()
    reset_rate_limiters()
    register_settings_secrets(settings)
    yield


@pytest.fixture
def client():
    from app.main import app

    # Not used as a context manager on purpose: that would run the lifespan
    # (broker login, screener run) against the network.
    return TestClient(app)


@pytest.fixture
def auth():
    return dict(AUTH)
