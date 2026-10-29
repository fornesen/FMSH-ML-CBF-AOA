@echo off
setlocal
cd /d "%~dp0"
"%~dp0..\..\dependencies\windows\python\python.exe" -B "%~dp0server.py" --demo --http-port 8769 --open %*
if errorlevel 1 pause
