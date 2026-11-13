@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    py -3.14 -m venv .venv
    if errorlevel 1 goto python_missing
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info[:2] != (3, 14))"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
if /i "%~1"=="--check" (
    if not exist "gateway\tests" (
        echo Tests are available in the verifiable source bundle.
        exit /b 1
    )
    ".venv\Scripts\python.exe" -B -m unittest discover -s gateway/tests -q
    exit /b
)
echo Open http://127.0.0.1:8765/ in your browser.
if "%~1"=="" (
    ".venv\Scripts\python.exe" -B -m gateway.web_demo --guided-demo
) else (
    ".venv\Scripts\python.exe" -B -m gateway.web_demo --prefill-role-logins %*
)
if errorlevel 1 goto failed
exit /b 0

:python_missing
echo Python 3.14 is required. Install the Python Install Manager from https://www.python.org/downloads/windows/
echo Then open a new terminal and run: py install 3.14
echo Installation steps are in docs\TELEPITES_WINDOWS.md.
pause
exit /b 1

:failed
echo Setup or startup failed. See the message above.
pause
exit /b 1
