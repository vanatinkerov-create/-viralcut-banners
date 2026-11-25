# Сторонние компоненты

ViralCut использует следующие открытые компоненты. Их лицензии сохраняются за
авторами; приложение не меняет условия этих лицензий.

| Компонент | Назначение | Лицензия |
|---|---|---|
| Python 3.12 | среда выполнения | PSF License |
| FastAPI / Uvicorn | локальный HTTP API | MIT / BSD |
| yt-dlp | скачивание видео | Unlicense |
| bgutil-ytdlp-pot-provider | локальная генерация PO-token | MIT |
| ffmpeg (imageio-ffmpeg build) | рендер, склейка, ASS | LGPL/GPL (сборка включает libx264 — GPL) |
| faster-whisper / CTranslate2 | распознавание речи | MIT |
| Whisper Small (Systran/faster-whisper-small) | модель речи | MIT |
| OpenCV + YuNet | поиск лиц | Apache-2.0 / MIT |
| pywebview | окно macOS | BSD-3-Clause |
| Pillow, NumPy, onnxruntime | обработка | HPND / BSD / MIT |
| Шрифты в `assets/fonts` | субтитры | SIL Open Font License 1.1 |

FFmpeg собран с компонентами GPL (libx264). Исходники FFmpeg и скрипты сборки
доступны в репозитории; при распространении бинарной сборки действуют условия
GPL для соответствующих компонентов.

Шрифты распространяются по SIL OFL 1.1: их можно использовать и встраивать в
видео, но нельзя продавать отдельно как шрифты.
