"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
from os import getenv

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Runtime settings for local development and hackathon demos."""

    app_name: str = "Stone - HackAlem AI"
    database_path: str = getenv("DATABASE_PATH", "app.db")
    chroma_path: str = getenv("CHROMA_PATH", "chroma_data")
    openai_api_key: str | None = getenv("OPENAI_API_KEY") or None
    anthropic_api_key: str | None = getenv("ANTHROPIC_API_KEY") or None


settings = Settings()
