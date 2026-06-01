"""Structured usage logging."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.settings import settings

logger = logging.getLogger("osh_ai_gateway.usage")


def _extract_usage(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    usage = payload.get("usageMetadata")
    if isinstance(usage, dict):
        return usage

    for candidate in payload.get("candidates", []) or []:
        if isinstance(candidate, dict):
            nested = candidate.get("usageMetadata")
            if isinstance(nested, dict):
                return nested
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

    line = json.dumps(record, ensure_ascii=False)
    logger.info(line)

    log_path = settings.usage_log_path.strip()
    if log_path:
        path_obj = Path(log_path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        with path_obj.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
