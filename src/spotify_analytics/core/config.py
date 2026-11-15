from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment or an optional .env file."""

    model_config = SettingsConfigDict(
        env_prefix="SPOTIFY_ANALYTICS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/spotify-analytics.db"
    host: str = "127.0.0.1"
    port: int = 8000
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    redirect_uri: str = "http://127.0.0.1:8000/auth/callback"
    session_secret: str | None = None
