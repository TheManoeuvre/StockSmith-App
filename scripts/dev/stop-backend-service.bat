@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-backend-service.ps1"
pause
