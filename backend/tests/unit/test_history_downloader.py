"""Unit coverage for the pure/SQLite helpers in history_downloader.py that
don't require Definedge or ClickHouse (the network paths are covered through
mocks in tests/api/test_admin_routes.py and tests/api/test_broker_backed_routes.py).
"""

from datetime import date, datetime, timedelta

from app.config import settings
from app.db.sqlite import get_conn
from app.integrations import history_downloader as hd
from app.models.schemas import DownloadScope


def test_clamp_equity_start_within_floor():
    recent = date.today() - timedelta(days=1)
    assert hd._clamp_equity_start(recent) == recent


def test_clamp_equity_start_before_floor():
    ancient = date.today() - timedelta(days=settings.history_equity_max_days_back + 100)
    assert hd._clamp_equity_start(ancient) > ancient


def test_option_candle_rows_shape():
    candles = [{"ts": "2025-01-01T09:15:00", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "oi": 50}]
    rows = hd._option_candle_rows("RELIANCE", date(2025, 1, 30), 1170.0, "CE", candles)
    assert len(rows) == 1
    symbol, expiry, strike, opt_type, ts, o, hi, lo, c, vol, oi, oi_chg, source = rows[0]
    assert (symbol, expiry, strike, opt_type) == ("RELIANCE", date(2025, 1, 30), 1170.0, "CE")
    assert ts == datetime(2025, 1, 1, 9, 15)
    assert (o, hi, lo, c, vol, oi, oi_chg, source) == (10.0, 11.0, 9.0, 10.5, 100, 50, 0, "admin_download")


def test_underlying_candle_rows_equity_uses_sentinel_expiry():
    candles = [{"ts": "2025-01-01T09:15:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "oi": 0}]
    rows = hd._underlying_candle_rows("RELIANCE", "EQUITY", None, candles)
    assert rows[0][2] == date(1970, 1, 1)  # sentinel expiry
    assert rows[0][1] == "EQUITY"


def test_underlying_candle_rows_future_keeps_expiry():
    candles = [{"ts": "2025-01-01T09:15:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "oi": 0}]
    rows = hd._underlying_candle_rows("RELIANCE", "FUTURE", date(2025, 1, 30), candles)
    assert rows[0][2] == date(2025, 1, 30)
    assert rows[0][1] == "FUTURE"


def test_rate_limiter_no_rps_returns_immediately():
    import asyncio

    limiter = hd._RateLimiter(0)
    assert asyncio.run(limiter.wait()) is None


def test_update_job_and_record_item():
    with get_conn() as conn:
        jid = conn.execute(
            "INSERT INTO download_jobs (symbol, start_date, end_date, scope_json, status) VALUES ('R', '2025-01-01', '2025-01-02', '{}', 'pending')"
        ).lastrowid
    hd._update_job(jid, status="running", total_items=2)
    hd._record_item(jid, "EQUITY|R", "done", 5)
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM download_jobs WHERE id = ?", (jid,)).fetchone()
        assert job["status"] == "running" and job["total_items"] == 2
        assert job["completed_items"] == 1 and job["total_rows"] == 5
        item = conn.execute("SELECT * FROM download_job_items WHERE job_id = ?", (jid,)).fetchone()
        assert item["instrument_key"] == "EQUITY|R" and item["status"] == "done"


def test_create_job_row_and_get_job_round_trip():
    jid = hd.create_job_row("RELIANCE", date(2025, 1, 1), date(2025, 1, 5), DownloadScope())
    job = hd.get_job(jid)
    assert job is not None and job.symbol == "RELIANCE" and job.status == "pending"


def test_cancel_job_not_running():
    assert hd.cancel_job(12345) is False


def test_cancel_job_running():
    hd._running_jobs[999] = object()
    hd._cancel_flags[999] = False
    try:
        assert hd.cancel_job(999) is True
        assert hd._cancel_flags[999] is True
    finally:
        hd._running_jobs.pop(999, None)
        hd._cancel_flags.pop(999, None)


def test_coverage_with_mock_clickhouse(monkeypatch):
    class _Result:
        def __init__(self, rows):
            self.result_rows = rows

    class _FakeClient:
        def __init__(self, results):
            self._results = results
            self._i = 0

        def query(self, sql, parameters=None):
            r = self._results[self._i]
            self._i += 1
            return _Result(r)

    import app.db.clickhouse as ch

    lo, hi = "2025-01-01 09:15:00", "2025-01-30 15:30:00"
    fake = _FakeClient([
        [(100, lo, hi)],   # option query
        [(50, lo, hi)],    # EQUITY query
        [(0, None, None)],  # FUTURE query -> empty, skipped
    ])
    monkeypatch.setattr(ch, "get_client", lambda: fake)
    rows = hd.coverage("RELIANCE")
    assert [r.instrument_type for r in rows] == ["OPTION", "EQUITY"]
    assert rows[0].count == 100 and rows[1].count == 50
