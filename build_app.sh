#!/usr/bin/env bash
# Сборка ViralCut.app (macOS, Apple Silicon)
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv312/bin/pyinstaller" ]; then
  PY=".venv312/bin/python"
elif [ -x ".venv/bin/pyinstaller" ]; then
  PY=".venv/bin/python"
else
  echo "Ставлю зависимости для сборки…"
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt pyinstaller pywebview
  PY=".venv/bin/python"
fi

# Кэш PyInstaller держим внутри проекта: сборка не пишет в ~/Library
# и остаётся воспроизводимой на любой машине и в CI.
export PYINSTALLER_CONFIG_DIR="$PWD/build/pyinstaller-config"
mkdir -p "$PYINSTALLER_CONFIG_DIR"
PYTHONPYCACHEPREFIX="$PWD/.pycache" "$PY" -m PyInstaller ViralCut.spec --noconfirm --clean

APP="dist/ViralCut.app"

echo "Подписываю ad-hoc…"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP" && echo "Подпись в порядке"

du -sh "dist/ViralCut.app"
echo "Готово: dist/ViralCut.app"
