from datetime import date

import pytest

from app.integrations import instruments
from app.screener import technicals


def test_rsi_bounds_and_short_series():
    assert technicals.rsi([1, 2, 3]) == 50.0
    assert technicals.rsi(list(range(1, 40))) == 100.0
    assert technicals.rsi(list(range(40, 1, -1))) == 0.0
    mixed = [100 + (i % 3) for i in range(60)]
    assert 0 <= technicals.rsi(mixed) <= 100


def test_ema_and_support_resistance_and_bias():
    assert technicals.ema([], 5) == 0.0
    assert technicals.ema([10, 10, 10], 5) == 10.0
    assert technicals.support_resistance([]) == (0.0, 0.0)
    assert technicals.support_resistance([5, 9, 7]) == (5, 9)
    assert technicals.technical_bias(70) == "mildly overbought"
    assert technicals.technical_bias(30) == "mildly oversold"
    assert technicals.technical_bias(50) == "neutral, range-bound"


ROWS = [
    {"segment": "NSE", "instrument_type": "IDX", "trading_symbol": "Nifty 50", "token": "26000", "symbol": "Nifty 50", "expiry": "", "option_type": "", "strike": "0"},
    {"segment": "NFO", "instrument_type": "FUTIDX", "symbol": "NIFTY", "trading_symbol": "NIFTYFUT", "token": "9", "expiry": "28102026", "option_type": "", "strike": "0"},
    {"segment": "NFO", "instrument_type": "OPTIDX", "symbol": "NIFTY", "trading_symbol": "N-CE", "token": "1", "expiry": "08102026", "option_type": "CE", "strike": "21800"},
    {"segment": "NFO", "instrument_type": "OPTIDX", "symbol": "NIFTY", "trading_symbol": "N-PE", "token": "2", "expiry": "08102026", "option_type": "PE", "strike": "21800"},
]


def test_instrument_lookups():
    assert instruments.find_spot(ROWS).token == "26000"
    assert instruments.find_future(ROWS, "NIFTY", date(2026, 10, 1)).token == "9"
    assert instruments.nearest_expiry(ROWS, "NIFTY", date(2026, 10, 1)) == date(2026, 10, 8)
    assert instruments.find_option(ROWS, "NIFTY", date(2026, 10, 8), 21800, "PE").token == "2"


def test_instrument_lookup_failures_are_typed():
    with pytest.raises(instruments.ContractNotFound):
        instruments.find_option(ROWS, "NIFTY", date(2026, 10, 8), 99999, "CE")
    with pytest.raises(instruments.ContractNotFound):
        instruments.nearest_expiry(ROWS, "NIFTY", date(2030, 1, 1))
    with pytest.raises(instruments.ContractNotFound):
        instruments.find_future(ROWS, "NIFTY", date(2030, 1, 1))
