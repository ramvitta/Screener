"""Read-only spike for the admin historical-data-download plan (see
~/.claude/plans/what-is-needed-in-rustling-curry.md). Answers, before any
real code is written:

1. What instrument_type/segment values the instrument master uses for stock
   options (OPTSTK?), stock futures (FUTSTK?), and NSE cash equity.
2. Whether the historical-candle endpoint serves 1-min data for an EXPIRED
   option contract at all, and how far back.
3. The expired-contract trading-symbol format, for later candidate
   generation.

Run from backend/: `source .venv/bin/activate && python scripts/stock_history_probe.py`
"""

import asyncio
from collections import Counter
from datetime import date, datetime, timedelta

from app.api.routes.auth import _client, ensure_authenticated
from app.integrations import instruments


async def main():
    await ensure_authenticated()
    rows = await instruments.get_master_rows(_client)
    print(f"Instrument master: {len(rows)} rows total")

    # --- Q1: what instrument_type/segment values exist for stocks? ---
    segments = Counter((r["segment"], r["instrument_type"]) for r in rows)
    print("\nTop (segment, instrument_type) pairs by row count:")
    for (seg, itype), count in segments.most_common(15):
        print(f"  {seg:8s} {itype:8s} {count}")

    # Look specifically for an NFO stock-options row and a stock-future row,
    # and an NSE equity row, by scanning for a well-known liquid stock.
    probe_symbol = "RELIANCE"
    stock_opt_rows = [r for r in rows if r["symbol"] == probe_symbol and r["segment"] == "NFO" and "OPT" in r["instrument_type"]]
    stock_fut_rows = [r for r in rows if r["symbol"] == probe_symbol and r["segment"] == "NFO" and "FUT" in r["instrument_type"]]
    equity_rows = [r for r in rows if r["segment"] == "NSE" and r["trading_symbol"] == probe_symbol]
    print(f"\n{probe_symbol}: {len(stock_opt_rows)} option rows, {len(stock_fut_rows)} future rows, {len(equity_rows)} equity rows")
    if stock_opt_rows:
        print("  sample option row:", stock_opt_rows[0])
    if stock_fut_rows:
        print("  sample future row:", stock_fut_rows[0])
    if equity_rows:
        print("  sample equity row:", equity_rows[0])

    # --- Q2/Q3: does the history endpoint serve an EXPIRED contract, and
    # what's the live trading-symbol format we'd need to replicate? ---
    if stock_opt_rows:
        live = stock_opt_rows[0]
        print(f"\nLive option trading_symbol format: {live['trading_symbol']!r} (expiry {live['expiry']}, strike {live['strike']}, token {live['token']})")
        end = datetime.now()
        start = end - timedelta(days=3)
        candles = await _client.get_historical_candles("NFO", live["token"], "minute", start, end)
        print(f"  live contract, last 3 days: {len(candles)} candles" + (f", last={candles[-1]}" if candles else ""))

    if equity_rows:
        eq = equity_rows[0]
        print(f"\nEquity token for {probe_symbol}: {eq['token']}")
        # Probe progressively further back to find where 1-min data runs out.
        for days_back in (7, 30, 90, 180, 365):
            end = datetime.now()
            start = end - timedelta(days=days_back)
            candles = await _client.get_historical_candles("NSE", eq["token"], "minute", start, end)
            print(f"  equity minute candles over last {days_back}d: {len(candles)}" + (f" (earliest={candles[0]['ts']})" if candles else ""))

    # --- Expired option contract: try to probe one using a plausible past
    # monthly expiry + a strike near where RELIANCE traded, using Definedge's
    # documented trading-symbol convention (SYMBOL+DDMMMYY+C/P+STRIKE), since
    # expired contracts aren't in the live master at all. ---
    if stock_opt_rows:
        live = stock_opt_rows[0]
        token_fmt_note = live["trading_symbol"]
        print(f"\nLive trading_symbol to infer expired-symbol format from: {token_fmt_note!r}")
        print("  (Definedge's history endpoint takes a TOKEN, not a trading symbol, for live contracts;")
        print("   an expired contract has no master row and thus no token — this is the open question")
        print("   the plan flags: can expired options be probed at all without a token?)")


if __name__ == "__main__":
    asyncio.run(main())
