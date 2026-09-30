@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0scripts\reset.ps1"
pause
