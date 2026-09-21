"""사업자(provider) 고정 · 실제 사업자 관측 — 2026-09-21

## 왜 있나

모델은 고정돼 있어도 OpenRouter 는 **매 호출 임의 사업자**로 라우팅한다
(실측: 이 모델을 서비스하는 사업자 15곳). 자산 원문을 보내기로 한 서비스는
"어디로 갔는지 사후에 알 수 없다" 가 된다.

두 가지를 함께 넣었고, **둘 다 있어야 의미가 있다**:
  C 고정 — 지정한 서비스만 사업자를 한 곳으로 묶는다
  D 관측 — 응답이 말해 주는 **실제** 사업자를 기록한다

★ D 가 없으면 C 를 걸고도 **걸렸는지 확인할 수단이 없다**
  (= "가드를 만들었다고 효과를 주장하지 않는다").

## 이 자가 지켜야 하는 것 — 오탐이 더 위험하다

게이트웨이는 **7개 서비스 공용**이다. 고정이 엉뚱한 서비스에 걸리면
그 서비스의 라우팅을 말없이 좁혀 **가용성을 떨어뜨린다**. 그래서
"지정한 곳에 걸린다" 보다 **"지정 안 한 곳은 한 글자도 안 바뀐다"** 를 더 세게 본다.
"""

from __future__ import annotations

import json

from app.config.settings import settings
from app.services.proxy import _apply_provider_policy, _provider_pins
from app.services.usage_logger import _extract_provider


def _pins(monkeypatch, value: str, deny: bool = True) -> None:
    monkeypatch.setattr(settings, "openrouter_provider_pins", value, raising=False)
    monkeypatch.setattr(settings, "openrouter_provider_deny_training", deny, raising=False)


# ── C: 고정 ──────────────────────────────────────────────────────────

def test_지정한_서비스에만_붙는다(monkeypatch):
    _pins(monkeypatch, "simpleStock:deepinfra")
    body = {"model": "m", "messages": []}
    _apply_provider_policy(body, "simpleStock")
    assert body["provider"] == {
        "order": ["deepinfra"],
        "allow_fallbacks": False,
        "data_collection": "deny",
    }


def test_지정_안_한_서비스는_한_글자도_안_바뀐다(monkeypatch):
    """🔴 이 테스트가 이 파일에서 제일 중요하다.

    공용 게이트웨이라, 다른 6개 서비스가 이 분기에 **들어가지 않는다**는 것이
    "다른 서비스 회귀를 따로 재지 않아도 된다" 의 근거다.
    """
    _pins(monkeypatch, "simpleStock:deepinfra")
    for svc in ("haru", "my-computer", "osh", "myapi", "waynai", "aim-monitor", "", "unknown"):
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        before = json.dumps(body, sort_keys=True)
        _apply_provider_policy(body, svc)
        assert json.dumps(body, sort_keys=True) == before, f"{svc} 의 요청이 바뀌었다"
        assert "provider" not in body


def test_설정이_비면_아무_데도_안_걸린다(monkeypatch):
    _pins(monkeypatch, "")
    body = {"model": "m", "messages": []}
    _apply_provider_policy(body, "simpleStock")
    assert "provider" not in body


def test_deny_training_을_끄면_그_키만_빠진다(monkeypatch):
    _pins(monkeypatch, "simpleStock:deepinfra", deny=False)
    body = {"model": "m", "messages": []}
    _apply_provider_policy(body, "simpleStock")
    assert body["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}


def test_여러_서비스와_여러_사업자를_파싱한다(monkeypatch):
    _pins(monkeypatch, "simpleStock:deepinfra,alibaba ; other:venice")
    pins = _provider_pins()
    assert pins == {"simpleStock": ["deepinfra", "alibaba"], "other": ["venice"]}


def test_형식이_깨진_항목은_버리고_나머지는_산다(monkeypatch, caplog):
    """오타 하나로 **전부** 안 걸리면 안 되고, 조용히 버려도 안 된다."""
    _pins(monkeypatch, "simpleStock:deepinfra;깨진항목;:비었음;other:")
    with caplog.at_level("WARNING"):
        pins = _provider_pins()
    assert pins == {"simpleStock": ["deepinfra"]}
    assert any("provider-pin" in r.message or "provider-pin" in r.getMessage()
               for r in caplog.records), "버린 항목을 warn 으로 남기지 않았다"


# ── D: 관측 ──────────────────────────────────────────────────────────

def test_응답에서_실제_사업자를_뽑는다():
    body = json.dumps({"provider": "DeepInfra", "choices": []}).encode()
    assert _extract_provider(body) == "DeepInfra"


def test_사업자_정보가_없으면_None():
    """⚠️ 기록에 provider 가 없으면 **'그 회차는 모른다'** 로 읽어야지 통과가 아니다."""
    assert _extract_provider(json.dumps({"choices": []}).encode()) is None
    assert _extract_provider(b"not json") is None
    assert _extract_provider(json.dumps([1, 2]).encode()) is None
    assert _extract_provider(json.dumps({"provider": ""}).encode()) is None
    assert _extract_provider(json.dumps({"provider": 7}).encode()) is None


# ── 자의 판별력 ───────────────────────────────────────────────────────

def test_자가_진짜_보는가_고정을_지우면_깨진다(monkeypatch):
    """탐지기 생존 확인 — 정책을 안 붙이는 구현을 먹여 본다."""
    _pins(monkeypatch, "simpleStock:deepinfra")
    body = {"model": "m", "messages": []}
    # (고의로 _apply_provider_policy 를 부르지 않는다 = 배선이 빠진 상태)
    assert "provider" not in body, "먹인 상태가 이미 틀렸다 — 이 테스트가 무의미하다"


# ── 배선 ─────────────────────────────────────────────────────────────

def test_실제_요청에_provider_가_실려_나간다(monkeypatch):
    """🔴 배선 테스트 — 순수 함수 테스트로는 **원리상** 못 보는 것을 본다.

    위의 `_apply_provider_policy` 단위 테스트들은 **호출부를 지워도 전부 통과한다**
    (실제로 변이로 확인했다: 배선 제거 → 13 passed). 로직이 맞아도 **안 불리면
    기능이 없는 것**이라, 요청을 실제로 태워 **업스트림으로 나가는 본문**을 본다.
    """
    import httpx
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.proxy import proxy_service

    _pins(monkeypatch, "simpleStock:deepinfra")
    monkeypatch.setattr(settings, "openrouter_translate_gemini", True, raising=False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key", raising=False)

    sent: dict[str, object] = {}

    async def fake_request(method, url, **kw):
        sent["body"] = json.loads(kw.get("content") or b"{}")
        return httpx.Response(
            200,
            json={
                "provider": "DeepInfra",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(proxy_service._client, "request", fake_request)

    # ⚠️ 실제 log_request 는 전역 usage store(sqlite)를 건드려 **다른 테스트를 오염**시킨다
    #    (실측: 이 파일을 먼저 돌리면 test_stats 가 "no such table: usage_log" 로 깨졌다).
    #    가로채면 오염이 없어지고, 덤으로 **D(사업자 기록) 배선까지** 여기서 검증된다.
    logged: dict[str, object] = {}
    import app.services.proxy as proxy_mod
    from app.services.usage_logger import _extract_provider as _ep

    def fake_log(**kw):
        logged.update(kw)
        rb = kw.get("response_body")
        logged["provider_recorded"] = _ep(rb) if rb else None

    monkeypatch.setattr(proxy_mod, "log_request", fake_log)

    with TestClient(app) as client:
        resp = client.post(
            "/gemini/v1beta/models/gemini-2.5-flash-lite:generateContent",
            json={"contents": [{"parts": [{"text": "hi"}]}]},
            headers={"x-service-id": "simpleStock"},
        )

    # 못 태웠으면 통과가 아니라 실패다 — "검사 못 함" 을 초록불로 만들지 않는다.
    assert resp.status_code == 200, f"요청이 업스트림까지 안 갔다: {resp.status_code} {resp.text[:200]}"
    assert sent.get("body"), "업스트림 본문을 못 잡았다 — 이 테스트는 아무것도 검사하지 못했다"
    assert sent["body"].get("provider") == {
        "order": ["deepinfra"],
        "allow_fallbacks": False,
        "data_collection": "deny",
    }, f"provider 가 안 실려 나갔다 — 배선이 빠졌는가? 실제 본문 키: {list(sent['body'])}"

    # D 배선 — 응답이 말해 준 **실제** 사업자가 기록 경로까지 닿는가
    assert logged.get("service_id") == "simpleStock", "기록에 service_id 가 안 실렸다"
    assert logged.get("provider_recorded") == "DeepInfra", (
        "실제 사업자가 기록되지 않았다 — D 가 없으면 C 가 걸렸는지 확인할 방법이 없다"
    )


def test_다른_서비스의_실제_요청은_안_바뀐다(monkeypatch):
    """오탐 방향 — 공용 게이트웨이라 이쪽이 더 위험하다."""
    import httpx
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.proxy import proxy_service

    _pins(monkeypatch, "simpleStock:deepinfra")
    monkeypatch.setattr(settings, "openrouter_translate_gemini", True, raising=False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key", raising=False)

    sent: dict[str, object] = {}

    async def fake_request(method, url, **kw):
        sent["body"] = json.loads(kw.get("content") or b"{}")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(proxy_service._client, "request", fake_request)
    import app.services.proxy as proxy_mod
    monkeypatch.setattr(proxy_mod, "log_request", lambda **kw: None)

    with TestClient(app) as client:
        resp = client.post(
            "/gemini/v1beta/models/gemini-2.5-flash-lite:generateContent",
            json={"contents": [{"parts": [{"text": "hi"}]}]},
            headers={"x-service-id": "haru"},
        )

    assert resp.status_code == 200
    assert sent.get("body"), "업스트림 본문을 못 잡았다 — 검사 못 함"
    assert "provider" not in sent["body"], "지정 안 한 서비스의 요청이 바뀌었다"


# ── 저장 (기록이 실제로 남는가) ────────────────────────────────────────

def test_provider_가_DB_에_실제로_남는다(monkeypatch, tmp_path):
    """🔴 이게 없어서 D 가 반쪽이었다 (2026-09-21 실측).

    `log_request` 는 record 에 `provider` 를 넣었는데, `insert_record` 는
    **고정 컬럼 목록**으로 INSERT 하고 표에는 그 컬럼이 아예 없었다.
    ⇒ 값이 **조용히 버려지고** journald 로그 줄에만 남아 회전하면 사라진다.
    "기록한다" 를 코드로 확인하지 않으면 **기록됐다고 믿게 된다.**
    """
    import sqlite3

    from app.services import usage_store

    db = tmp_path / "usage.db"
    monkeypatch.setattr(usage_store.settings, "usage_db_path", str(db), raising=False)
    monkeypatch.setattr(usage_store, "_schema_ready", False, raising=False)

    usage_store.insert_record({
        "ts": "2026-09-21T00:00:00+00:00", "backend": "openrouter",
        "service_id": "simpleStock", "method": "POST", "path": "/x",
        "status": 200, "elapsed_ms": 1.0, "provider": "DeepInfra",
    })

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_log)")}
    assert "provider" in cols, "usage_log 에 provider 컬럼이 없다 — 값이 버려진다"
    row = conn.execute("SELECT service_id, provider FROM usage_log").fetchone()
    assert row == ("simpleStock", "DeepInfra"), f"기록이 안 남았다: {row}"


def test_기존_DB_에도_컬럼을_붙인다(monkeypatch, tmp_path):
    """옛 스키마로 만들어진 DB — `CREATE TABLE IF NOT EXISTS` 는 안 고쳐 준다."""
    import sqlite3

    from app.services import usage_store

    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute("""CREATE TABLE usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, backend TEXT NOT NULL,
        service_id TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
        status INTEGER NOT NULL, elapsed_ms REAL NOT NULL,
        prompt_tokens INTEGER, candidates_tokens INTEGER, total_tokens INTEGER)""")
    old.commit(); old.close()

    monkeypatch.setattr(usage_store.settings, "usage_db_path", str(db), raising=False)
    monkeypatch.setattr(usage_store, "_schema_ready", False, raising=False)
    usage_store.insert_record({
        "ts": "t", "backend": "openrouter", "service_id": "simpleStock",
        "method": "POST", "path": "/x", "status": 200, "elapsed_ms": 1.0,
        "provider": "DeepInfra",
    })
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT provider FROM usage_log").fetchone() == ("DeepInfra",)
