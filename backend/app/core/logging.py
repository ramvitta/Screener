"""Log redaction + structured logging.

Every log record passes through RedactingFilter, which scrubs known secrets
(registered by exact value) and common credential shapes (bearer tokens, the
broker login URL that carries the API token in its path, OTPs, session keys,
account ids) before any handler formats it.
"""

import json
import logging
import re
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Optional, Set

REDACTED = "[REDACTED]"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_registered_secrets: Set[str] = set()

# Order matters: the specific shapes run before the generic key=value ones.
_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(/login/)[^\s/?\"']+"), r"\1" + REDACTED),
    (
        re.compile(
            r"(?i)\b(api_secret|api_token|api_session_key|ws_session_key|susertoken|"
            r"totp_secret|otp_token|authorization|password|secret)\b(['\"]?\s*[:=]\s*['\"]?)[^\s,'\"&}]+"
        ),
        r"\1\2" + REDACTED,
    ),
    (re.compile(r"(?i)\b(otp(?:_code)?)\b(['\"]?\s*[:=]\s*['\"]?)\d{4,8}"), r"\1\2" + REDACTED),
    (re.compile(r"(?i)\b(uid|actid)\b(['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9]+)"), None),  # masked, see _mask_ids
]
_ID_PATTERN = _PATTERNS[-1][0]


def register_secret(value: Optional[str]) -> None:
    """Registers an exact secret value to be scrubbed from all future logs."""
    if value and len(value) >= 6:
        _registered_secrets.add(value)


def register_settings_secrets(s) -> None:
    for name in (
        "definedge_api_token",
        "definedge_api_secret",
        "definedge_totp_secret",
        "clickhouse_password",
        "api_auth_token",
        "session_encryption_key",
        "llm_api_key",
    ):
        register_secret(getattr(s, name, ""))


def mask_id(value: str) -> str:
    """Keeps only the last 2 characters of an account identifier."""
    return value if len(value) <= 2 else "*" * (len(value) - 2) + value[-2:]


def _mask_ids(match: "re.Match[str]") -> str:
    return f"{match.group(1)}{match.group(2)}{mask_id(match.group(3))}"


def redact(text: str) -> str:
    for secret in _registered_secrets:
        text = text.replace(secret, REDACTED)
    for pattern, repl in _PATTERNS:
        if repl is None:
            text = _ID_PATTERN.sub(_mask_ids, text)
        else:
            text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed format args — never let logging crash the app
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            # Exceptions (e.g. httpx errors) can embed secret-bearing URLs.
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def setup_logging(log_file: Optional[Path] = None, level: int = logging.INFO) -> None:
    """Replaces logging.basicConfig. Filter is attached to the *handlers* (not
    just loggers) so records from every library, including uvicorn and httpx,
    are scrubbed."""
    handlers: Iterable[logging.Handler]
    stream = logging.StreamHandler()
    handlers_list = [stream]
    if log_file is not None:
        # Full log goes to the rotating file; the console/stdout capture only
        # carries warnings and up, so nothing is duplicated into it.
        stream.setLevel(logging.WARNING)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers_list.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3))
    handlers = handlers_list
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        h.addFilter(RedactingFilter())
        h.setFormatter(JsonFormatter())
        root.addHandler(h)
    root.setLevel(level)
    # httpx/httpcore log full request URLs at INFO; the broker login URL
    # carries the API token in its path.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn installs its own handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
