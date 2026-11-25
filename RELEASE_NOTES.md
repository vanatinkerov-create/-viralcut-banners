# ViralCut 1.2.1

Локальная нарезка длинных видео в вертикальные клипы для YouTube Shorts, TikTok и Instagram Reels. Всё считается на вашем компьютере: видео, звук и текст никуда не отправляются.

## Скачать

| Платформа | Файл |
|---|---|
| **Windows 10/11 x64** | [ViralCut-1.2.1-windows-x64.zip](https://github.com/vanwild11/viralcut/releases/download/v1.2.1/ViralCut-1.2.1-windows-x64.zip) |
| **macOS 11+ Apple Silicon** | [ViralCut-1.2.1-macOS-arm64.zip](https://github.com/vanwild11/viralcut/releases/download/v1.2.1/ViralCut-1.2.1-macOS-arm64.zip) |

## Что нового в 1.2.1

- **Исправлен запуск Windows-версии.** Ошибка `Failed to resolve Python.Runtime.Loader.Initialize`
  возникала при инициализации pythonnet: `Python.Runtime.dll` не находил `python3XX.dll`.
  Добавлен runtime-hook, который выставляет `PYTHONNET_PYDLL`, `PYTHONNET_RUNTIME=netfx`,
  добавляет каталоги сборки в DLL search path и снимает метку «скачано из интернета».
- **Стабильный релиз 1.2.0** — без beta, с Telegram-каналом и GitHub Actions.
- Windows-сборка собирается в GitHub Actions, macOS-архив — локально.

## Что было в 1.2.0

- Стабильный релиз: убрана пометка beta.
- Telegram-канал [@ivanAI22](https://t.me/ivanAI22) в сайдбаре и на главной.
- Нативный Windows-запуск, WebView2, кроссплатформенные пути и блокировки.

Внутри архива: модель Whisper Small, ffmpeg, 24 шрифта, определитель лиц.
На macOS дополнительно Node и PO-провайдер для YouTube; Windows-сборка работает без Node.

Если Windows показывает SmartScreen: **«Подробнее» → «Выполнить в любом случае»**.
