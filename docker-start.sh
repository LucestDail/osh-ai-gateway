#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker build -t osh-ai-gateway:local .
docker run --rm -p 8780:8780 --env-file .env --name osh-ai-gateway osh-ai-gateway:local
