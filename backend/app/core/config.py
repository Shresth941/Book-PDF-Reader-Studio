from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # The local reader has bounded upload and page limits.
    max_upload_bytes: int | None = 500 * 1024 * 1024
    runtime_mode: Literal["local", "hosted"] = "local"
    data_dir: Path = Path("local-data")
    max_pages: int = 1000
    worker_memory_mb: int = 3072
    minimum_free_memory_mb: int = 1024
    worker_timeout_seconds: int = 90
    model_timeout_seconds: int = 300
    models_dir: Path = Path("models")
    gemini_api_key: SecretStr | None = None
    google_readonly_access_token: SecretStr | None = None
    google_project_number: str = ""
    cloud_policy_reviewed_on: str = ""
    libretranslate_url: str | None = None
    libretranslate_api_key: str | None = None
    upload_dir: Path = Path("uploads")

    @property
    def origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]

settings = Settings()
