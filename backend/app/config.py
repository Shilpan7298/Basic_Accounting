"""Application configuration.

Everything that varies between the dev laptop, the office server and the
"no internet at all" future lives here. Adapters are selected by the
``extractor`` value, never by an import somewhere in the code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Urjapod Order & Invoicing System"
    debug: bool = False

    # --- storage -----------------------------------------------------------
    database_url: str = f"sqlite:///{REPO_ROOT / 'storage' / 'urjapod.db'}"
    storage_dir: Path = REPO_ROOT / "storage"
    template_dir: Path = Path(__file__).resolve().parent / "templates" / "documents"

    # --- extraction --------------------------------------------------------
    # The whole point of constraint #1: this string, and nothing else, decides
    # which document-understanding backend runs.
    extractor: Literal["anthropic", "openai_compatible", "manual"] = "manual"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    anthropic_base_url: str = "https://api.anthropic.com"

    # Points at Ollama / vLLM / LM Studio by default. No cloud, no key needed.
    openai_base_url: str = "http://localhost:11434/v1"
    openai_api_key: str = "not-needed-for-local"
    openai_model: str = "qwen2.5:14b"

    extractor_timeout_seconds: float = 120.0
    # Below this, the review screen refuses to let a one-click approve happen.
    extraction_review_threshold: float = 0.80

    # --- company -----------------------------------------------------------
    home_state_code: str = "24"  # Gujarat. Drives intra vs inter-state GST.

    # --- tally -------------------------------------------------------------
    tally_host: str = "localhost"
    tally_port: int = 9000
    tally_company_name: str = "Urjapod Energy Private Limited"

    # --- sessions ----------------------------------------------------------
    # The intended deployment is an office LAN over plain HTTP
    # (http://urjapod-server:8080), where a Secure cookie would never be sent
    # and nobody could stay signed in. Set this to true only when the app is
    # actually served over HTTPS, e.g. behind a reverse proxy or on a VPS.
    session_cookie_secure: bool = False

    # --- misc --------------------------------------------------------------
    default_actor: str = "system"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def upload_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def generated_dir(self) -> Path:
        return self.storage_dir / "generated"

    def ensure_dirs(self) -> None:
        for path in (self.storage_dir, self.upload_dir, self.generated_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
