"""Application configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    server_host: str = "127.0.0.1"
    server_port: int = 8780
    debug: bool = False

    public_root_path: str = ""
    gateway_internal_token: str = ""

    # Gemini AI Studio
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    # Vertex AI
    vertex_enabled: bool = False
    vertex_project_id: str = ""
    vertex_location: str = "asia-northeast1"
    vertex_credentials_path: str = ""

    # OpenRouter (openrouter.ai — OpenAI-compatible)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_translate_gemini: bool = False
    openrouter_default_model: str = "deepseek/deepseek-v4-flash"
    openrouter_price_per_m_in_usd: float = 0.09
    openrouter_price_per_m_out_usd: float = 0.18

    # Proxy timeouts (seconds)
    proxy_timeout_seconds: float = 120.0
    proxy_stream_timeout_seconds: float = 600.0

    # SQLite traffic store
    usage_db_path: str = "data/usage.db"
    usage_log_path: str = ""

    # Gemini cost (legacy records)
    gemini_price_per_m_in_usd: float = 0.10
    gemini_price_per_m_out_usd: float = 0.40
    usd_krw_rate: float = 1380.0


settings = Settings()
