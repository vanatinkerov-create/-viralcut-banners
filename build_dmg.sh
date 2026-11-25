#!/usr/bin/env bash
# Сборка дистрибутива: DMG (если разрешает система) + ZIP (всегда)
set -euo pipefail
cd "$(dirname "$0")"

VERSION="$(python3 tools/version.py)"
NAME="ViralCut-${VERSION}-macOS-arm64"

APP="dist/ViralCut.app"
[ -d "$APP" ] || { echo "Сначала соберите приложение: ./build_app.sh"; exit 1; }

# ZIP — универсальный вариант, сохраняет подпись и права
ditto -c -k --sequesterRsrc --keepParent "$APP" "dist/${NAME}.zip"
du -sh "dist/${NAME}.zip"

# DMG — красивее, но требует прав на создание образов
STAGE="build/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
# Инструкция для тестера: Gatekeeper, что проверить, куда смотреть в логе
cp docs/MACOS-TEST.md "$STAGE/ЧИТАЙ-МЕНЯ.txt"
ln -s /Applications "$STAGE/Applications"

if hdiutil create -volname "ViralCut" -srcfolder "$STAGE" -ov -format UDZO \
    "dist/ViralCut-${VERSION}.dmg" >/dev/null 2>&1; then
  echo "DMG: dist/ViralCut-${VERSION}.dmg"
else
  echo "DMG создать не удалось (нет прав) — используйте ZIP."
fi
rm -rf "$STAGE"
