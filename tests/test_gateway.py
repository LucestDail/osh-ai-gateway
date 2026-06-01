"""Basic gateway tests (no upstream calls)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_status():
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "gemini_configured" in body
    assert "vertex_enabled" in body


def test_gemini_without_key_returns_503():
    resp = client.post(
        "/gemini/v1beta/models/gemini-2.5-flash-lite:generateContent",
        json={"contents": [{"parts": [{"text": "hi"}]}]},
    )
    assert resp.status_code == 503
