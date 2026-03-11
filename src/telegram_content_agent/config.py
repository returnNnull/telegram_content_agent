from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_channel_id: str
    publish_api_token: str
    telegram_api_base: str = "https://api.telegram.org"
    request_timeout_seconds: float = 30.0
    default_parse_mode: str | None = "HTML"
    default_link_style: str = "buttons"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
