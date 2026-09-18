from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "notion2local"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./notion2local.sqlite3"
    db_auto_create: bool = True

    raw_storage_path: Path = Path("./data/raw")
    blob_storage_path: Path = Path("./data/blobs")

    notion_api_base_url: str = "https://api.notion.com/v1"
    notion_api_version: str = "2026-03-11"
    notion_token: SecretStr | None = None
    notion_webhook_secret: SecretStr | None = None
    setup_token: SecretStr | None = None

    public_base_url: str | None = None
    sync_timezone: str = "Asia/Shanghai"
    reconcile_time: str = "00:05"
    sync_poll_seconds: int = 15
    http_timeout_seconds: float = 30.0
    max_sync_depth: int = 100

    @property
    def notion_token_value(self) -> str | None:
        return self._secret_value(self.notion_token)

    @property
    def notion_webhook_secret_value(self) -> str | None:
        return self._secret_value(self.notion_webhook_secret)

    @property
    def setup_token_value(self) -> str | None:
        return self._secret_value(self.setup_token)

    @staticmethod
    def _secret_value(value: SecretStr | None) -> str | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        return raw or None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
