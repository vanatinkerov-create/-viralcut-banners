#!/usr/bin/env bash
# Переносимый Windows-пакет из исходников (собирается на macOS, без exe).
# Использование: ./tools/build_portable_windows.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 tools/version.py)"
STAGE="build/portable-windows"
OUT="dist/ViralCut-${VERSION}-windows-portable-src.zip"

rm -rf "$STAGE"
mkdir -p "$STAGE"

copy() { cp -R "$1" "$STAGE/$2"; }

copy viralcut viralcut
copy web web
copy assets assets
copy tests tests
mkdir -p "$STAGE/desktop"
copy desktop/main.py desktop/main.py
copy desktop/pyi_rth_pythonnet.py desktop/pyi_rth_pythonnet.py
[ -f desktop/vendor.tar.gz ] && copy desktop/vendor.tar.gz desktop/vendor.tar.gz
mkdir -p "$STAGE/tools"
copy tools/version.py tools/version.py
copy tools/windows_smoke.ps1 tools/windows_smoke.ps1
copy tools/analyze_app_log.py tools/analyze_app_log.py
copy requirements.txt requirements.txt
copy requirements-desktop.txt requirements-desktop.txt
copy install_windows.bat install_windows.bat
copy run_windows.bat run_windows.bat
copy build_windows.ps1 build_windows.ps1
copy ViralCut.spec ViralCut.spec
copy LICENSE LICENSE
copy THIRD_PARTY_NOTICES.md THIRD_PARTY_NOTICES.md
copy docs/WINDOWS-TEST.md README-WINDOWS-TEST.md

# Кэши и тестовые артефакты в пакет не нужны
find "$STAGE" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name "*.pyc" -delete 2>/dev/null || true
find "$STAGE" -name ".DS_Store" -delete 2>/dev/null || true

mkdir -p dist
rm -f "$OUT"
(cd "$STAGE" && zip -rq -X "../../$OUT" .)

SIZE="$(du -h "$OUT" | cut -f1 | tr -d ' ')"
SHA="$(shasum -a 256 "$OUT" | cut -d' ' -f1)"
printf '%s  %s\n' "$SHA" "$(basename "$OUT")" > "dist/SHA256SUMS-windows-portable.txt"
echo "готово: $OUT ($SIZE)"
echo "sha256: $SHA"
