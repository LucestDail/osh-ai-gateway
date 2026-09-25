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

    # 🔴 thinking 제어를 **번역**한다 — 2026-09-23 (pm2)
    #
    # 왜: 종전엔 `thinkingConfig` 를 읽지도 않았다. 그래서 앱이 "생각 끄기" 를 보내도
    #     아무 일도 안 일어났고, simpleStock 브리핑이 이틀 연속 죽었다.
    #     실측(2026-09-23 10:05): market_briefing 이
    #       candidatesTokens 2400 · **thoughtsTokens 2400** · 본문 **0토큰**
    #     ⇒ 출력 예산 **전부를 생각이 먹고 답이 안 나왔다.**
    #
    # ⚠️ `exclude` 가 아니라 `enabled: false` 를 쓴다 —
    #    OpenRouter 에서 `exclude:true` 는 "응답에 안 싣는다" 일 뿐 **생각은 계속한다**(시간·과금 그대로).
    #    우리가 필요한 건 **생성 자체를 멈추는 것**이다.
    # ⚠️ 호출자가 thinkingConfig 를 **안 보내면 아무것도 안 붙인다** — 기존 서비스 동작 불변.
    thinking = gen_config.get("thinkingConfig") or {}
    if thinking:
        budget = thinking.get("thinkingBudget")
        level = thinking.get("thinkingLevel")
        if budget == 0 or (isinstance(level, str) and level.lower() in ("none", "off")):
            openai_body["reasoning"] = {"enabled": False}
        elif isinstance(budget, int) and budget > 0:
            openai_body["reasoning"] = {"max_tokens": budget}
        elif isinstance(level, str) and level.lower() in ("low", "medium", "high"):
            openai_body["reasoning"] = {"effort": level.lower()}
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
    # 🔴 reasoning(thinking) 토큰을 살려서 넘긴다 — 2026-09-23 (pm2)
    #
    # 왜: simpleStock 브리핑이 이틀 연속 죽었다. 증상은 "출력이 캡에서 잘림" 인데,
    #     실측에서 **completion_tokens 1,356 인데 content 는 0자** 였다. OpenAI 형식은
    #     reasoning 토큰을 **completion_tokens 안에 포함**해서 세므로, 답이 긴 게 아니라
    #     **생각이 예산을 먹은 것**인지 여기서 가르지 않으면 앱은 영영 알 수 없다.
    #     (앱이 thoughtsTokenCount 를 읽을 준비를 해 뒀는데 우리가 안 보내고 있었다.)
    # ⚠️ **필드 추가만** 한다. 기존 세 값과 candidates 는 한 글자도 안 바꾼다 —
    #    이 함수는 HARU·osh·myapi·aim-monitor·my-computer 가 공용으로 탄다.
    # ⚠️ 없으면 **0 이 아니라 생략**한다. "안 쟀다" 와 "0 이다" 는 다르다.
    detail = usage.get("completion_tokens_details") or {}
    reasoning_tokens = detail.get("reasoning_tokens")
    extra = {}
    if reasoning_tokens is not None:
        extra["thoughtsTokenCount"] = reasoning_tokens
    # 본문이 비었는데 토큰을 썼다면 그 자체가 진단이다 — 소비자가 볼 수 있게 남긴다.
    if not text and usage.get("completion_tokens"):
        extra["emptyContentWithTokens"] = True
        reasoning_text = (choices[0].get("message") or {}).get("reasoning") if choices else None
        if reasoning_text:
            extra["reasoningChars"] = len(str(reasoning_text))
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
            **extra,
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
