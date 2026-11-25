# ViralCut для Windows 10 и 11

## Скачать и запустить

1. Скачайте `ViralCut-1.2.1-windows-x64.zip` из раздела **Releases** на GitHub.
2. Распакуйте архив в любую папку, например `C:\ViralCut`.
3. Запустите `ViralCut.exe`.

Сборка не подписана сертификатом, поэтому SmartScreen может показать предупреждение:
**«Подробнее» → «Выполнить в любом случае»**. Это нормально для открытого проекта.

На Windows 10 нужен **WebView2 Runtime** — он уже установлен на большинстве систем
вместе с Microsoft Edge. Если окно не открывается, поставьте runtime с
<https://developer.microsoft.com/microsoft-edge/webview2/>.

## Где лежат данные

- Проекты, клипы, модель и логи: `%APPDATA%\ViralCut`
- Сохранённые клипы (кнопка «Скачать»): `%USERPROFILE%\Downloads\ViralCut`
- Лог: `%APPDATA%\ViralCut\app.log`

Удалить программу можно простым удалением папки с `ViralCut.exe`; данные останутся
в `%APPDATA%\ViralCut`, пока вы не удалите их вручную.

## Запуск из исходников (без сборки)

Нужен Python 3.12 x64 с <https://www.python.org/downloads/windows/> (галочка
«Add python.exe to PATH»).

Дважды кликните `install_windows.bat` — скрипт через `winget` поставит Python 3.12 и
WebView2, затем создаст `.venv-win`, поставит зависимости и откроет приложение.
Если Python уже установлен, достаточно `run_windows.bat`. Первый запуск занимает
несколько минут.

## Сборка exe

В PowerShell из папки проекта:

```powershell
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```

Скрипт создаст `.venv-win`, поставит PyInstaller, скачает модель Whisper Small
(если её нет), соберёт `dist\ViralCut\ViralCut.exe` и упакует
`release\ViralCut-1.2.1-windows-x64.zip`.

Флаги:

- `-SkipModel` — не скачивать модель (приложение докачает её при первом запуске).
- `-Version 1.2.1` — версия в имени архива.

## GitHub Actions

В репозитории уже есть `.github/workflows/build.yml`:

- **Windows** — собирает exe, запускает headless-проверку `/api/health` и загружает ZIP.
- **Release** — при пуше тега `v*` прикрепляет Windows ZIP к GitHub Release.

macOS-архив для Apple Silicon собирается локально (`./build_app.sh`) и загружается
в релиз вручную.

Первый пуш:

```bash
git init
git add .
git commit -m "ViralCut 1.2.1"
git branch -M main
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```

Тег для релиза:

```bash
git tag v1.2.1
git push origin v1.2.1
```

## Ограничения Windows-сборки

- Нет автоподписи и автообновления.
- YouTube может ограничивать качество без PO-токена; нарезка Twitch/Kick, локальных
  файлов и прямых эфиров работает полностью.
- Модель Whisper Small по возможности входит в ZIP (≈460 МБ); если при сборке её не было,
  приложение скачает модель при первом запуске.
