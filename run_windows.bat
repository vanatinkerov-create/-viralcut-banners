@echo off
setlocal
cd /d "%~dp0"

set "PY=py -3.12"
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 set "PY=python"

if not exist ".venv-win\Scripts\python.exe" (
  echo [ViralCut] создаю виртуальное окружение...
  %PY% -m venv .venv-win || goto :error
)

call ".venv-win\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
echo [ViralCut] ставлю зависимости (первый запуск — несколько минут)...
pip install -r requirements-desktop.txt || goto :error
echo [ViralCut] запускаю...
python desktop\main.py
goto :eof

:error
echo.
echo [ViralCut] Не удалось запустить. Проверьте, что установлен Python 3.12 x64:
echo   https://www.python.org/downloads/windows/
pause
exit /b 1
