"""Stats aggregation tests."""

import sqlite3
from datetime import datetime, timezone

from app.services import stats as stats_mod
from app.services.usage_store import ensure_schema


def _seed(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO usage_log (
            ts, backend, service_id, method, path, status, elapsed_ms,
            prompt_tokens, candidates_tokens, total_tokens
        ) VALUES (?, 'gemini', 'aim-monitor', 'POST', '/gemini/v1beta/models/x:generateContent', 200, 900.0, 100, 20, 120)
        """,
        (now,),
    )
    conn.commit()


def test_build_dashboard(tmp_path):
    db = tmp_path / "usage.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    _seed(conn)
    data = stats_mod.build_dashboard(conn)
    conn.close()
    assert data["empty"] is False
    assert data["summary"]["total_requests"] == 1
    assert data["by_service"][0]["service_id"] == "aim-monitor"
