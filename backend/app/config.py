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
    # Provider-neutral: any OpenAI-compatible /chat/completions endpoint
    # (OpenAI, Ollama cloud, Groq, OpenRouter, local Ollama, ...).
    llm_reason_enabled: bool = False
    llm_api_key: str = ""
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_reason_model: str = "gpt-4o-mini"
    llm_reason_max_signals: int = 5  # cost cap per screener run
    llm_reason_timeout_seconds: float = 20.0

    sessions_retention_hours: int = 24
    logs_retention_days: int = 14

    # --- Signal history (workstream 1) ---
    # Retention is NOT auto-enforced: pruning is a manual, admin-confirmed
    # action (POST /api/v1/admin/history/prune-signals) that deletes rows
    # older than this many days.
    signal_history_retention_days: int = 365

    # --- Admin historical-data-download (proactive archival) ---
    history_rps: float = 3.0  # max historical-candle requests/sec to Definedge
    history_max_retries: int = 3
    # Definedge's equity 1-min history is capped at roughly this many days
    # back regardless of the requested range (confirmed empirically via
    # scripts/stock_history_probe.py — 365d and 730d requests returned
    # identical data). Validate/download clamp to this rather than silently
    # returning less than asked for.
    history_equity_max_days_back: int = 180

    # --- str-stra screener (Definedge) ---
    # Screens the 50-stock watchlist through the same Definedge Integrate
    # connection the credit-spread screener uses (daily equity candles + live
    # NFO futures/options quotes). Signals are tagged data_source="live".
    str_stra_enabled: bool = True
    str_stra_interval_minutes: int = 10
    # Daily-candle lookback used for ADX + candle indicators.
    str_stra_history_days: int = 90
    str_stra_atm_parity_pct: float = 2.0

    # --- Market breadth ---
    # Advance/decline + %-above-DMA breadth across the F&O (or explicit)
    # universe, computed in a background worker and persisted to SQLite.
    breadth_enabled: bool = True
    # Universe: "fno" (all F&O stocks from the instrument master), "watchlist"
    # (the 50-stock str-stra list), or a comma-separated explicit symbol list.
    breadth_universe: str = "fno"
    breadth_interval_minutes: int = 15
    # Comma-separated simple-moving-average periods for %-above-DMA metrics.
    breadth_dma_periods: str = "20,50,200"
    # Daily-candle lookback (enough to roll up weekly + monthly series).
    breadth_daily_history_days: int = 400
    # Intraday (hourly) breadth is expensive (~1-min candles per symbol) and
    # opt-in: set true to compute today's hourly advance/decline series.
    breadth_intraday_enabled: bool = False

    # --- Morning daily byte (Telegram via Telethon) ---
    # Pulls the latest messages from a set of Telegram channels/groups each
    # morning and stores them (raw + LLM-structured) for display and later
    # intelligence. Telethon reads message history (not just new messages).
    telegram_enabled: bool = False
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    # Telethon session file; first run does an interactive phone-code login
    # and persists the .session file here. A StringSession can override it.
    telegram_session_path: str = "./data/telegram.session"
    telegram_string_session: str = ""
    # Comma-separated channel/group usernames or ids to track.
    telegram_daily_byte_chats: str = ""
    # Local (IST) time to run the daily-byte job each trading day.
    telegram_daily_byte_hour: int = 9
    telegram_daily_byte_minute: int = 15
    telegram_daily_byte_timezone: str = "Asia/Kolkata"
    # How far back (hours) to fetch when building each day's byte.
    telegram_history_lookback_hours: int = 12

    # --- Daily byte LLM analysis (reuses llm_api_key/llm_api_base_url) ---
    # Structured extraction of the market entities in each daily-byte message,
    # plus a plain-language digest. Validated before storage; never in the
    # money path. Off unless both this and an LLM_API_KEY are set.
    llm_byte_enabled: bool = False
    # Empty falls back to llm_reason_model.
    llm_byte_model: str = ""
    # Cost cap: max messages analysed per daily-byte run.
    llm_byte_max_messages: int = 20


settings = Settings()
