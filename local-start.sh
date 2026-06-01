#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — set GEMINI_API_KEY before use."
fi

exec python -m uvicorn app.main:app --host "${SERVER_HOST:-127.0.0.1}" --port "${SERVER_PORT:-8780}" --reload
