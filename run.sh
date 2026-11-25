#!/usr/bin/env bash
# Запуск ViralCut: ./run.sh  (опционально --port 8765)
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv312/bin/python" ]; then
  PY=".venv312/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  echo "Первый запуск: создаю окружение и ставлю зависимости…"
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
  PY=".venv/bin/python"
fi

exec "$PY" -m viralcut "$@"
