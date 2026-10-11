"""Static guard: the app is read-only. Fails if any order-placement code
appears under app/ other than the guarded choke point in core/execution_guard.py."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
FORBIDDEN = re.compile(
    r"(place[_]?order|modify[_]?order|cancel[_]?order|/placeorder|/modify|/cancel|square[_]?off|\bplace_gtt\b|\bbasket[_]?order)",
    re.IGNORECASE,
)
ALLOWED_FILES = {"execution_guard.py"}


def test_no_order_placement_code_outside_guard():
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name in ALLOWED_FILES:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") and "not used" in stripped.lower():
                continue
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(APP)}:{lineno}: {stripped}")
    assert not offenders, "order-placement code found:\n" + "\n".join(offenders)


def test_broker_client_has_no_write_methods():
    from app.integrations.definedge_client import DefinedgeClient

    public = {n for n in dir(DefinedgeClient) if not n.startswith("_")}
    assert public <= {"login", "login_step1", "login_step2", "get_instrument_master", "get_quote", "get_historical_candles", "get_positions", "get_holdings", "get_limits", "close", "on_invalidated"}
