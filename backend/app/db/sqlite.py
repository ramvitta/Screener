import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS broker_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    broker          TEXT NOT NULL DEFAULT 'definedge',
    uid             TEXT NOT NULL,
    actid           TEXT NOT NULL,
    api_session_key TEXT NOT NULL,
    ws_session_key  TEXT NOT NULL,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS backfill_progress (
    contract_id         TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    expiry              TEXT NOT NULL,
    strike              REAL NOT NULL,
    option_type         TEXT NOT NULL CHECK (option_type IN ('CE','PE')),
    last_completed_date TEXT,
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','complete','failed')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS screener_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_type   TEXT NOT NULL CHECK (strategy_type IN ('bull_put_spread','bear_call_spread','iron_condor')),
    symbol          TEXT NOT NULL DEFAULT 'NIFTY',
    is_active       INTEGER NOT NULL DEFAULT 1,
    params_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type      TEXT NOT NULL CHECK (alert_type IN ('screener_signal','profit_target','max_loss','margin_call')),
    strategy_type   TEXT,
    symbol          TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per run_screener_job() invocation, including empty/degraded runs,
-- so the history page can show "nothing found this run".
CREATE TABLE IF NOT EXISTS screener_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL UNIQUE,
    started_at      TEXT NOT NULL,
    data_source     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('ok','sample','degraded')),
    message         TEXT,
    signal_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per screener signal (accepted) or rejected candidate. decision_json
-- holds the full decision inputs (active params + computed values) so the
-- history page can reconstruct *why* a signal was emitted or rejected.
CREATE TABLE IF NOT EXISTS screener_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES screener_runs(run_id),
    signal_id       TEXT,
    strategy_type   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    expiry          TEXT,
    status          TEXT NOT NULL CHECK (status IN ('accepted','rejected')),
    reject_reason   TEXT,
    generated_at    TEXT NOT NULL,
    data_source     TEXT,
    reason          TEXT,
    reason_source   TEXT,
    signal_json     TEXT,
    decision_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_screener_signals_run ON screener_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_screener_signals_created ON screener_signals(created_at);

CREATE TABLE IF NOT EXISTS position_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_type   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    expiry          TEXT NOT NULL,
    legs_json       TEXT NOT NULL,
    entry_time      TEXT NOT NULL,
    exit_time       TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    profit_target   REAL,
    max_loss        REAL,
    realized_pnl    REAL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS trade_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL REFERENCES position_logs(id),
    entry_rationale TEXT,
    exit_rationale  TEXT,
    lessons_learned TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-leg detail for a position_logs row: normalizes what used to live only
-- in legs_json so each leg can carry its own live LTP, SL/TSL/target, a
-- roll-to strike, and its own close/exit — legs_json is kept in
-- position_logs for backward-compatible audit, this table is the source of
-- truth going forward (see Reqs/plan "Phase 2 — Positions & P&L tab").
CREATE TABLE IF NOT EXISTS position_legs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL REFERENCES position_logs(id),
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL CHECK (option_type IN ('CE','PE')),
    action          TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    qty             INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    ltp             REAL NOT NULL,
    sl              REAL,
    tsl_trigger     REAL,
    tsl_step        REAL,
    target          REAL,
    roll_to_strike  REAL,
    closed_at       TEXT,
    exit_price      REAL,
    realized_pnl    REAL
);

-- Persisted strategy-backtest runs, so the History & Backtest UI loads an
-- instant cached result instead of recomputing on every page visit; a
-- "Refresh history" action triggers a fresh run and overwrites this row.
CREATE TABLE IF NOT EXISTS strategy_backtests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_type   TEXT NOT NULL,
    symbol          TEXT NOT NULL DEFAULT 'NIFTY',
    params_json     TEXT NOT NULL DEFAULT '{}',
    run_at          TEXT NOT NULL DEFAULT (datetime('now')),
    summary_json    TEXT NOT NULL,
    results_json    TEXT NOT NULL
);

-- Append-only, hash-chained log of money events (see db/audit.py).
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    event           TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- Admin historical-data-download jobs (see api/routes/admin.py and
-- integrations/history_downloader.py). Proactive archival only — a stock's
-- CURRENTLY-listed equity/future/option contracts, not expired ones (the
-- broker's history endpoint stops serving a contract once it rolls off the
-- live instrument master; confirmed via scripts/stock_history_probe.py).
CREATE TABLE IF NOT EXISTS download_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    scope_json      TEXT NOT NULL,  -- {"equity":true,"futures":true,"options":true,"strike_band":10}
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','cancelled')),
    total_items     INTEGER NOT NULL DEFAULT 0,
    completed_items INTEGER NOT NULL DEFAULT 0,
    total_rows      INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS download_job_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES download_jobs(id),
    instrument_key  TEXT NOT NULL,  -- e.g. "EQUITY|RELIANCE" or "OPTION|RELIANCE|2026-10-27|1170|CE"
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed','skipped')),
    rows            INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- position_logs.strategy_type was created without a CHECK (SQLite can't add
-- one to an existing table), so enforce it with triggers that also apply to
-- databases created before this guard existed.
CREATE TRIGGER IF NOT EXISTS position_logs_strategy_insert BEFORE INSERT ON position_logs
WHEN NEW.strategy_type NOT IN ('bull_put_spread','bear_call_spread','iron_condor','straddle','strangle')
BEGIN SELECT RAISE(ABORT, 'invalid strategy_type'); END;

CREATE TRIGGER IF NOT EXISTS position_logs_strategy_update BEFORE UPDATE OF strategy_type ON position_logs
WHEN NEW.strategy_type NOT IN ('bull_put_spread','bear_call_spread','iron_condor','straddle','strangle')
BEGIN SELECT RAISE(ABORT, 'invalid strategy_type'); END;

CREATE TRIGGER IF NOT EXISTS position_legs_qty_insert BEFORE INSERT ON position_legs
WHEN NEW.qty <= 0 OR NEW.entry_price < 0 OR NEW.strike <= 0
BEGIN SELECT RAISE(ABORT, 'invalid leg qty/price/strike'); END;

-- Market breadth snapshots (workers/breadth_loop.py). One row per
-- (bucket, ts): bucket is 'hour' | 'day' | 'week' | 'month'; metrics_json is
-- free-form ({advancers, decliners, unchanged, breadth_ratio, pct_above:{..},
-- total, new_highs, new_lows}) so the shape can evolve without a migration.
CREATE TABLE IF NOT EXISTS breadth_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket          TEXT NOT NULL CHECK (bucket IN ('hour','day','week','month')),
    ts              TEXT NOT NULL,
    metrics_json    TEXT NOT NULL,
    computed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(bucket, ts)
);

-- Morning daily byte: raw Telegram messages (append-only, deduped on
-- chat_id+message_id). Kept verbatim so future intelligence never needs to
-- re-fetch Telegram.
CREATE TABLE IF NOT EXISTS daily_byte_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_slot      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    chat_username   TEXT,
    chat_title      TEXT,
    message_id      INTEGER NOT NULL,
    posted_at       TEXT NOT NULL,
    posted_local    TEXT,
    edit_date       TEXT,
    is_pinned       INTEGER NOT NULL DEFAULT 0,
    is_forward      INTEGER NOT NULL DEFAULT 0,
    sender_id       INTEGER,
    sender_username TEXT,
    sender_name     TEXT,
    is_bot          INTEGER NOT NULL DEFAULT 0,
    reply_to_msg_id INTEGER,
    text            TEXT,
    entities_json   TEXT,
    media_type      TEXT,
    media_json      TEXT,
    views           INTEGER,
    forwards        INTEGER,
    reactions_json  TEXT,
    word_count      INTEGER,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(chat_id, message_id)
);

-- Validated LLM extraction, one per message, versioned (idempotent re-runs,
-- evolvable prompt/schema without a migration). extracted_json holds the full
-- structured entities; sentiment/summary are denormalized for cheap queries.
CREATE TABLE IF NOT EXISTS daily_byte_analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    message_fk        INTEGER NOT NULL REFERENCES daily_byte_messages(id),
    analysis_version  TEXT NOT NULL,
    model             TEXT NOT NULL,
    extracted_json    TEXT NOT NULL,
    summary           TEXT,
    sentiment         TEXT,
    symbols_json      TEXT,
    events_json       TEXT,
    confidence        REAL,
    is_valid          INTEGER NOT NULL DEFAULT 1,
    analyzed_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(message_fk, analysis_version)
);

-- Daily digest: the synthesized "byte" for a calendar day, combining all
-- configured sources into one brief.
CREATE TABLE IF NOT EXISTS daily_byte_digests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    digest_json     TEXT NOT NULL,
    summary         TEXT,
    market_bias     TEXT,
    top_symbols_json TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(date)
);

-- One classification per Telegram message. Kept separate from the raw
-- archive so a message is classified exactly once per (re)run and the
-- category/structured extraction is queryable without parsing text.
CREATE TABLE IF NOT EXISTS telegram_message_classifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_fk      INTEGER NOT NULL UNIQUE REFERENCES daily_byte_messages(id) ON DELETE CASCADE,
    category        TEXT NOT NULL CHECK (category IN ('trade_alert','trade_update','trade_closure','result_calendar','market_commentary','promo','other')),
    method          TEXT NOT NULL,  -- regex | llm | llm+regex | none
    confidence      REAL,
    is_trade_call   INTEGER NOT NULL DEFAULT 0,
    summary         TEXT,
    extracted_json  TEXT NOT NULL,
    is_valid        INTEGER NOT NULL DEFAULT 1,
    classified_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tg_class_category ON telegram_message_classifications(category);

-- One row per trade call (a single alert message may contain several).
-- status tracks the call to closure via either a follow-up Telegram message
-- (status_source='message') or market-price tracking (status_source='price').
CREATE TABLE IF NOT EXISTS telegram_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_fk      INTEGER NOT NULL REFERENCES daily_byte_messages(id) ON DELETE CASCADE,
    chat_id         INTEGER NOT NULL,
    chat_username   TEXT,
    chat_title      TEXT,
    message_id      INTEGER NOT NULL,
    posted_at       TEXT NOT NULL,
    call_index      INTEGER NOT NULL DEFAULT 1,
    symbol          TEXT NOT NULL,
    symbol_raw      TEXT,
    display_name    TEXT,
    segment         TEXT NOT NULL DEFAULT 'other' CHECK (segment IN ('index','index_option','index_future','equity','stock_option','stock_future','commodity','commodity_option','commodity_future','other')),
    exchange        TEXT,
    direction       TEXT NOT NULL CHECK (direction IN ('long','short','neutral')),
    action          TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    option_type     TEXT CHECK (option_type IN ('CE','PE')),
    strike          REAL,
    expiry          TEXT,
    expiry_text     TEXT,
    entry_low       REAL,
    entry_high      REAL,
    entry_price     REAL,
    stop_loss       REAL,
    target_1        REAL,
    target_2        REAL,
    target_3        REAL,
    cmp             REAL,
    horizon         TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','target_hit','sl_hit','closed','expired')),
    status_reason   TEXT,
    status_source   TEXT,  -- message | price | manual
    exit_price      REAL,
    closed_at       TEXT,
    realized_pct    REAL,
    r_multiple      REAL,
    parent_call_id  INTEGER REFERENCES telegram_calls(id) ON DELETE SET NULL,
    confidence      REAL,
    method          TEXT NOT NULL,
    is_valid        INTEGER NOT NULL DEFAULT 1,
    extraction_json TEXT NOT NULL,
    tracking_status TEXT,  -- tracked | unresolved | no_data | error | contract_expired
    tracking_message TEXT,
    last_checked_at TEXT,
    last_price      REAL,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(message_fk, call_index)
);

CREATE INDEX IF NOT EXISTS idx_tg_calls_status ON telegram_calls(status);
CREATE INDEX IF NOT EXISTS idx_tg_calls_symbol ON telegram_calls(symbol);
CREATE INDEX IF NOT EXISTS idx_tg_calls_chat ON telegram_calls(chat_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_tg_calls_posted ON telegram_calls(posted_at);

-- Follow-up events (target hit, SL hit, booked profit, trail, manual update).
-- call_id is nullable so an unmatched closure message is still retained for
-- review rather than silently dropped.
CREATE TABLE IF NOT EXISTS telegram_call_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         INTEGER REFERENCES telegram_calls(id) ON DELETE SET NULL,
    message_fk      INTEGER NOT NULL REFERENCES daily_byte_messages(id) ON DELETE CASCADE,
    chat_id         INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    posted_at       TEXT NOT NULL,
    event_index     INTEGER NOT NULL DEFAULT 1,
    event_type      TEXT NOT NULL CHECK (event_type IN ('alert','target_hit','sl_hit','closed','partial_exit','trail','update')),
    symbol          TEXT,
    option_type     TEXT CHECK (option_type IN ('CE','PE')),
    strike          REAL,
    expiry          TEXT,
    price           REAL,
    target_value    REAL,
    realized_pct    REAL,
    note            TEXT,
    method          TEXT NOT NULL,
    is_valid        INTEGER NOT NULL DEFAULT 1,
    extracted_json  TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(message_fk, event_index)
);

CREATE INDEX IF NOT EXISTS idx_tg_events_call ON telegram_call_events(call_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_tg_events_message ON telegram_call_events(message_fk);
"""

SCHEMA_VERSION = 6  # +Telegram trade-call classification / calls-to-closure tracking


def get_db_path() -> str:
    path = Path(settings.sqlite_path)
    os.makedirs(path.parent, exist_ok=True)
    return str(path)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # persistent per database file
        conn.executescript(SCHEMA)
        # Versioned marker so future migrations can branch on it.
        if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    """One transaction per `with` block: commits on success, rolls back on any
    exception, so multi-statement money changes are all-or-nothing."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
