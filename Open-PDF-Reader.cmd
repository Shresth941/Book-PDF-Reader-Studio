@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Local.ps1" -OpenBrowser
if errorlevel 1 pause
