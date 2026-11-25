# Настольная версия (macOS)

`desktop/main.py` — точка входа .app: поднимает локальный сервер на 127.0.0.1 и открывает
окно WKWebView через pywebview. Данные хранятся в `~/Library/Application Support/ViralCut`.

Сборка:

```bash
./build_app.sh     # dist/ViralCut.app
./build_dmg.sh     # dist/ViralCut-1.0.0.dmg
```

Запуск без окна (для проверки):

```bash
VIRALCUT_HEADLESS=1 VIRALCUT_DATA=/tmp/viralcut dist/ViralCut.app/Contents/MacOS/ViralCut
```

Лог: `~/Library/Application Support/ViralCut/app.log`.
