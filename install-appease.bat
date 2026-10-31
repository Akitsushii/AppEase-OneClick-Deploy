@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  AppEase - install or update from GitHub, in one file
REM
REM  The only file a new server computer needs. Download it on its
REM  own and double-click it. It:
REM   1. Downloads the latest AppEase from GitHub
REM   2. Copies it into C:\AppEase
REM   3. Runs C:\AppEase\setup-server.bat (Python, packages, keys,
REM      firewall, desktop shortcut)
REM
REM  Run it again later to UPDATE. Files are only added or replaced,
REM  never deleted, and the repository holds no data, so server\.env,
REM  the database, uploaded installers and photos are never touched.
REM  They are also excluded by name below, in case that ever changes.
REM ============================================================

set "REPO_ZIP_URL=https://github.com/Akitsushii/AppEase-OneClick-Deploy/archive/refs/heads/main.zip"
set "INSTALLDIR=C:\AppEase"

REM ---------- Self-elevate to administrator ----------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo(
echo ==== AppEase Installer ====
echo Install folder : %INSTALLDIR%
echo Download from  : %REPO_ZIP_URL%
echo(

if exist "%INSTALLDIR%\server\app.py" (
    echo AppEase is already installed here, so this will UPDATE it.
    echo Your settings, database, installers and photos are kept.
    echo(
)

REM ---------- AppEase must not be running while its files change ----------
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo AppEase seems to be running ^(something is using port 8080^).
    echo Close the "AppEase Server" window, then run this file again.
    echo(
    pause
    exit /b 1
)

set "WORK=%TEMP%\appease_install_%RANDOM%%RANDOM%"
mkdir "%WORK%" >nul 2>&1

REM ---------- Step 1: download ----------
echo [1/3] Downloading AppEase...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri $env:REPO_ZIP_URL -OutFile (Join-Path $env:WORK 'appease.zip') -UseBasicParsing; Expand-Archive -Path (Join-Path $env:WORK 'appease.zip') -DestinationPath (Join-Path $env:WORK 'src') -Force; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo(
    echo ERROR: AppEase could not be downloaded. Check this computer's
    echo internet connection and try again.
    goto FAIL
)

REM  GitHub puts everything inside one folder, e.g. AppEase-OneClick-Deploy-main.
set "SRC="
for /d %%D in ("%WORK%\src\*") do set "SRC=%%D"
if not defined SRC goto BAD_DOWNLOAD
if not exist "!SRC!\server\app.py" goto BAD_DOWNLOAD
if not exist "!SRC!\setup-server.bat" goto BAD_DOWNLOAD
echo       Downloaded.

REM ---------- Step 2: copy into C:\AppEase ----------
REM  robocopy without /MIR or /PURGE only adds and replaces; it never deletes
REM  anything already in C:\AppEase. Exit codes below 8 mean success.
echo [2/3] Copying AppEase into %INSTALLDIR% ...
robocopy "!SRC!" "%INSTALLDIR%" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP ^
    /XF .env appease.db appease.db-wal appease.db-shm appease.db-journal ^
    /XD venv .git >nul
if errorlevel 8 (
    echo(
    echo ERROR: the files could not be copied into %INSTALLDIR%.
    goto FAIL
)
echo       Copied.
rmdir /s /q "%WORK%" >nul 2>&1

REM ---------- Step 3: set up ----------
echo [3/3] Setting up the server...
echo(
call "%INSTALLDIR%\setup-server.bat"
exit /b %errorlevel%

:BAD_DOWNLOAD
echo(
echo ERROR: the download does not look like AppEase with its setup files.
echo The copy on GitHub may be older than this installer.
goto FAIL

:FAIL
rmdir /s /q "%WORK%" >nul 2>&1
echo(
pause
exit /b 1
