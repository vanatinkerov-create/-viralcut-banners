@echo off
setlocal
cd /d "%~dp0"

where winget >nul 2>&1
if errorlevel 1 (
  echo Не найден winget. Установите Python 3.12 вручную:
  echo   https://www.python.org/downloads/windows/
  echo и WebView2 Runtime:
  echo   https://developer.microsoft.com/microsoft-edge/webview2/
  pause
  exit /b 1
)

echo [ViralCut] ставлю Python 3.12...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements

echo [ViralCut] ставлю WebView2 Runtime...
winget install -e --id Microsoft.EdgeWebView2Runtime --accept-source-agreements --accept-package-agreements

echo.
echo [ViralCut] запускаю установку зависимостей и приложение...
call run_windows.bat
