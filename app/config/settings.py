"""Application configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    server_host: str = "127.0.0.1"
    server_port: int = 8780
    debug: bool = False

    # Internal LAN auth — empty disables check (dev only).
    gateway_internal_token: str = ""

    # Gemini AI Studio (generativelanguage.googleapis.com)
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    # Vertex AI ({location}-aiplatform.googleapis.com)
    vertex_enabled: bool = False
    vertex_project_id: str = ""
    vertex_location: str = "asia-northeast1"
    vertex_credentials_path: str = ""

    # Proxy timeouts (seconds)
    proxy_timeout_seconds: float = 120.0
    proxy_stream_timeout_seconds: float = 600.0

    # Optional usage log file (JSON lines). Empty = stdout only.
    usage_log_path: str = ""


settings = Settings()
