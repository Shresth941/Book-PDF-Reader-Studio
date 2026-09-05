from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # This is a local, private application, so uploads are unlimited by default.
    # Set MAX_UPLOAD_BYTES to a positive byte count if a deployment needs a cap.
    max_upload_bytes: int | None = None
    libretranslate_url: str | None = None
    libretranslate_api_key: str | None = None
    upload_dir: Path = Path("uploads")

    @property
    def origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]

settings = Settings()
