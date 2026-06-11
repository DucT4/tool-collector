import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    cdp_url: str = os.getenv("CDP_URL", "http://localhost:9222")
    gpm_api_base: str = os.getenv("GPM_API_BASE", "http://127.0.0.1:9495")
    gpm_profile_id: str = os.getenv("GPM_PROFILE_ID", "")
    mongo_url: str = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    mongo_db: str = os.getenv("MONGO_DB", "social_data")
    mongo_collection: str = os.getenv("MONGO_COLLECTION", "tiktok_comments")
    telegram_subscriber_collection: str = os.getenv("TELEGRAM_SUBSCRIBER_COLLECTION", "telegram_subscribers")
    scroll_times: int = _int_env("SCROLL_TIMES", 5)
    max_reply_clicks: int = _int_env("MAX_REPLY_CLICKS", 0)
    page_timeout_ms: int = _int_env("PAGE_TIMEOUT_MS", 30000)
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")


settings = Settings()
