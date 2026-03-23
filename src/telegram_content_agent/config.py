from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_channel_id: str
    moderation_bot_token: str
    moderation_chat_id: str
    publish_api_token: str
    telegram_api_base: str = "https://api.telegram.org"
    request_timeout_seconds: float = 30.0
    default_parse_mode: str | None = "HTML"
    default_link_style: str = "buttons"
    moderation_timezone: str = "Europe/Moscow"
    moderation_allowed_user_ids: list[int] = Field(default_factory=list)
    moderation_poll_interval_seconds: float = 1.0
    moderation_poll_timeout_seconds: int = 20
    scheduler_db_path: Path = Path("data/scheduled_posts.sqlite3")
    scheduler_poll_interval_seconds: float = 5.0
    scheduler_batch_size: int = 10
    scheduler_retry_delay_seconds: float = 60.0
    scheduler_max_attempts: int = 3
    articles_root_path: Path = Path("publications")
    articles_auto_sync_on_startup: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("moderation_allowed_user_ids", mode="before")
    @classmethod
    def parse_allowed_user_ids(cls, value: object) -> object:
        if value in (None, "", []):
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
