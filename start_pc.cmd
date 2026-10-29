@echo off
setlocal
cd /d "%~dp0"
"%~dp0dependencies\windows\python\python.exe" -B "%~dp0PC\start_pc.py" %*
if errorlevel 1 pause
