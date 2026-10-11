import clickhouse_connect
from clickhouse_connect.driver.client import Client

from app.config import settings

CANDLES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS options_1min_candles
(
    symbol          LowCardinality(String),
    expiry          Date,
    strike          Decimal(10, 2),
    option_type     Enum8('CE' = 1, 'PE' = 2),
    ts              DateTime('Asia/Kolkata'),
    open            Decimal(10, 2),
    high            Decimal(10, 2),
    low             Decimal(10, 2),
    close           Decimal(10, 2),
    volume          UInt64,
    oi              UInt64,
    oi_change       Int64,
    source          Enum8('live' = 1, 'backfill' = 2, 'admin_download' = 3)
)
ENGINE = ReplacingMergeTree(ts)
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, expiry, strike, option_type, ts)
SETTINGS index_granularity = 8192
"""

# Equity and stock-future 1-min candles — not NIFTY-specific despite the name
# carried over from the original Phase-1 design; symbol distinguishes rows.
# Equity rows carry no real expiry, so they use the 1970-01-01 sentinel
# instead of Nullable(Date) (ClickHouse ORDER BY keys are simplest non-null).
UNDERLYING_CANDLES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS underlying_1min_candles
(
    symbol          LowCardinality(String),
    instrument_type Enum8('EQUITY' = 1, 'FUTURE' = 2),
    expiry          Date DEFAULT toDate('1970-01-01'),
    ts              DateTime('Asia/Kolkata'),
    open            Decimal(10, 2),
    high            Decimal(10, 2),
    low             Decimal(10, 2),
    close           Decimal(10, 2),
    volume          UInt64,
    oi              UInt64,
    source          Enum8('live' = 1, 'backfill' = 2, 'admin_download' = 3)
)
ENGINE = ReplacingMergeTree(ts)
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, instrument_type, expiry, ts)
SETTINGS index_granularity = 8192
"""

DEPTH_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS options_market_depth
(
    symbol       LowCardinality(String),
    expiry       Date,
    strike       Decimal(10, 2),
    option_type  Enum8('CE' = 1, 'PE' = 2),
    ts           DateTime('Asia/Kolkata'),
    bid_price    Array(Decimal(10,2)),
    bid_qty      Array(UInt64),
    ask_price    Array(Decimal(10,2)),
    ask_qty      Array(UInt64)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, expiry, strike, option_type, ts)
"""


OPTION_CANDLE_COLUMNS = [
    "symbol", "expiry", "strike", "option_type", "ts",
    "open", "high", "low", "close", "volume", "oi", "oi_change", "source",
]
UNDERLYING_CANDLE_COLUMNS = [
    "symbol", "instrument_type", "expiry", "ts",
    "open", "high", "low", "close", "volume", "oi", "source",
]


def insert_option_candles(client: Client, rows: list) -> int:
    """Batch insert; `rows` are tuples matching OPTION_CANDLE_COLUMNS order.
    ReplacingMergeTree means re-running the same insert for the same
    (symbol, expiry, strike, option_type, ts) is safe — duplicates are
    dropped on the next background merge, not immediately, so a `count()`
    right after a re-run may look inflated until ClickHouse merges."""
    if not rows:
        return 0
    client.insert("options_1min_candles", rows, column_names=OPTION_CANDLE_COLUMNS)
    return len(rows)


def insert_underlying_candles(client: Client, rows: list) -> int:
    if not rows:
        return 0
    client.insert("underlying_1min_candles", rows, column_names=UNDERLYING_CANDLE_COLUMNS)
    return len(rows)


def get_client() -> Client:
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )


def _migrate_candles_engine(client: Client) -> None:
    """One-time migration: options_1min_candles was originally plain
    MergeTree; the admin-download work needs ReplacingMergeTree so re-running
    a download job doesn't duplicate candles. ClickHouse can't ALTER a
    table's engine, so this drops and recreates — safe only because the
    table has always been empty (nothing has written to it yet; confirmed via
    `SELECT count()` before this migration was written). If that's no longer
    true by the time this runs, it refuses rather than silently drop data."""
    try:
        engine = client.command(
            "SELECT engine FROM system.tables WHERE database = {db:String} AND name = 'options_1min_candles'",
            parameters={"db": settings.clickhouse_database},
        )
    except Exception:
        return  # table doesn't exist yet — CREATE TABLE IF NOT EXISTS below handles it
    if engine and "MergeTree" in str(engine) and "Replacing" not in str(engine):
        count = client.command("SELECT count() FROM options_1min_candles")
        if count == 0:
            client.command("DROP TABLE options_1min_candles")
        else:
            raise RuntimeError(
                f"options_1min_candles has {count} rows under the old MergeTree engine — "
                "refusing to auto-migrate to ReplacingMergeTree. Migrate manually."
            )


def init_db() -> None:
    bootstrap = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )
    bootstrap.command(f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}")

    client = get_client()
    _migrate_candles_engine(client)
    client.command(CANDLES_TABLE_DDL)
    client.command(UNDERLYING_CANDLES_TABLE_DDL)
    client.command(DEPTH_TABLE_DDL)
