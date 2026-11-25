# Как опубликовать релиз на GitHub

Артефакты уже лежат в `release/v1.2.1/`:

- `ViralCut-1.2.1-macOS-arm64.zip` — приложение для Apple Silicon;
- `ViralCut-1.2.1-source.zip` — исходники без данных и сборок;
- `SHA256SUMS.txt` — контрольные суммы;
- `RELEASE_NOTES.md` — текст релиза.

## Через GitHub CLI

```bash
gh release create v1.2.1 \
  release/v1.2.1/ViralCut-1.2.1-macOS-arm64.zip \
  release/v1.2.1/ViralCut-1.2.1-source.zip \
  release/v1.2.1/SHA256SUMS.txt \
  --title "ViralCut 1.0.0 — локальная нарезка видео" \
  --notes-file release/v1.2.1/RELEASE_NOTES.md
```

## Через веб-интерфейс

1. Создайте репозиторий и загрузите исходники (без `data/`, `dist/`, `build/`).
2. Откройте **Releases → Draft a new release**.
3. Тег: `v1.2.1`, заголовок: `ViralCut 1.0.0`.
4. Прикрепите ZIP с приложением и исходники, вставьте текст из `RELEASE_NOTES.md`.
5. Опубликуйте.

Перед публикацией проверьте:

```bash
shasum -a 256 -c release/v1.2.1/SHA256SUMS.txt
codesign --verify --deep --strict /path/to/ViralCut.app
```

ZIP весит ~620 МБ и превышает обычный лимит файла в git, поэтому его нужно
прикреплять именно к Release, а не коммитить в репозиторий.

## Windows

Сборка выполняется на Windows-машине или в GitHub Actions:

```powershell
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```

Готовый архив — `release/ViralCut-1.2.1-windows-x64.zip`.
