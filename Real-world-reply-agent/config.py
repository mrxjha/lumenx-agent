"""Central configuration loaded from environment / .env."""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""

    # --- Telegram bot connector ---
    telegram_bot_token: str = ""
    # When False, EVERY draft routes to human review even if the MLP is confident.
    # Safe default for cold-start + demo. Flip to True to enable the auto-send path.
    auto_send_enabled: bool = False

    confidence_threshold: float = 0.90
    poll_interval_sec: int = 30           # getUpdates long-poll timeout (seconds)

    intent_model: str = "claude-haiku-4-5-20251001"
    draft_model: str = "claude-sonnet-4-6"

    database_url: str = "sqlite:///data/agent.db"
    wiki_dir: str = "wiki/products"

    log_level: str = "INFO"

    @property
    def is_postgres(self) -> bool:
        """True when DATABASE_URL points at PostgreSQL (prod). SQLite otherwise (dev)."""
        return self.database_url.startswith(("postgres://", "postgresql://"))

    @property
    def sqlite_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            rel = self.database_url.replace("sqlite:///", "", 1)
            return (PROJECT_ROOT / rel).resolve()
        raise ValueError("sqlite_path is only valid for sqlite:/// URLs (got a non-sqlite DATABASE_URL)")

    @property
    def wiki_path(self) -> Path:
        return (PROJECT_ROOT / self.wiki_dir).resolve()


settings = Settings()
