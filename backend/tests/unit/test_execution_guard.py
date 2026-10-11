import pytest

from app.config import settings
from app.core import execution_guard as eg
from app.core.execution_guard import OrderLimits, OrderRefused, OrderRequest, authorize_order

LIMITS = OrderLimits(max_order_value=100_000, max_daily_loss=5_000, realized_loss_today=0, symbol_allowlist=["NIFTY"], expected_confirmation="CONFIRM-123")


def good(**kw):
    base = dict(symbol="NIFTY", qty=1, price=100.0, dry_run=False, confirmation_token="CONFIRM-123")
    base.update(kw)
    return OrderRequest(**base)


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "trading_mode", "live")
    monkeypatch.setattr(eg, "KILL_SWITCH_FILE", tmp_path / "KILL")
    return tmp_path / "KILL"


def test_disabled_by_default():
    assert settings.trading_mode == "disabled"
    with pytest.raises(OrderRefused, match="disabled"):
        authorize_order(good(), LIMITS)


def test_all_conditions_satisfied_passes(live):
    authorize_order(good(), LIMITS)


@pytest.mark.parametrize(
    "order,match",
    [
        (good(dry_run=True), "Dry-run"),
        (good(qty=0), "Invalid"),
        (good(price=float("nan")), "Invalid"),
        (good(symbol="BANKNIFTY"), "allowlist"),
        (good(qty=10_000, price=100), "per-order limit"),
        (good(confirmation_token=None), "confirmation"),
        (good(confirmation_token="nope"), "confirmation"),
    ],
)
def test_refusals(live, order, match):
    with pytest.raises(OrderRefused, match=match):
        authorize_order(order, LIMITS)


def test_daily_loss_limit(live):
    with pytest.raises(OrderRefused, match="Daily loss"):
        authorize_order(good(), OrderLimits(100_000, 5_000, 5_000, ["NIFTY"], "CONFIRM-123"))


def test_kill_switch_overrides_everything(live):
    live.write_text("stop")
    with pytest.raises(OrderRefused, match="Kill switch"):
        authorize_order(good(), LIMITS)


def test_dry_run_is_default():
    assert OrderRequest("NIFTY", 1, 1.0).dry_run is True
