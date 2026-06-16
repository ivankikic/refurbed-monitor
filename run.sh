#!/usr/bin/env bash
# Cron-friendly wrapper: load .env, use the venv if present, run the monitor.
# Usage:  ./run.sh                 (normal run)
#         ./run.sh --dry-run       (any monitor.py flags are passed through)
set -euo pipefail
cd "$(dirname "$0")"

# Load secrets if present (SMTP_USER/PASS/ALERT_TO, TELEGRAM_*)
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

# Prefer a local virtualenv if it exists
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3)"
fi

exec "$PY" monitor.py "$@" >> monitor.log 2>&1
