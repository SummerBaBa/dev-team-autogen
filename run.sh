#!/bin/bash
# ── Load environment from .env (if present) ──────────────────
# .env is gitignored — place secrets (LangSmith API key) here.
_ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env"
if [ -f "$_ENV_FILE" ]; then
    set -a
    source "$_ENV_FILE"
    set +a
fi
# ─────────────────────────────────────────────────────────────

PYTHON=/opt/homebrew/Caskroom/miniconda/base/bin/python3.13
if [ ! -x "$PYTHON" ]; then
    echo "未找到 conda Python，尝试使用 python3"
    PYTHON=python3
fi
exec "$PYTHON" main.py "$@"
