import json
import logging

import httpx
import pytest
import respx

from app.config import settings
from app.core.logging import REDACTED, RedactingFilter, mask_id, redact, register_secret


def make_record(msg, *args, exc_info=None):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, exc_info)


@pytest.mark.parametrize(
    "text",
    [
        "GET https://signin.definedgesecurities.com/auth/realms/debroking/dsbpkc/login/62e0aaaa-bbbb-cccc-dddd-eeeeeeeeeeee 200",
        "Authorization: Bearer abc.def.ghi",
        "api_secret=M+FAsecret",
        '{"api_session_key": "zzzzzzzz", "susertoken": "yyyyyyyy"}',
        "otp=123456",
        "password: hunter22",
    ],
)
def test_credential_shapes_scrubbed(text):
    out = redact(text)
    assert REDACTED in out
    for leak in ("62e0aaaa", "abc.def.ghi", "M+FAsecret", "zzzzzzzz", "yyyyyyyy", "123456", "hunter22"):
        assert leak not in out


def test_registered_secret_scrubbed_anywhere():
    register_secret("my-custom-session-key-777")
    assert "my-custom-session-key-777" not in redact("weird place: xx-my-custom-session-key-777-yy")


def test_configured_secrets_are_scrubbed():
    for value in (settings.definedge_api_token, settings.definedge_api_secret, settings.definedge_totp_secret, settings.api_auth_token):
        assert value not in redact(f"leak: {value}")


def test_account_ids_masked():
    assert mask_id("ABC1234") == "*****34"
    assert "ABC1234" not in redact("login ok uid=ABC1234 actid=XYZ9876")


def test_filter_scrubs_args_and_exceptions():
    rec = make_record("failed for %s", settings.definedge_api_secret)
    RedactingFilter().filter(rec)
    assert settings.definedge_api_secret not in rec.getMessage()

    try:
        raise httpx.ConnectError(f"boom https://x/login/{settings.definedge_api_token}")
    except httpx.ConnectError:
        import sys

        rec = make_record("oops", exc_info=sys.exc_info())
    RedactingFilter().filter(rec)
    assert settings.definedge_api_token not in (rec.exc_text or "")


@respx.mock
async def test_full_login_flow_never_logs_secrets(caplog):
    """End to end: run the real TOTP login against a mocked broker and prove
    no credential, OTP-derived value or session key appears in any log line."""
    from app.api.routes import auth
    from app.core.logging import setup_logging

    setup_logging(None)  # attach the redacting handlers (this also detaches caplog's)
    logging.getLogger().addHandler(caplog.handler)
    handler_filters = [f for h in logging.getLogger().handlers for f in h.filters]
    assert any(isinstance(f, RedactingFilter) for f in handler_filters)

    secrets_in_play = {"api_key": "API-SESSION-KEY-ZZZ999", "ws_key": "WS-SESSION-KEY-QQQ888", "otp_token": "OTP-TOKEN-ABC123"}
    respx.get(url__regex=r".*/login/.*").mock(return_value=httpx.Response(200, json={"otp_token": secrets_in_play["otp_token"]}))
    respx.post(url__regex=r".*/token").mock(
        return_value=httpx.Response(200, json={"uid": "UID1234", "actid": "ACT5678", "api_session_key": secrets_in_play["api_key"], "susertoken": secrets_in_play["ws_key"]})
    )
    auth._client._session = None
    auth._client._login_http = httpx.AsyncClient(base_url="https://signin.definedgesecurities.com/auth/realms/debroking/dsbpkc")
    logging.getLogger().setLevel(logging.DEBUG)
    # caplog attaches its own handler; apply the same filter the app's handlers use
    caplog.handler.addFilter(RedactingFilter())
    with caplog.at_level(logging.DEBUG):
        await auth.ensure_authenticated(force=True)
        logging.getLogger("t").info("session %r", auth._client._session)
    assert caplog.records, "flow produced no log records — the leak check would be vacuous"
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "uid=" in blob  # the flow really logged the (masked) account id
    for secret in [*secrets_in_play.values(), settings.definedge_api_token, settings.definedge_api_secret, "UID1234", "ACT5678"]:
        assert secret not in blob, f"leaked {secret!r}"
    # and nothing sensitive was persisted in plaintext
    from app.db.sqlite import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT api_session_key, ws_session_key FROM broker_sessions").fetchone()
    assert secrets_in_play["api_key"] not in row["api_session_key"] and row["api_session_key"].startswith("enc:")
    json.dumps(blob)
