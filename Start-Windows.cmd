@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    py -3.14 -m venv .venv
    if errorlevel 1 goto missing
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info[:2] != (3, 14))"
if errorlevel 1 goto missing
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
if /i "%~1"=="--check" (
    ".venv\Scripts\python.exe" -B -m unittest discover -s gateway/tests -q
    exit /b
)
echo Open http://127.0.0.1:8765/ in your browser.
".venv\Scripts\python.exe" -B -m gateway.web_demo --guided-demo
if errorlevel 1 goto failed
exit /b 0

:missing
echo CPython 3.14 and the Windows py launcher are required.
goto failed

:failed
echo Setup or startup failed. See the message above.
if /i not "%~1"=="--check" pause
exit /b 1
