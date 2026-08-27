"""Translate Gemini generateContent format ↔ OpenAI chat.completions format."""

from __future__ import annotations

import json
from typing import Any


def gemini_body_to_openai(gemini_body: dict, model: str, stream: bool = False) -> dict:
    messages: list[dict] = []

    sys_inst = gemini_body.get("systemInstruction")
    if sys_inst:
        parts = sys_inst.get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if text:
            messages.append({"role": "system", "content": text})

    for content in gemini_body.get("contents", []):
        role = content.get("role", "user")
        if role == "model":
            role = "assistant"
        parts = content.get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts if "text" in p)
        messages.append({"role": role, "content": text})

    openai_body: dict[str, Any] = {"model": model, "messages": messages}
    if stream:
        openai_body["stream"] = True

    gen_config = gemini_body.get("generationConfig", {})
    if "temperature" in gen_config:
        openai_body["temperature"] = gen_config["temperature"]
    if "maxOutputTokens" in gen_config:
        openai_body["max_tokens"] = gen_config["maxOutputTokens"]
    if "topP" in gen_config:
        openai_body["top_p"] = gen_config["topP"]
    if "stopSequences" in gen_config:
        openai_body["stop"] = gen_config["stopSequences"]

    # JSON 구조화 출력: responseMimeType=application/json → response_format
    if gen_config.get("responseMimeType") == "application/json":
        openai_body["response_format"] = {"type": "json_object"}

    return openai_body


def openai_response_to_gemini(openai_resp: dict) -> dict:
    text = ""
    finish_reason = "STOP"
    choices = openai_resp.get("choices", [])
    if choices:
        choice = choices[0]
        text = (choice.get("message") or {}).get("content") or ""
        fr = choice.get("finish_reason")
        if fr == "length":
            finish_reason = "MAX_TOKENS"
        elif fr == "content_filter":
            finish_reason = "SAFETY"

    usage = openai_resp.get("usage", {})
    return {
        "candidates": [{
            "content": {"parts": [{"text": text}], "role": "model"},
            "finishReason": finish_reason,
            "index": 0,
        }],
        "usageMetadata": {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens", 0),
        },
    }


def openai_sse_chunk_to_gemini(chunk_data: str) -> str | None:
    """Convert a single OpenAI SSE data payload to Gemini SSE JSON. Returns None to skip."""
    stripped = chunk_data.strip()
    if stripped == "[DONE]":
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    choices = obj.get("choices", [])
    if not choices:
        return None
    delta = choices[0].get("delta", {})
    text = delta.get("content")
    if text is None:
        return None

    return json.dumps({
        "candidates": [{
            "content": {"parts": [{"text": text}], "role": "model"},
            "index": 0,
        }]
    }, ensure_ascii=False)
