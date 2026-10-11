from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    definedge_api_token: str = ""
    definedge_api_secret: str = ""
    definedge_totp_secret: str = ""

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_database: str = "options_platform"

    sqlite_path: str = "./data/app.db"

    nifty_moneyness_band_pct: float = 15.0
    screener_interval_minutes: int = 5

    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:3001"]

    # --- Security guardrails ---
    # Static bearer token required on every API/WebSocket call. Empty means the
    # app refuses to start (see main.py lifespan) rather than running open.
    api_auth_token: str = ""
    # Fernet key for encrypting broker session keys at rest. Empty means
    # session keys are not persisted at all.
    session_encryption_key: str = ""
    # Order placement is not implemented; anything other than "disabled" is
    # still refused by core/execution_guard.py unless every limit is supplied.
    trading_mode: str = "disabled"
    # Dev only: lets the screener emit signals from the hardcoded sample chain.
    allow_sample_data: bool = False
    # Signals older than this are withheld by /screener/signals.
    max_signal_age_seconds: int = 900

    # --- LLM "Reason for Trade" (off by default) ---
    llm_reason_enabled: bool = False
    anthropic_api_key: str = ""
    llm_reason_model: str = "claude-opus-5-5"
    llm_reason_max_signals: int = 5  # cost cap per screener run
    llm_reason_timeout_seconds: float = 20.0

    sessions_retention_hours: int = 24
    logs_retention_days: int = 14

    # --- Admin historical-data-download (proactive archival) ---
    history_rps: float = 3.0  # max historical-candle requests/sec to Definedge
    history_max_retries: int = 3
    # Definedge's equity 1-min history is capped at roughly this many days
    # back regardless of the requested range (confirmed empirically via
    # scripts/stock_history_probe.py — 365d and 730d requests returned
    # identical data). Validate/download clamp to this rather than silently
    # returning less than asked for.
    history_equity_max_days_back: int = 180


settings = Settings()
