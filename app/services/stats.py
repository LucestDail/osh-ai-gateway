"""Dashboard aggregation for gateway traffic."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))


def _kst_now() -> datetime:
    return datetime.now(KST)


def _parse_ts(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def has_data(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS c FROM usage_log").fetchone()
    return bool(row and row["c"] > 0)


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status >= 200 AND status < 300 THEN 1 ELSE 0 END) AS ok,
            SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors,
            COALESCE(SUM(total_tokens), 0) AS tokens,
            COALESCE(AVG(elapsed_ms), 0) AS avg_ms
        FROM usage_log
        """
    ).fetchone()
    today_start = _kst_now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start.astimezone(timezone.utc).isoformat()
    today = conn.execute(
        """
        SELECT COUNT(*) AS c, COALESCE(SUM(total_tokens), 0) AS tokens
        FROM usage_log
        WHERE ts >= ?
        """,
        (today_start_utc,),
    ).fetchone()
    last24 = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM usage_log
        WHERE ts >= datetime('now', '-24 hours')
        """
    ).fetchone()
    total = int(row["total"] or 0)
    ok = int(row["ok"] or 0)
    return {
        "total_requests": total,
        "success_rate": round(ok / total * 100, 1) if total else 0.0,
        "error_count": int(row["errors"] or 0),
        "total_tokens": int(row["tokens"] or 0),
        "avg_latency_ms": round(float(row["avg_ms"] or 0), 1),
        "today_requests": int(today["c"] or 0),
        "today_tokens": int(today["tokens"] or 0),
        "last_24h_requests": int(last24["c"] or 0),
    }


def histogram_24h(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    anchor = _kst_now().replace(minute=0, second=0, microsecond=0)
    buckets = [
        {"label": (anchor - timedelta(hours=23 - i)).strftime("%H시"), "count": 0}
        for i in range(24)
    ]
    cutoff = anchor - timedelta(hours=23)
    for row in _rows(conn, "SELECT ts FROM usage_log"):
        dt = _parse_ts(row["ts"]).astimezone(KST)
        if dt < cutoff:
            continue
        idx = int((dt - cutoff).total_seconds() // 3600)
        if 0 <= idx < 24:
            buckets[idx]["count"] += 1
    return buckets


def histogram_7d(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    today = _kst_now().date()
    days = [today - timedelta(days=6 - i) for i in range(7)]
    by_day: dict[date, int] = {d: 0 for d in days}
    for row in _rows(conn, "SELECT ts FROM usage_log"):
        d = _parse_ts(row["ts"]).astimezone(KST).date()
        if d in by_day:
            by_day[d] += 1
    return [{"label": d.strftime("%m/%d"), "count": by_day[d]} for d in days]


def by_service(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """
        SELECT
            service_id,
            COUNT(*) AS requests,
            SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors,
            COALESCE(SUM(total_tokens), 0) AS tokens,
            COALESCE(AVG(elapsed_ms), 0) AS avg_ms
        FROM usage_log
        GROUP BY service_id
        ORDER BY requests DESC
        """,
    )
    return [
        {
            "service_id": r["service_id"],
            "requests": int(r["requests"]),
            "errors": int(r["errors"] or 0),
            "tokens": int(r["tokens"] or 0),
            "avg_ms": round(float(r["avg_ms"] or 0), 1),
        }
        for r in rows
    ]


def by_backend(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """
        SELECT backend, COUNT(*) AS requests, COALESCE(SUM(total_tokens), 0) AS tokens
        FROM usage_log
        GROUP BY backend
        ORDER BY requests DESC
        """,
    )
    return [
        {
            "backend": r["backend"],
            "requests": int(r["requests"]),
            "tokens": int(r["tokens"] or 0),
        }
        for r in rows
    ]


def status_breakdown(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """
        SELECT status, COUNT(*) AS c
        FROM usage_log
        GROUP BY status
        ORDER BY c DESC
        """,
    )
    return [{"status": int(r["status"]), "count": int(r["c"])} for r in rows]


def recent_requests(conn: sqlite3.Connection, limit: int = 40) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """
        SELECT ts, backend, service_id, path, status, elapsed_ms, total_tokens
        FROM usage_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = _parse_ts(r["ts"]).astimezone(KST).strftime("%m-%d %H:%M:%S")
        path = r["path"]
        if len(path) > 72:
            path = path[:69] + "..."
        out.append(
            {
                "ts": ts,
                "backend": r["backend"],
                "service_id": r["service_id"],
                "path": path,
                "status": int(r["status"]),
                "elapsed_ms": round(float(r["elapsed_ms"]), 1),
                "total_tokens": r["total_tokens"],
            }
        )
    return out


def build_dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    empty = not has_data(conn)
    if empty:
        return {"empty": True}
    return {
        "empty": False,
        "generated_at": _kst_now().strftime("%Y-%m-%d %H:%M:%S KST"),
        "summary": summary(conn),
        "h24": histogram_24h(conn),
        "h7d": histogram_7d(conn),
        "by_service": by_service(conn),
        "by_backend": by_backend(conn),
        "status_breakdown": status_breakdown(conn),
        "recent": recent_requests(conn),
    }
