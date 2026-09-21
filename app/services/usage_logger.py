"""Structured usage logging."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.services import usage_store

logger = logging.getLogger("osh_ai_gateway.usage")


def _extract_provider(body: bytes) -> str | None:
    """응답에서 실제 서빙한 사업자 이름을 뽑는다. 없으면 None.

    OpenRouter 는 응답 본문에 `provider` 를 넣어 준다. Gemini 포맷 응답에는 없다
    (게이트웨이가 재조립하면서 버리므로) — 그래서 **여기서 원본 바이트를 본다.**
    ⚠️ 못 뽑는 것과 "없다" 를 구분하지 않는다 — 둘 다 None 이고 기록에서 빠진다.
       기록에 `provider` 가 없으면 **"그 회차는 모른다"** 로 읽어야지 통과가 아니다.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    provider = payload.get("provider")
    return provider if isinstance(provider, str) and provider else None


def _extract_usage(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    # Gemini format
    usage = payload.get("usageMetadata")
    if isinstance(usage, dict):
        return usage
    for candidate in payload.get("candidates", []) or []:
        if isinstance(candidate, dict):
            nested = candidate.get("usageMetadata")
            if isinstance(nested, dict):
                return nested

    # OpenAI-compatible format (OpenRouter, etc.)
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return usage

    return None


def log_request(
    *,
    backend: str,
    service_id: str,
    method: str,
    path: str,
    status_code: int,
    elapsed_ms: float,
    response_body: bytes | None = None,
) -> None:
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "backend": backend,
        "service_id": service_id or "unknown",
        "method": method,
        "path": path,
        "status": status_code,
        "elapsed_ms": round(elapsed_ms, 1),
    }
    if response_body:
        usage = _extract_usage(response_body)
        if usage:
            record["usage"] = usage
        # 🔎 실제로 서빙한 사업자를 남긴다 (2026-09-21)
        #
        # 왜: 모델은 고정이어도 OpenRouter 는 임의 사업자로 라우팅한다.
        #     기록이 없으면 **"어디로 갔는지 사후에 알 수 없다"** 가 그대로 남고,
        #     사업자 고정을 걸어도 **그것이 실제로 먹었는지 확인할 방법이 없다**
        #     (= "가드를 만들었다고 효과를 주장하지 않는다").
        # 비용: 라우팅을 제한하지 않는다. 순수 관측이라 가용성 영향 0.
        provider = _extract_provider(response_body)
        if provider:
            record["provider"] = provider

    line = json.dumps(record, ensure_ascii=False)
    logger.info(line)

    try:
        usage_store.insert_record(record)
    except Exception:
        logger.exception("failed to persist usage record")

    log_path = settings.usage_log_path.strip()
    if log_path:
        path_obj = Path(log_path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        with path_obj.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
