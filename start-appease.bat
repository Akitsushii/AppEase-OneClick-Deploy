@echo off
setlocal EnableExtensions

REM ============================================================
REM  Start the AppEase server. Keep this window open while AppEase
REM  is in use; closing it stops AppEase.
REM  Run setup-server.bat once before using this.
REM ============================================================

cd /d "%~dp0"
title AppEase Server
set "VENVPY=%~dp0server\venv\Scripts\python.exe"

if not exist "%VENVPY%" goto NOT_SET_UP
if not exist "%~dp0server\.env" goto NOT_SET_UP

echo ==== AppEase Server ====
echo(
echo Open AppEase in a web browser at:
"%VENVPY%" "%~dp0server\server_setup.py" addresses
echo(
echo Keep this window open while AppEase is in use.
echo To stop AppEase, close this window.
echo ------------------------------------------------------------
echo(

REM  Opens the dashboard a few seconds from now, once the server is
REM  listening. Port 8080 always serves browsers too, even when 80 is busy.
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 5; Start-Process 'http://localhost:8080/'"

cd /d "%~dp0server"
"%VENVPY%" app.py --prod

echo(
echo ------------------------------------------------------------
echo AppEase has stopped. If that was not on purpose, read the
echo messages above. A common cause is AppEase already running in
echo another window, which holds port 8080.
echo(
pause
exit /b 1

:NOT_SET_UP
echo AppEase is not set up on this computer yet.
echo Double-click setup-server.bat in this folder first, then try again.
echo(
pause
exit /b 1
