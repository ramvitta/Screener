"""Market-breadth computation — pure functions, no I/O, no numpy/pandas.

Given per-symbol daily (or intraday) closes, computes advance/decline breadth
and %-above-SMA at several granularities:

  * daily   — close vs previous day's close
  * weekly  — last close of this week vs last close of the prior week
  * monthly — last close of this month vs last close of the prior month
  * hourly  — last close of this hour vs the prior hour (intraday, opt-in)

All metrics are derived from the actual close series; a symbol contributes to
a bucket only when it has data for that bucket *and* the prior bucket (so
advance/decline is never a fabricated comparison).
"""

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple


def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _symbol_daily_rows(closes: List[Tuple[date, float]], dma_periods: List[int]) -> List[dict]:
    """Per-symbol daily rows with prev close and per-DMA above-flags."""
    s = sorted(closes)
    rows = []
    close_hist: List[float] = []
    for k, (d, c) in enumerate(s):
        prev = s[k - 1][1] if k > 0 else None
        close_hist.append(c)
        above = {p: c > _sma(close_hist, p) for p in dma_periods if _sma(close_hist, p) is not None}
        rows.append({"date": d, "close": c, "prev": prev, "above": above})
    return rows


def _bucket_key(d: date, bucket: str) -> Tuple[int, ...]:
    if bucket == "week":
        iso = d.isocalendar()
        return (iso[0], iso[1])
    if bucket == "month":
        return (d.year, d.month)
    raise ValueError(f"unsupported bucket {bucket!r}")


def _period_rows(per_symbol_rows: Dict[str, List[dict]], bucket: str) -> Dict[str, List[dict]]:
    """Re-bucket daily rows into weekly/monthly rows (last close per bucket)."""
    out: Dict[str, List[dict]] = {}
    for sym, rows in per_symbol_rows.items():
        buckets: Dict[Tuple[int, ...], dict] = {}
        for r in rows:
            buckets[_bucket_key(r["date"], bucket)] = r
        keys = sorted(buckets)
        bucket_rows = []
        for i, key in enumerate(keys):
            r = buckets[key]
            prev = buckets[keys[i - 1]]["close"] if i > 0 else None
            bucket_rows.append({"key": key, "date": r["date"], "close": r["close"], "prev": prev, "above": r["above"]})
        out[sym] = bucket_rows
    return out


def _breadth_from_rows(per_symbol_rows: Dict[str, List[dict]], dma_periods: List[int], ts_label: str) -> List[dict]:
    """Shared breadth aggregation: advance/decline (close vs prev close) plus
    %-above-DMA per bucket."""
    by_bucket: Dict[object, List[dict]] = {}
    for rows in per_symbol_rows.values():
        for r in rows:
            key = r.get("key", r["date"])
            by_bucket.setdefault(key, []).append(r)

    snapshots = []
    for key in sorted(by_bucket, key=lambda k: (k if isinstance(k, tuple) else (k,))):
        entries = by_bucket[key]
        total = len(entries)
        adv = sum(1 for r in entries if r["prev"] is not None and r["close"] > r["prev"])
        dec = sum(1 for r in entries if r["prev"] is not None and r["close"] < r["prev"])
        unch = sum(1 for r in entries if r["prev"] is not None and r["close"] == r["prev"])
        pct_above = {}
        for p in dma_periods:
            n = sum(1 for r in entries if r["above"].get(p) is True)
            pct_above[str(p)] = round(n / total * 100, 2) if total else None
        representative = entries[0]
        ts = representative["date"].isoformat() if hasattr(representative["date"], "isoformat") else representative["date"]
        snapshots.append(
            {
                "ts": ts,
                "advancers": adv,
                "decliners": dec,
                "unchanged": unch,
                "breadth_ratio": round(adv / dec, 3) if dec else None,
                "pct_above": pct_above,
                "total": total,
            }
        )
    return snapshots


def daily_breadth_series(per_symbol_closes: Dict[str, List[Tuple[date, float]]], dma_periods: List[int]) -> List[dict]:
    per_symbol_rows = {sym: _symbol_daily_rows(closes, dma_periods) for sym, closes in per_symbol_closes.items()}
    return _breadth_from_rows(per_symbol_rows, dma_periods, "day")


def period_breadth_series(
    per_symbol_closes: Dict[str, List[Tuple[date, float]]], dma_periods: List[int], bucket: str
) -> List[dict]:
    per_symbol_rows = {sym: _symbol_daily_rows(closes, dma_periods) for sym, closes in per_symbol_closes.items()}
    per_period = _period_rows(per_symbol_rows, bucket)
    return _breadth_from_rows(per_period, dma_periods, bucket)


def hourly_breadth_series(per_symbol_minutes: Dict[str, List[Tuple[datetime, float]]]) -> List[dict]:
    """Intraday A/D: hourly buckets, close vs prior hour's close (no DMA)."""
    per_symbol_rows: Dict[str, List[dict]] = {}
    for sym, minutes in per_symbol_minutes.items():
        s = sorted(minutes)
        hours: Dict[Tuple[int, int, int], dict] = {}
        for ts, close in s:
            key = (ts.year, ts.month, ts.day, ts.hour)
            hours[key] = {"date": ts, "close": close, "prev": None, "above": {}}
        keys = sorted(hours)
        rows = []
        for i, key in enumerate(keys):
            r = hours[key]
            r["prev"] = hours[keys[i - 1]]["close"] if i > 0 else None
            r["key"] = key
            rows.append(r)
        per_symbol_rows[sym] = rows
    # Hourly snapshots carry no %-above-DMA.
    snapshots = _breadth_from_rows(per_symbol_rows, [], "hour")
    for s in snapshots:
        s.pop("pct_above", None)
    return snapshots
