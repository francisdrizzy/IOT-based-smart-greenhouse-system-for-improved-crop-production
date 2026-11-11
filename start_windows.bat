@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  set "GREENHOUSE_SERIAL_PORT=auto"
) else (
  set "GREENHOUSE_SERIAL_PORT=%~1"
)
set "GREENHOUSE_SERIAL_BAUD=9600"
python app.py
endlocal
