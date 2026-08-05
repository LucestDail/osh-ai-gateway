"""Dashboard aggregation for gateway traffic."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config.settings import settings

KST = timezone(timedelta(hours=9))


def _usd_cost(prompt_tokens: int, output_tokens: int) -> float:
    pin = settings.gemini_price_per_m_in_usd
    pout = settings.gemini_price_per_m_out_usd
    return prompt_tokens / 1_000_000 * pin + output_tokens / 1_000_000 * pout


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
    last24_cutoff = (_kst_now() - timedelta(hours=24)).astimezone(timezone.utc).isoformat()
    last24 = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM usage_log
        WHERE ts >= ?
        """,
        (last24_cutoff,),
    ).fetchone()
    total = int(row["total"] or 0)
    ok = int(row["ok"] or 0)
    tok = conn.execute(
        """
        SELECT
            COALESCE(SUM(prompt_tokens), 0) AS pin,
            COALESCE(SUM(candidates_tokens), 0) AS pout
        FROM usage_log
        """
    ).fetchone()
    prompt_total = int(tok["pin"] or 0)
    output_total = int(tok["pout"] or 0)
    cost_usd = _usd_cost(prompt_total, output_total)
    return {
        "total_requests": total,
        "success_rate": round(ok / total * 100, 1) if total else 0.0,
        "error_count": int(row["errors"] or 0),
        "total_tokens": int(row["tokens"] or 0),
        "prompt_tokens": prompt_total,
        "output_tokens": output_total,
        "cost_usd": round(cost_usd, 6),
        "cost_krw": int(cost_usd * settings.usd_krw_rate),
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
            COALESCE(SUM(prompt_tokens), 0) AS pin,
            COALESCE(SUM(candidates_tokens), 0) AS pout,
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
            "cost_usd": round(_usd_cost(int(r["pin"] or 0), int(r["pout"] or 0)), 6),
            "cost_krw": int(_usd_cost(int(r["pin"] or 0), int(r["pout"] or 0)) * settings.usd_krw_rate),
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


def cost_breakdown(conn: sqlite3.Connection) -> dict[str, Any]:
    """Actual token usage × configured unit prices."""
    today = _kst_now().date()
    days = [today - timedelta(days=6 - i) for i in range(7)]
    by_day: dict[date, dict[str, int]] = {
        d: {"requests": 0, "pin": 0, "pout": 0} for d in days
    }
    total_pin = 0
    total_pout = 0
    for row in _rows(
        conn,
        "SELECT ts, prompt_tokens, candidates_tokens FROM usage_log",
    ):
        d = _parse_ts(row["ts"]).astimezone(KST).date()
        pin = int(row["prompt_tokens"] or 0)
        pout = int(row["candidates_tokens"] or 0)
        total_pin += pin
        total_pout += pout
        if d in by_day:
            by_day[d]["requests"] += 1
            by_day[d]["pin"] += pin
            by_day[d]["pout"] += pout

    per_day = []
    for d in days:
        b = by_day[d]
        usd = _usd_cost(b["pin"], b["pout"])
        per_day.append(
            {
                "label": d.strftime("%m/%d"),
                "requests": b["requests"],
                "prompt_tokens": b["pin"],
                "output_tokens": b["pout"],
                "usd": round(usd, 6),
                "krw": int(usd * settings.usd_krw_rate),
            }
        )

    total_usd = _usd_cost(total_pin, total_pout)
    days_with_data = sum(1 for d in days if by_day[d]["requests"] > 0)
    avg_daily_usd = total_usd / days_with_data if days_with_data else 0.0
    monthly_est_usd = avg_daily_usd * 30

    return {
        "params": {
            "price_in_per_m_usd": settings.gemini_price_per_m_in_usd,
            "price_out_per_m_usd": settings.gemini_price_per_m_out_usd,
            "usd_krw": settings.usd_krw_rate,
        },
        "total_prompt_tokens": total_pin,
        "total_output_tokens": total_pout,
        "total_usd": round(total_usd, 6),
        "total_krw": int(total_usd * settings.usd_krw_rate),
        "monthly_est_usd": round(monthly_est_usd, 4),
        "monthly_est_krw": int(monthly_est_usd * settings.usd_krw_rate),
        "per_day": per_day,
        "note": "usageMetadata 실측 토큰 × 단가 추정. 모델별 단가 차이는 미반영.",
    }


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
        "cost": cost_breakdown(conn),
    }
