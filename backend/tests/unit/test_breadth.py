"""Pure-function tests for market breadth (app/screener/breadth.py) and the
daily-byte digest synthesis (app/reasoning/byte_analyzer.py). No network."""

from datetime import date, datetime, timedelta

from app.models.schemas import DailyByteAnalysis
from app.reasoning.byte_analyzer import synthesize_digest
from app.screener.breadth import daily_breadth_series, hourly_breadth_series, period_breadth_series


def _daily(per_symbol: dict):
    return {sym: [(d, c) for d, c in series] for sym, series in per_symbol.items()}


def test_daily_advance_decline_counts():
    d = date(2026, 1, 1)
    closes = {
        "AAA": [(d + timedelta(days=k), 100 + k) for k in range(10)],
        "BBB": [(d + timedelta(days=k), 100 - k) for k in range(10)],
        "CCC": [(d + timedelta(days=k), 100) for k in range(10)],
    }
    snaps = daily_breadth_series(closes, [])
    last = snaps[-1]
    assert last["advancers"] == 1  # AAA up
    assert last["decliners"] == 1  # BBB down
    assert last["unchanged"] == 1  # CCC flat
    assert last["total"] == 3
    # A/D ratio is undefined when decliners == 0; here it's 1.0
    assert last["breadth_ratio"] == 1.0


def test_daily_breadth_ratio_none_when_no_decliners():
    d = date(2026, 1, 1)
    closes = {"AAA": [(d + timedelta(days=k), 100 + k) for k in range(5)]}
    snaps = daily_breadth_series(closes, [])
    assert snaps[-1]["breadth_ratio"] is None


def test_pct_above_dma():
    d = date(2026, 1, 1)
    up = [(d + timedelta(days=k), 100 + k) for k in range(20)]
    down = [(d + timedelta(days=k), 100 - k) for k in range(20)]
    snaps = daily_breadth_series({"UP": up, "DOWN": down}, [5])
    last = snaps[-1]
    assert last["pct_above"]["5"] == 50.0  # one above, one below


def test_weekly_and_monthly_bucketing():
    d = date(2026, 1, 5)  # a Monday
    series = [(d + timedelta(days=k), 100 + k) for k in range(35)]  # 5 weeks
    weekly = period_breadth_series({"AAA": series}, [20], "week")
    monthly = period_breadth_series({"AAA": series}, [20], "month")
    assert len(weekly) == 5
    assert len(monthly) == 2  # Jan + Feb


def test_hourly_intraday_breadth():
    t0 = datetime(2026, 1, 1, 9, 15)
    mins = {
        "AAA": [(t0 + timedelta(minutes=m), 100 + m) for m in range(0, 180, 5)],
        "BBB": [(t0 + timedelta(minutes=m), 100 - m) for m in range(0, 180, 5)],
    }
    snaps = hourly_breadth_series(mins)
    # ~3 hourly buckets (09:15-11:xx)
    assert len(snaps) >= 2
    assert "pct_above" not in snaps[-1]  # intraday has no DMA
    assert snaps[-1]["advancers"] == 1 and snaps[-1]["decliners"] == 1


def test_synthesize_digest_picks_mode_bias_and_dedupes_symbols():
    a = DailyByteAnalysis(sentiment="bullish", symbols=[{"ticker": "RELIANCE", "direction": "long"}])
    b = DailyByteAnalysis(sentiment="bullish", symbols=[{"ticker": "RELIANCE", "direction": "long"}])
    c = DailyByteAnalysis(sentiment="bearish", symbols=[{"ticker": "TCS", "direction": "short"}])
    results = [
        {"analysis": a, "summary": "A", "is_valid": True},
        {"analysis": b, "summary": "B", "is_valid": True},
        {"analysis": c, "summary": "C", "is_valid": True},
    ]
    digest = synthesize_digest(results)
    assert digest["market_bias"] == "bullish"
    assert len(digest["top_symbols"]) == 2  # RELIANCE deduped
    assert digest["summary"] == "A B C"


def test_synthesize_digest_empty():
    digest = synthesize_digest([])
    assert digest["market_bias"] is None
    assert digest["top_symbols"] == []
    assert digest["summary"] is None


def test_synthesize_digest_excludes_invalid_analyses():
    good = DailyByteAnalysis(sentiment="bullish", symbols=[{"ticker": "RELIANCE", "direction": "long"}])
    bad = DailyByteAnalysis(sentiment="unknown", symbols=[{"ticker": "CNC", "direction": None}])
    digest = synthesize_digest(
        [
            {"analysis": good, "summary": "A", "is_valid": True},
            {"analysis": bad, "summary": "B", "is_valid": False},
        ]
    )
    assert digest["market_bias"] == "bullish"
    assert [s["ticker"] for s in digest["top_symbols"]] == ["RELIANCE"]
    assert digest["summary"] == "A"
