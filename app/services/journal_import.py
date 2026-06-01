"""Import usage JSON lines from journalctl into SQLite."""

from __future__ import annotations

import json
import re
import subprocess
import sys

from app.services.usage_store import ensure_schema, insert_record

_JSON_RE = re.compile(r"\{.*\}$")


def import_journal(since: str = "24 hours ago") -> int:
    cmd = [
        "journalctl",
        "-u",
        "osh-ai-gateway",
        "--since",
        since,
        "--no-pager",
        "-o",
        "cat",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "journalctl failed")

    ensure_schema()
    imported = 0
    for line in proc.stdout.splitlines():
        if "osh_ai_gateway.usage" not in line:
            continue
        match = _JSON_RE.search(line)
        if not match:
            continue
        try:
            record = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        if not {"ts", "backend", "service_id", "status"}.issubset(record):
            continue
        insert_record(record)
        imported += 1
    return imported


if __name__ == "__main__":
    since = sys.argv[1] if len(sys.argv) > 1 else "24 hours ago"
    count = import_journal(since)
    print(f"imported={count}")
