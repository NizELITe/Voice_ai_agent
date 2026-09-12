"""Application settings, loaded from environment variables (never hardcoded)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./patients.db"
    vapi_server_secret: str = ""
    # When true, tool calls without a valid x-vapi-secret header are still served.
    # Convenient while wiring things up; flip to false once the secret is set in Vapi.
    allow_unauthenticated_tools: bool = True


settings = Settings()
