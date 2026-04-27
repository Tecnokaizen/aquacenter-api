from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "aquacenter-api"
    app_version: str = "0.1.0"

    api_key: str = ""
    max_upload_mb: int = 50

    data_dir: str = "data"
    uploads_dir: str = "data/uploads"
    outputs_dir: str = "data/outputs"
    logs_dir: str = "data/logs"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    request_timeout_seconds: int = 180


settings = Settings()


def ensure_runtime_dirs() -> None:
    for p in [settings.data_dir, settings.uploads_dir, settings.outputs_dir, settings.logs_dir]:
        Path(p).mkdir(parents=True, exist_ok=True)
