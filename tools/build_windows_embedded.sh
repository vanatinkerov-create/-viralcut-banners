#!/usr/bin/env bash
# Переносимая Windows-сборка «всё внутри»: встроенный Python, зависимости,
# модель Whisper и Node/PO-провайдер. Пользователь распаковывает ZIP и запускает
# ViralCut.bat — ничего не скачивается и не устанавливается.
#
# Это НЕ .exe (PyInstaller не кросс-компилирует с macOS). Нужен, чтобы отдать
# полноценную сборку без Windows-машины; exe собирается в CI или build_windows.ps1.
#
# Подготовка колёс (один раз):
#   python tools/resolve_windows_wheels.py --dest build/windows-wheels
# Сборка:
#   ./tools/build_windows_embedded.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 tools/version.py)"
PY_EMBED_VER="${PY_EMBED_VER:-3.12.10}"
WHEELS="${WHEELS:-build/windows-wheels}"
MODEL="${MODEL:-build/whisper-small}"
STAGE="build/windows-embedded"
OUTDIR="dist"
NAME="ViralCut-${VERSION}-windows-x64-portable"
OUT="$OUTDIR/$NAME.zip"

[ -d "$WHEELS" ] || { echo "Нет колёс: $WHEELS. Запустите tools/resolve_windows_wheels.py"; exit 1; }
[ -f "$MODEL/model.bin" ] || { echo "Нет модели: $MODEL/model.bin"; exit 1; }

PYCACHE="build/python-embed-$PY_EMBED_VER.zip"
if [ ! -f "$PYCACHE" ]; then
  mkdir -p build
  echo "Качаю Python $PY_EMBED_VER (embeddable)..."
  curl -fL --retry 3 -o "$PYCACHE" \
    "https://www.python.org/ftp/python/$PY_EMBED_VER/python-$PY_EMBED_VER-embed-amd64.zip"
fi

rm -rf "$STAGE"
APPDIR="$STAGE/ViralCut"
mkdir -p "$APPDIR"/{python,site-packages,desktop,tools,models}

echo "Распаковываю Python..."
unzip -q "$PYCACHE" -d "$APPDIR/python"

echo "Распаковываю колёса..."
for wheel in "$WHEELS"/*.whl; do
  unzip -q -o "$wheel" -d "$APPDIR/site-packages"
done
rm -rf "$APPDIR"/site-packages/*.data

echo "sdist-пакеты (proxy-tools)..."
for sdist in "$WHEELS"/sdists/*.tar.gz; do
  [ -f "$sdist" ] || continue
  tmp="$(mktemp -d)"
  tar xzf "$sdist" -C "$tmp"
  for pkg in "$tmp"/*/; do
    base="$(basename "$pkg")"
    [ "$base" = "PKG-INFO" ] && continue
    if [ -f "$pkg/__init__.py" ] && [ ! -e "$APPDIR/site-packages/$base" ]; then
      cp -R "$pkg" "$APPDIR/site-packages/$base"
    fi
  done
  rm -rf "$tmp"
done

echo "Копирую приложение..."
cp -R viralcut "$APPDIR/viralcut"
cp -R web "$APPDIR/web"
cp -R assets "$APPDIR/assets"
cp desktop/main.py "$APPDIR/desktop/main.py"
cp desktop/vendor.tar.gz "$APPDIR/desktop/vendor.tar.gz"
cp tools/analyze_app_log.py "$APPDIR/tools/analyze_app_log.py"
cp LICENSE "$APPDIR/LICENSE"
cp -R "$MODEL" "$APPDIR/models/whisper-small"
find "$APPDIR" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APPDIR" -name ".DS_Store" -delete 2>/dev/null || true

cat > "$APPDIR/python/python312._pth" <<'PTH'
python312.zip
.
..
..\site-packages
import site
PTH

python3 - "$APPDIR" "$VERSION" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = sys.argv[2]


def write(name: str, text: str) -> None:
    (root / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


write("ViralCut.bat", r'''@echo off
setlocal
cd /d "%~dp0"
set "PYTHONNET_PYDLL=%CD%\python\python312.dll"
set "PYTHONNET_RUNTIME=netfx"
set "VIRALCUT_WHISPER_DIR=%CD%\models\whisper-small"
start "" "%CD%\python\pythonw.exe" "%CD%\desktop\main.py"
''')

write("ViralCut-console.bat", r'''@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONNET_PYDLL=%CD%\python\python312.dll"
set "PYTHONNET_RUNTIME=netfx"
set "VIRALCUT_WHISPER_DIR=%CD%\models\whisper-small"
echo Запуск ViralCut в консольном режиме (ошибки видны здесь)...
"%CD%\python\python.exe" "%CD%\desktop\main.py"
echo.
echo Приложение закрылось. Лог: %APPDATA%\ViralCut\app.log
pause
''')

write("Diagnostics.bat", r'''@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
"%CD%\python\python.exe" "%CD%\tools\analyze_app_log.py" %*
echo.
pause
''')

write("ViralCut.vbs", r'''Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
sh.Run """" & root & "\ViralCut.bat""", 0, False
''')

write("README-WINDOWS-PORTABLE.txt", f'''ViralCut {version} — переносимая сборка для Windows 10/11 x64
==========================================================

Внутри уже есть всё: Python, зависимости, ffmpeg и модель распознавания речи.
Докачивать ничего не нужно — только распаковать и запустить.

Запуск
------
1. Распакуйте папку целиком (не запускайте из ZIP-архива).
2. Двойной клик по ViralCut.bat (или ViralCut.vbs — без мелькания консоли).

Если окно не открылось
----------------------
1. Запустите ViralCut-console.bat — там будет текст ошибки.
2. Пришлите %APPDATA%\\ViralCut\\app.log (или запустите Diagnostics.bat).
3. Windows 10 без WebView2 Runtime: поставьте «Microsoft Edge WebView2 Runtime»
   с сайта Microsoft (на Windows 11 он обычно уже есть).

Что проверить
-------------
1. Загрузка файла или ссылки, в параметрах — «Вырезать паузы».
2. «Пересобрать в 1080p» — окно не должно закрываться.
3. Виджет нагрузки: CPU/RAM/GPU (в подсказке — источник pdh/nvidia-smi/adl).
4. Настройки → «Разобрать журнал»: версия {version} и состояние системы.
5. Закрыть окно и снова открыть журнал: должен быть маркер stop.

Что прислать при проблеме
-------------------------
- app.log целиком (Diagnostics.bat) и на каком шаге упало.
- Версию Windows и модель видеокарты.

Ограничения
-----------
- Сборка сделана на macOS из win_amd64-колёс и на живой Windows ещё не
  проверялась. Если что-то не запускается — пришлите app.log.
- Приложение не подписано: SmartScreen → «Подробнее» → «Выполнить всё равно».
- YouTube HD зависит от PO-token провайдера (Node внутри); при сбое — SD.
''')
PY

mkdir -p "$OUTDIR"
rm -f "$OUT"
echo "Папую $OUT..."
(cd "$STAGE" && zip -rq -X -6 "../../$OUT" ViralCut)
SIZE="$(du -h "$OUT" | cut -f1 | tr -d ' ')"
SHA="$(shasum -a 256 "$OUT" | cut -d' ' -f1)"
printf '%s  %s\n' "$SHA" "$(basename "$OUT")" > "$OUTDIR/SHA256SUMS-windows-portable.txt"
echo "готово: $OUT ($SIZE)"
echo "sha256: $SHA"
