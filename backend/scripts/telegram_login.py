"""One-time interactive Telethon login for the Morning Daily Byte.

Run once from a terminal to generate the .session file the worker reuses:

    cd backend && python scripts/telegram_login.py

You'll be prompted for the Telegram login code (sent to the phone) and, if
enabled, a 2FA password. The session is persisted at TELEGRAM_SESSION_PATH
(default ./data/telegram.session) so run_daily_byte_job() never re-prompts.
"""

import asyncio
import sys
from pathlib import Path

# Allow `python scripts/telegram_login.py` to run from any cwd: put the
# backend package root (the parent of scripts/) on sys.path so `app.*` imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.integrations.telegram_client import TelegramDailyByteClient


async def main() -> None:
    client = TelegramDailyByteClient()
    try:
        me = await client.interactive_login()
        print(f"Logged in as {me['username'] or me['name']} (id {me['id']})")
        print(f"Session persisted at {settings.telegram_session_path}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
