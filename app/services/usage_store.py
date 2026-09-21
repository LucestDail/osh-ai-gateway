"""SQLite persistence for gateway usage records."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.config.settings import settings

_lock = threading.Lock()
_schema_ready = False


def _db_path() -> Path | None:
    raw = settings.usage_db_path.strip()
    if not raw:
        return None
    return Path(raw)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    if path is None:
        raise RuntimeError("USAGE_DB_PATH is not configured")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _lock:
        if _schema_ready:
            return
        own = conn is None
        db = conn or _connect()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                backend TEXT NOT NULL,
                service_id TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER NOT NULL,
                elapsed_ms REAL NOT NULL,
                prompt_tokens INTEGER,
                candidates_tokens INTEGER,
                total_tokens INTEGER
            )
            """
        )
        # 🔎 provider — 실제로 서빙한 사업자 (2026-09-21 추가)
        #
        # 기존 DB 에는 이 컬럼이 없다. `CREATE TABLE IF NOT EXISTS` 는 **이미 있는 표를
        # 고치지 않으므로** 여기서 따로 붙인다. 안 붙이면 `insert_record` 가 이 값을
        # **조용히 버리고**, 기록이 journald 로그 줄에만 남아 회전하면 사라진다
        # (실측 2026-09-21: 정확히 그 상태였다 — "기록한다" 고 믿었는데 안 남았다).
        cols = {r[1] for r in db.execute("PRAGMA table_info(usage_log)")}
        if "provider" not in cols:
            db.execute("ALTER TABLE usage_log ADD COLUMN provider TEXT")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_service ON usage_log(service_id)"
        )
        db.commit()
        if own:
            db.close()
        _schema_ready = True


def insert_record(record: dict[str, Any]) -> None:
    path = _db_path()
    if path is None:
        return
    usage = record.get("usage") or {}
    conn = _connect()
    try:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO usage_log (
                ts, backend, service_id, method, path, status, elapsed_ms,
                prompt_tokens, candidates_tokens, total_tokens, provider
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["ts"],
                record["backend"],
                record["service_id"],
                record["method"],
                record["path"],
                int(record["status"]),
                float(record["elapsed_ms"]),
                usage.get("promptTokenCount") or usage.get("prompt_tokens"),
                usage.get("candidatesTokenCount") or usage.get("completion_tokens"),
                usage.get("totalTokenCount") or usage.get("total_tokens"),
                # 없으면 NULL — ⚠️ "그 회차는 사업자를 모른다" 는 뜻이지 통과가 아니다.
                record.get("provider"),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        # 외부 잠금·경합 시 요청 처리는 이미 끝난 상태 — 로깅만 건너뜀
        conn.rollback()
        raise
    finally:
        conn.close()


def open_readonly() -> sqlite3.Connection | None:
    path = _db_path()
    if path is None or not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
