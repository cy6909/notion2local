from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .runtime_secrets import read_secret_file


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
    notion_token_file: Path | None = Path("./data/config/notion_token")
    notion_webhook_secret: SecretStr | None = None
    setup_token: SecretStr | None = None

    public_base_url: str | None = None
    sync_timezone: str = "Asia/Shanghai"
    reconcile_time: str = "00:05"
    sync_poll_seconds: int = 15
    http_timeout_seconds: float = 30.0
    max_sync_depth: int = 100
    sync_checkpoint_objects: int = Field(default=50, ge=1, le=10000)

    @property
    def notion_token_value(self) -> str | None:
        return read_secret_file(self.notion_token_file) or self._secret_value(self.notion_token)

    @property
    def notion_token_source(self) -> str:
        if read_secret_file(self.notion_token_file):
            return "runtime_file"
        if self._secret_value(self.notion_token):
            return "environment"
        return "none"

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
