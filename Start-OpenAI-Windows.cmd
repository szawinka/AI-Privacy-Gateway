@echo off
setlocal
cd /d "%~dp0"
call "%~dp0Start-Windows.cmd" --allow-https-target openai-responses https://api.openai.com/v1
exit /b %errorlevel%
