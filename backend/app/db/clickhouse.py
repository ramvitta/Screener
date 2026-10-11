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
    source          Enum8('live' = 1, 'backfill' = 2)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, expiry, strike, option_type, ts)
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


def get_client() -> Client:
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
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
    client.command(CANDLES_TABLE_DDL)
    client.command(DEPTH_TABLE_DDL)
