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
    # 🔒 서비스별 사업자(provider) 고정 — 2026-09-21
    #
    # 왜: 모델은 고정돼 있어도 OpenRouter 는 **매 호출 임의 사업자**로 라우팅한다
    #     (실측: 이 모델을 서비스하는 사업자 15곳). 민감 데이터를 보내는 서비스는
    #     "어디로 갔는지 사후에 알 수 없다" 가 된다.
    # 범위: **여기 적힌 service_id 에만** 적용한다. 다른 서비스는 분기에 들어가지도
    #     않으므로 라우팅이 그대로다(= "공용은 건드리지 않는다" 는 사용자 결정).
    # 형식: "서비스ID:사업자[,사업자...]" 를 `;` 로 구분. 비우면 아무 데도 적용 안 함.
    #     ⚠️ allow_fallbacks=false 와 함께 나가므로 **목록 밖으로는 안 나간다** —
    #        적은 사업자가 전부 죽으면 그 서비스의 LLM 호출은 실패한다(의도된 대가).
    #
    # 🔴 왜 한 곳이 아니라 세 곳인가 (2026-09-21 실측으로 바뀐 값)
    #   처음에 `simpleStock:deepinfra` 한 곳으로 걸었더니 **5회 중 3회 429**
    #   (성공률 40%). 같은 시각 고정 없는 대조군은 3/3 성공이었다.
    #   ⇒ "한 곳" 은 추적성은 최고지만 **이 워크로드에서 안 버틴다.**
    #   실측에서 실제로 응답한 세 곳으로 넓혔다. 데이터가 가는 곳은 15곳 → **3곳**으로
    #   바운드되고, 어느 회차가 어디로 갔는지는 usage_log.provider 가 알려준다.
    # ⚠️ 슬러그는 **공식 providers 목록과 모델 endpoints 의 tag 로 교차 확인**했다.
    #   `openinference` 가 아니라 **`open-inference`** 다 — 이름만 보고 적었으면
    #   allow_fallbacks=false 와 맞물려 **전부 실패**했을 자리다.
    openrouter_provider_pins: str = "simpleStock:open-inference,venice,deepinfra"
    # 학습 사용 거부. 고정 대상 서비스에만 함께 나간다.
    openrouter_provider_deny_training: bool = True
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
