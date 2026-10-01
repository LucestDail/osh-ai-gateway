"""Gemini `responseSchema` → OpenAI `json_schema` 번역 · 라우팅 · 폴백 — 2026-10-01

## 왜 있나

종전엔 `responseMimeType` 만 보고 `{"type":"json_object"}` 를 보냈다. 호출자가
매 호출 성실히 만들어 보내던 `responseSchema` 는 **통째로 버려지고 있었다** —
보내는 쪽은 계약이 집행되는 줄 알았다.

## 이 자가 지켜야 하는 두 축 — 둘 다 없으면 의미가 없다

  A 변환이 **맞게** 된다        (strict 제약 흡수 · null 유니온 · enum 보존)
  B 못 바꾸면 **예전 동작**으로  (None → json_object)

★ B 가 더 중요하다. 이 번역기는 **7개 서비스가 공용으로 탄다.** 변환이 틀리면
  한 서비스가 아니라 전부가 죽는다. 그래서 "바뀐다" 보다 **"못 바꿀 때 조용히
  예전 길로 간다"** 를 더 세게 본다. 같은 이유로 D 축(폴백 멱등·고정 보존)이
  이 파일에서 제일 날이 서 있어야 한다.
"""

from __future__ import annotations

import json

import pytest

from app.config.settings import settings
from app.services.gemini_compat import (
    gemini_body_to_openai,
    gemini_schema_to_json_schema,
)
from app.services.proxy import (
    _apply_provider_policy,
    _apply_structured_output_routing,
    _downgrade_to_json_object,
)


# ── 공용 헬퍼 ────────────────────────────────────────────────────────

def _body(gen_config: dict | None = None) -> dict:
    """최소한의 Gemini generateContent 요청."""
    out: dict = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    if gen_config is not None:
        out["generationConfig"] = gen_config
    return out


def _json_body(schema: dict | None = None, **extra) -> dict:
    gen: dict = {"responseMimeType": "application/json", **extra}
    if schema is not None:
        gen["responseSchema"] = schema
    return _body(gen)


def _nest(levels: int) -> dict:
    """object 를 ``levels`` 겹 더 감싼다. 가장 깊은 노드의 depth = ``levels + 1``."""
    node: dict = {"type": "OBJECT", "properties": {"leaf": {"type": "STRING"}}, "required": ["leaf"]}
    for _ in range(levels):
        node = {"type": "OBJECT", "properties": {"child": node}, "required": ["child"]}
    return node


# simpleStock 라이브가 실제로 보내는 모양 (2026-10-01 기준).
# ⚠️ 호출자는 `@google/genai` 의 `Type.*` 를 쓰므로 **대문자**로 온다.
REPORT_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING", "description": "한 문단 요약"},
        "positions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "symbol": {"type": "STRING"},
                    "stance": {"type": "STRING", "enum": ["BUY", "SELL", "HOLD"]},
                    "confidence": {"type": "NUMBER"},
                    "rationale": {"type": "STRING"},
                    "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "risk": {"type": "STRING"},
                    "entry": {"type": "NUMBER"},
                    "stop": {"type": "NUMBER"},
                    "target": {"type": "NUMBER"},
                    "scenarioUp": {"type": "STRING"},
                    "scenarioDown": {"type": "STRING"},
                },
                "required": ["symbol", "stance", "confidence", "rationale", "evidence", "risk"],
            },
        },
    },
    "required": ["summary", "positions"],
}


# ── A: 변환 성공 축 ──────────────────────────────────────────────────

def test_중첩_스키마가_소문자_타입과_strict_제약으로_바뀐다():
    """object > array > object 세 겹.

    strict 모드는 ①모든 object 에 ``additionalProperties:false``
    ②``required`` 에 **모든** 프로퍼티 — 둘 다 빠지면 업스트림이 400 을 낸다.
    """
    schema = {
        "type": "OBJECT",
        "properties": {
            "rows": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING"},
                        "count": {"type": "INTEGER"},
                        "ok": {"type": "BOOLEAN"},
                    },
                    "required": ["name", "count", "ok"],
                },
            },
        },
        "required": ["rows"],
    }
    out = gemini_schema_to_json_schema(schema)

    assert out["type"] == "object"
    assert out["additionalProperties"] is False
    assert out["required"] == ["rows"]

    rows = out["properties"]["rows"]
    assert rows["type"] == "array"

    item = rows["items"]
    assert item["type"] == "object"
    assert item["additionalProperties"] is False
    # 🔴 required 는 **모든 프로퍼티**를 담아야 한다 (원래 required 가 아니라).
    assert item["required"] == ["name", "count", "ok"]
    assert item["properties"]["name"]["type"] == "string"
    assert item["properties"]["count"]["type"] == "integer"
    assert item["properties"]["ok"]["type"] == "boolean"


def test_원래_선택이던_칸만_null_유니온이_된다():
    """★ 이 테스트가 A 축의 핵심이다.

    strict 가 전부 required 를 요구한다고 **원래 선택이던 칸까지 강제하면
    모델이 없는 값을 지어낸다.** null 을 허용해서 "모른다" 를 말할 수 있게
    해 둔 것이 그 대책이고, 그게 실제로 걸리는지 본다.
    """
    schema = {
        "type": "OBJECT",
        "properties": {
            "must": {"type": "STRING"},
            "maybe": {"type": "NUMBER"},
        },
        "required": ["must"],
    }
    out = gemini_schema_to_json_schema(schema)

    # 원래 required → 유니온이 **아니다** (문자열 그대로)
    assert out["properties"]["must"]["type"] == "string"
    # 원래 선택 → null 유니온
    assert out["properties"]["maybe"]["type"] == ["number", "null"]
    # 그래도 둘 다 required 에는 들어간다 (strict 제약)
    assert out["required"] == ["must", "maybe"]


def test_required_키가_아예_없으면_전부_null_유니온이다():
    """``required`` 자체가 없는 스키마 = 전부 선택. 전부 지어내기를 막아야 한다."""
    schema = {"type": "OBJECT", "properties": {"a": {"type": "STRING"}, "b": {"type": "NUMBER"}}}
    out = gemini_schema_to_json_schema(schema)
    assert out["properties"]["a"]["type"] == ["string", "null"]
    assert out["properties"]["b"]["type"] == ["number", "null"]
    assert out["required"] == ["a", "b"]


def test_enum_이_보존되고_선택_필드에만_None_이_더해진다():
    """enum 을 null 유니온으로 만들면서 **목록에 None 을 안 넣으면 모순**이 된다
    (타입은 null 을 허용하는데 값 목록이 금지한다) — 모델이 그 칸을 못 채운다.
    """
    schema = {
        "type": "OBJECT",
        "properties": {
            "req_enum": {"type": "STRING", "enum": ["A", "B"]},
            "opt_enum": {"type": "STRING", "enum": ["A", "B"]},
        },
        "required": ["req_enum"],
    }
    out = gemini_schema_to_json_schema(schema)

    assert out["properties"]["req_enum"] == {"type": "string", "enum": ["A", "B"]}
    assert out["properties"]["opt_enum"]["type"] == ["string", "null"]
    assert out["properties"]["opt_enum"]["enum"] == ["A", "B", None]


def test_description_이_보존된다():
    """description 은 모델에게 주는 지시다 — 번역에서 떨어뜨리면 품질이 조용히 내려간다."""
    schema = {
        "type": "OBJECT",
        "properties": {
            "note": {"type": "STRING", "description": "한 줄 메모"},
            "rows": {
                "type": "ARRAY",
                "description": "행 목록",
                "items": {
                    "type": "OBJECT",
                    "description": "한 행",
                    "properties": {"x": {"type": "STRING"}},
                    "required": ["x"],
                },
            },
        },
        "required": ["note", "rows"],
        "description": "최상위",
    }
    out = gemini_schema_to_json_schema(schema)

    assert out["description"] == "최상위"
    assert out["properties"]["note"]["description"] == "한 줄 메모"
    assert out["properties"]["rows"]["description"] == "행 목록"
    assert out["properties"]["rows"]["items"]["description"] == "한 행"


def test_responseSchema_가_있으면_json_schema_로_나간다():
    """end-to-end: 번역기 출력의 `response_format` 모양."""
    out = gemini_body_to_openai(_json_body(REPORT_SCHEMA), "m")
    rf = out["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "response"
    assert rf["json_schema"]["schema"]["type"] == "object"


# ── B: 폴백 축 — "못 바꾸면 예전 동작" ───────────────────────────────

def test_responseSchema_가_없으면_json_object_그대로():
    """스키마를 안 보내는 기존 호출자(대다수)의 동작이 **한 글자도 안 바뀐다**."""
    out = gemini_body_to_openai(_json_body(), "m")
    assert out["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "schema, 사유",
    [
        ({"type": "ARRAY", "items": {"type": "STRING"}}, "최상위가 object 가 아니다"),
        ({"type": "STRING"}, "최상위가 스칼라다"),
        ({"type": "OBJECT"}, "프로퍼티가 없다"),
        ({"type": "OBJECT", "properties": {}}, "프로퍼티가 비었다"),
        ({"type": "WEIRD", "properties": {"a": {"type": "STRING"}}}, "알 수 없는 타입"),
        (None, "스키마가 없다"),
        ("문자열", "스키마가 dict 가 아니다"),
    ],
)
def test_못_바꾸는_스키마는_None_을_낸다(schema, 사유):
    """★ ``None`` 은 실패가 아니라 **"예전 동작으로 가라"** 는 신호다."""
    assert gemini_schema_to_json_schema(schema) is None, 사유


def test_최상위가_배열이면_json_object_로_떨어진다():
    out = gemini_body_to_openai(_json_body({"type": "ARRAY", "items": {"type": "STRING"}}), "m")
    assert out["response_format"] == {"type": "json_object"}


def test_프로퍼티_없는_object_는_json_object_로_떨어진다():
    out = gemini_body_to_openai(_json_body({"type": "OBJECT", "properties": {}}), "m")
    assert out["response_format"] == {"type": "json_object"}


def test_깊은_곳의_알_수_없는_타입_하나가_스키마_전체를_포기시킨다():
    """🔴 **반쪽 스키마를 보내지 않는다.**

    못 읽은 칸만 빼고 strict 로 보내면 업스트림이 그 칸을 `additionalProperties:false`
    위반으로 **거부**한다 — 멀쩡한 필드까지 잃는 조용한 손실이 된다.
    """
    schema = {
        "type": "OBJECT",
        "properties": {
            "ok": {"type": "STRING"},
            "rows": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "fine": {"type": "STRING"},
                        "broken": {"type": "TIMESTAMP"},   # Gemini 에 없는 타입
                    },
                    "required": ["fine", "broken"],
                },
            },
        },
        "required": ["ok", "rows"],
    }
    assert gemini_schema_to_json_schema(schema) is None

    out = gemini_body_to_openai(_json_body(schema), "m")
    assert out["response_format"] == {"type": "json_object"}


def test_깊이_한도를_넘으면_포기한다():
    """경계 양쪽을 **둘 다** 본다 — 한쪽만 보면 가드가 항상 켜져 있어도 통과한다."""
    assert gemini_schema_to_json_schema(_nest(4)) is not None, "한도 안인데 포기했다"
    assert gemini_schema_to_json_schema(_nest(5)) is None, "한도를 넘었는데 통과했다"


def test_프로퍼티_수_한도를_넘으면_포기한다():
    def _flat(n: int) -> dict:
        props = {f"p{i}": {"type": "STRING"} for i in range(n)}
        return {"type": "OBJECT", "properties": props, "required": list(props)}

    assert gemini_schema_to_json_schema(_flat(100)) is not None, "한도 안인데 포기했다"
    assert gemini_schema_to_json_schema(_flat(101)) is None, "한도를 넘었는데 통과했다"


def test_한도를_넘어도_json_object_로_살아서_나간다():
    """포기가 **요청을 깨뜨리지 않는다** — 그래야 집행을 켤 수 있다."""
    out = gemini_body_to_openai(_json_body(_nest(5)), "m")
    assert out["response_format"] == {"type": "json_object"}


# ── C: 스트리밍 축 ───────────────────────────────────────────────────

def test_스트리밍에는_스키마를_안_건다():
    """🔴 SSE 는 **되돌릴 수 없다.**

    비스트리밍 경로에는 4xx 1회 폴백 재시도가 있지만 스트리밍에는 없다
    (이미 내려가기 시작한 뒤다). 되돌릴 수 없는 자리에 새 실패 모드를 만들지 않는다.
    """
    out = gemini_body_to_openai(_json_body(REPORT_SCHEMA), "m", stream=True)
    assert out["response_format"] == {"type": "json_object"}
    assert out["stream"] is True

    # 같은 입력을 비스트리밍으로 보내면 스키마가 걸린다 — 가드가 **스트리밍 때문**임을 못박는다.
    assert gemini_body_to_openai(_json_body(REPORT_SCHEMA), "m", stream=False)[
        "response_format"
    ]["type"] == "json_schema"


# ── D: 라우팅 · 폴백 축 (proxy.py) ──────────────────────────────────

def _pin_simplestock(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openrouter_provider_pins", "simpleStock:open-inference", raising=False)
    monkeypatch.setattr(settings, "openrouter_provider_deny_training", True, raising=False)


def test_json_schema_일_때만_require_parameters_를_붙인다():
    """json_object 요청에 `provider` 를 만들면 **지정 안 한 서비스의 라우팅을
    말없이 좁힌다** — 공용 게이트웨이에서 가용성을 떨어뜨리는 쪽이라 더 위험하다.
    """
    schema_body = {"model": "m", "messages": [], "response_format": {"type": "json_schema", "json_schema": {}}}
    _apply_structured_output_routing(schema_body)
    assert schema_body["provider"] == {"require_parameters": True}

    for rf in ({"type": "json_object"}, None, "json_schema", {"type": "text"}):
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        if rf is not None:
            body["response_format"] = rf
        before = json.dumps(body, sort_keys=True)
        _apply_structured_output_routing(body)
        assert json.dumps(body, sort_keys=True) == before, f"{rf} 요청이 바뀌었다"
        assert "provider" not in body


def test_사업자_고정이_있으면_덧붙이기만_한다(monkeypatch):
    """🔴 고정은 **자산 원문을 어디로 보낼지**의 결정이다. 여기서 한 글자라도
    바뀌면 그 결정이 조용히 뒤집힌다.
    """
    _pin_simplestock(monkeypatch)
    body = {"model": "m", "messages": [], "response_format": {"type": "json_schema", "json_schema": {}}}
    _apply_provider_policy(body, "simpleStock")
    고정_그대로 = dict(body["provider"])

    _apply_structured_output_routing(body)

    assert body["provider"]["order"] == 고정_그대로["order"]
    assert body["provider"]["allow_fallbacks"] == 고정_그대로["allow_fallbacks"]
    assert body["provider"]["data_collection"] == 고정_그대로["data_collection"]
    assert body["provider"]["require_parameters"] is True
    # 더해진 것이 **그 키 하나뿐**인지 거꾸로 센다.
    assert set(body["provider"]) - set(고정_그대로) == {"require_parameters"}


def test_폴백은_response_format_과_require_parameters_만_되돌린다(monkeypatch):
    """고정이 **없던** 요청에서는 `provider` 키가 아예 사라져야 한다 —
    우리가 만든 키를 남겨 두면 폴백한 뒤에도 라우팅이 좁은 채로 남는다.
    """
    body = {"model": "m", "messages": [], "response_format": {"type": "json_schema", "json_schema": {}}}
    _apply_structured_output_routing(body)
    assert _downgrade_to_json_object(body) is True
    assert body["response_format"] == {"type": "json_object"}
    assert "provider" not in body

    # 고정이 **있던** 요청에서는 고정이 남는다.
    _pin_simplestock(monkeypatch)
    pinned = {"model": "m", "messages": [], "response_format": {"type": "json_schema", "json_schema": {}}}
    _apply_provider_policy(pinned, "simpleStock")
    _apply_structured_output_routing(pinned)
    assert _downgrade_to_json_object(pinned) is True
    assert pinned["response_format"] == {"type": "json_object"}
    assert pinned["provider"]["order"] == ["open-inference"]
    assert pinned["provider"]["allow_fallbacks"] is False
    assert "require_parameters" not in pinned["provider"]


def test_이미_json_object_면_폴백이_아무것도_안_바꾼다():
    """★ 멱등. 재시도 분기가 `_downgrade_to_json_object(...)` 의 반환값으로
    "재시도할지" 를 정하므로, 여기서 True 를 내면 **스키마와 무관한 4xx(401·429)
    에도 호출이 두 배**가 된다.
    """
    for body in (
        {"model": "m", "messages": [], "response_format": {"type": "json_object"}},
        {"model": "m", "messages": []},
        {"model": "m", "messages": [], "provider": {"order": ["x"], "allow_fallbacks": False}},
    ):
        before = json.dumps(body, sort_keys=True)
        assert _downgrade_to_json_object(body) is False
        assert json.dumps(body, sort_keys=True) == before


# ── E: 회귀 축 — 기존 동작 불변 ─────────────────────────────────────

@pytest.mark.parametrize(
    "thinking, 기대",
    [
        ({"thinkingBudget": 0}, {"enabled": False}),
        ({"thinkingLevel": "none"}, {"enabled": False}),
        ({"thinkingLevel": "OFF"}, {"enabled": False}),
        ({"thinkingBudget": 512}, {"max_tokens": 512}),
        ({"thinkingLevel": "low"}, {"effort": "low"}),
        ({"thinkingLevel": "High"}, {"effort": "high"}),
    ],
)
def test_thinking_번역이_그대로다(thinking, 기대):
    """🔴 이걸 깨뜨리면 생각이 출력 예산을 다 먹어 **본문이 0토큰**이 된다
    (2026-09-23 simpleStock 브리핑 이틀 장애의 그 자리).
    """
    out = gemini_body_to_openai(_body({"thinkingConfig": thinking}), "m")
    assert out["reasoning"] == 기대


def test_thinkingConfig_를_안_보내면_reasoning_키가_없다():
    assert "reasoning" not in gemini_body_to_openai(_body({"temperature": 0.2}), "m")


def test_responseMimeType_이_없으면_response_format_키가_아예_없다():
    """스키마를 안 쓰는 호출(대다수)에 키가 생기면 그것만으로 업스트림 동작이 달라진다."""
    out = gemini_body_to_openai(_body({"temperature": 0.3, "maxOutputTokens": 100}), "m")
    assert "response_format" not in out
    # 곁가지로 기존 번역이 멀쩡한지도 함께 본다.
    assert out["temperature"] == 0.3
    assert out["max_tokens"] == 100
    assert out["messages"] == [{"role": "user", "content": "hi"}]


def test_responseSchema_만_있고_mimeType_이_없으면_무시한다():
    """스키마는 `responseMimeType=application/json` 일 때만 의미가 있다."""
    out = gemini_body_to_openai(_body({"responseSchema": REPORT_SCHEMA}), "m")
    assert "response_format" not in out


# ── F: 실제 스키마 축 ────────────────────────────────────────────────

def test_simpleStock_REPORT_SCHEMA_가_온전히_살아남는다():
    """★ 합성 스키마가 아니라 **라이브가 실제로 보내는 모양**으로 잰다.

    `stance` 가 null 유니온이 되면 모델이 종목 판단에 `null` 을 넣을 수 있게 되고,
    그건 9/28 에 봤던 `positions 0` 과 **구분이 안 되는 증상**으로 나타난다.
    """
    out = gemini_schema_to_json_schema(REPORT_SCHEMA)
    assert out is not None

    assert out["additionalProperties"] is False
    assert out["required"] == ["summary", "positions"]
    assert out["properties"]["summary"] == {"type": "string", "description": "한 문단 요약"}

    item = out["properties"]["positions"]["items"]
    assert item["additionalProperties"] is False
    # strict: 11개 전부 required
    assert item["required"] == [
        "symbol", "stance", "confidence", "rationale", "evidence", "risk",
        "entry", "stop", "target", "scenarioUp", "scenarioDown",
    ]

    # 🔴 원래 required 인 `stance` 는 null 유니온이 **아니어야** 한다.
    assert item["properties"]["stance"] == {"type": "string", "enum": ["BUY", "SELL", "HOLD"]}
    assert item["properties"]["symbol"] == {"type": "string"}
    assert item["properties"]["confidence"] == {"type": "number"}
    assert item["properties"]["evidence"] == {"type": "array", "items": {"type": "string"}}

    # 원래 선택이던 수치 칸은 "모른다" 를 말할 수 있어야 한다.
    for key in ("entry", "stop", "target"):
        assert item["properties"][key]["type"] == ["number", "null"], key
    for key in ("scenarioUp", "scenarioDown"):
        assert item["properties"][key]["type"] == ["string", "null"], key


def test_REPORT_SCHEMA_가_end_to_end_로_json_schema_가_된다():
    out = gemini_body_to_openai(_json_body(REPORT_SCHEMA), "m")
    schema = out["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["positions"]["items"]["properties"]["stance"]["enum"] == [
        "BUY", "SELL", "HOLD",
    ]
    # 번역 결과가 JSON 직렬화 가능해야 업스트림으로 나간다 (None 이 섞여도 괜찮은지 포함).
    json.dumps(out, ensure_ascii=False)


# ── G. nullable 의 **주체** · array 예외 ──────────────────────────────
#
# 아래 셋은 **첫 구현이 틀렸던 자리**다(검증자가 두 방향을 다 실측해 잡았다).
# 처음엔 `nullable` 을 부모 object 에서 읽어서, 자식에 단 것은 무시되고
# 부모에 달면 자식 전부가 null 이 되는 **양방향 오류**였다. 고친 뒤 못박는다.

def test_자식에_달린_nullable_은_required_여도_null_을_허용한다():
    """`nullable` 은 **그 칸 자신**의 선언이다 — required 여부와 별개로 존중한다."""
    out = gemini_schema_to_json_schema({
        "type": "OBJECT",
        "properties": {"maybe": {"type": "STRING", "nullable": True}},
        "required": ["maybe"],
    })
    assert out["properties"]["maybe"]["type"] == ["string", "null"]


def test_부모의_nullable_이_자식들로_새지_않는다():
    """부모 object 에 달린 `nullable` 이 자식 전부를 null 로 만들면 안 된다."""
    out = gemini_schema_to_json_schema({
        "type": "OBJECT",
        "nullable": True,
        "properties": {
            "a": {"type": "STRING"},
            "b": {"type": "NUMBER"},
        },
        "required": ["a", "b"],
    })
    assert out["properties"]["a"]["type"] == "string"
    assert out["properties"]["b"]["type"] == "number"


def test_선택인_배열은_null_유니온이_되지_않는다():
    """"없음" 은 빈 배열로 말할 수 있다 — null 이 필요 없고, 제공자 지원도 갈린다.

    ⚠️ 그래도 strict 때문에 `required` 에는 **들어간다**(모양은 강제, 값은 빈 배열 허용).
    """
    out = gemini_schema_to_json_schema({
        "type": "OBJECT",
        "properties": {
            "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "note": {"type": "STRING"},
        },
        "required": [],
    })
    assert out["properties"]["tags"]["type"] == "array"      # 유니온 아님
    assert out["properties"]["note"]["type"] == ["string", "null"]  # 대조군
    assert out["required"] == ["tags", "note"]


def test_선택인_object_는_null_유니온을_유지한다():
    """object 는 "없음" 을 표현할 방법이 null 뿐이다 — array 와 갈리는 지점."""
    out = gemini_schema_to_json_schema({
        "type": "OBJECT",
        "properties": {
            "inner": {"type": "OBJECT", "properties": {"x": {"type": "STRING"}}, "required": ["x"]},
        },
        "required": [],
    })
    assert out["properties"]["inner"]["type"] == ["object", "null"]


def test_REPORT_SCHEMA_의_선택_배열은_유니온이_아니다():
    """실제 스키마에서 확인 — `evidence` 는 required 라 애초에 유니온이 아니고,
    최상위 `dataGaps`·`positions`·`proposals` 도 required 라 그대로다.
    선택이 됐더라도 배열이면 유니온이 아니어야 한다는 것을 한 번 더 못박는다."""
    out = gemini_schema_to_json_schema({
        "type": "OBJECT",
        "properties": {
            "dataGaps": {"type": "ARRAY", "items": {"type": "STRING"}},
            "marketView": {"type": "STRING"},
        },
        "required": ["marketView"],       # dataGaps 를 일부러 선택으로
    })
    assert out["properties"]["dataGaps"]["type"] == "array"
    assert out["properties"]["marketView"]["type"] == "string"
