@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  AppEase Server - one-time setup
REM  Double-click this file on the computer that will be the
REM  AppEase server. Run it again any time: it skips what is done.
REM
REM  1. Makes sure a machine-wide Python 3.10 or newer is installed
REM  2. Creates server\venv and installs requirements.txt into it
REM  3. Creates server\.env and fills in SECRET_KEY / REPORT_QR_SECRET
REM  4. Opens TCP 8080 (agents) and 80 (browsers) in Windows Firewall
REM  5. Puts a "Start AppEase" shortcut on the desktop
REM
REM  It does NOT start AppEase. Use start-appease.bat (or the desktop
REM  shortcut) for that.
REM ============================================================

REM ---------- Self-elevate to administrator ----------
REM  Needed for the firewall rules and a machine-wide Python.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
set "ROOT=%~dp0"
set "SERVERDIR=%ROOT%server"
set "VENVPY=%SERVERDIR%\venv\Scripts\python.exe"
set "PYEXE="

REM ---- Python fallback (used only if winget fails) ----
set "PY_WINGET_ID=Python.Python.3.14"
set "PY_VERSION=3.14.6"
set "PY_URL=https://www.python.org/ftp/python/3.14.6/python-3.14.6-amd64.exe"
set "PY_SHA256=14b3e9a710a3fcf0bd9b55ab6b60412bd91227563f813fc49040cabc0209e0bd"

echo(
echo ==== AppEase Server Setup ====
echo Folder : %ROOT%
echo(

if not exist "%SERVERDIR%\app.py" (
    echo ERROR: server\app.py was not found next to this file.
    echo Run setup-server.bat from the AppEase folder.
    echo(
    pause
    exit /b 1
)

REM ---------- Step 1: Python ----------
echo [1/5] Checking for Python 3.10 or newer ^(machine-wide^)...
call :FIND_PYTHON
if defined PYEXE (
    for /f "delims=" %%V in ('""!PYEXE!" --version" 2^>^&1') do set "PYVER=%%V"
    echo       Found: !PYEXE!  ^(!PYVER!^)
) else (
    echo       Not found. Installing Python %PY_VERSION%...
    where winget >nul 2>&1
    if not errorlevel 1 (
        winget install --id %PY_WINGET_ID% --scope machine --silent ^
            --accept-package-agreements --accept-source-agreements
        call :FIND_PYTHON_INSTALLED
        if not defined PYEXE call :FIND_PYTHON
    )
    if not defined PYEXE (
        echo       Trying a direct download from python.org...
        call :INSTALL_FROM_PYTHONORG
        call :FIND_PYTHON_INSTALLED
        if not defined PYEXE call :FIND_PYTHON
    )
)
if not defined PYEXE (
    echo(
    echo ERROR: Python could not be installed. Check this computer's internet
    echo connection, or install Python %PY_VERSION% yourself with "Install for
    echo all users" and "Add to PATH" ticked, then run this file again.
    echo(
    pause
    exit /b 1
)

REM ---------- Step 2: virtual environment and packages ----------
echo [2/5] Installing AppEase's Python packages ^(this can take a few minutes^)...
if not exist "%VENVPY%" (
    "!PYEXE!" -m venv "%SERVERDIR%\venv"
    if errorlevel 1 (
        echo ERROR: could not create server\venv.
        pause
        exit /b 1
    )
)
"%VENVPY%" -m pip install --disable-pip-version-check -r "%ROOT%requirements.txt"
if errorlevel 1 (
    echo(
    echo ERROR: the packages could not be installed. Check the internet
    echo connection and run this file again.
    echo(
    pause
    exit /b 1
)
echo       Packages installed.

REM ---------- Step 3: settings file ----------
echo [3/5] Preparing server\.env ...
"%VENVPY%" "%SERVERDIR%\server_setup.py" env
if errorlevel 1 (
    pause
    exit /b 1
)

REM ---------- Step 4: firewall ----------
REM  Deleted first so re-running never stacks duplicate rules.
echo [4/5] Allowing AppEase through Windows Firewall...
for %%P in (8080 80) do (
    netsh advfirewall firewall delete rule name="AppEase Server (TCP %%P)" >nul 2>&1
    netsh advfirewall firewall add rule name="AppEase Server (TCP %%P)" dir=in action=allow protocol=TCP localport=%%P profile=any >nul
    if errorlevel 1 (
        echo       WARNING: could not add the rule for port %%P.
    ) else (
        echo       Port %%P allowed.
    )
)

REM ---------- Step 5: desktop shortcut ----------
REM  The PUBLIC desktop, so the shortcut shows for whoever signs in, even
REM  when the account that answered the admin prompt is a different one.
echo [5/5] Adding a "Start AppEase" shortcut to the desktop...
set "LINK=%PUBLIC%\Desktop\Start AppEase.lnk"
set "ICON=%SERVERDIR%\static\img\appease.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); $s.TargetPath = Join-Path $env:ROOT 'start-appease.bat'; $s.WorkingDirectory = $env:ROOT.TrimEnd('\'); $s.Description = 'Start the AppEase server'; if (Test-Path $env:ICON) { $s.IconLocation = $env:ICON }; $s.Save()" >nul 2>&1
if exist "%LINK%" (
    echo       Shortcut added.
) else (
    echo       WARNING: the shortcut could not be created. Use start-appease.bat instead.
)

echo(
echo ==== Done ====
echo To start AppEase, double-click "Start AppEase" on the desktop
echo ^(or start-appease.bat in this folder^) and keep its window open.
echo(
echo Once it is running, open it at:
"%VENVPY%" "%SERVERDIR%\server_setup.py" addresses
echo(
echo FIRST TIME ONLY: the Start window prints the starting password for the
echo "admin" account. Sign in with it and choose your own password.
echo(
pause
exit /b 0


REM ============================================================
REM  Subroutine: download the official python.org installer,
REM  verify its SHA-256, and install it machine-wide silently.
REM ============================================================
:INSTALL_FROM_PYTHONORG
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo       ERROR: this PC is not 64-bit x64; the pinned installer won't fit.
    exit /b 1
)
set "PYINSTALLER=%TEMP%\python-%PY_VERSION%-amd64.exe"
echo       Downloading Python %PY_VERSION% ^(~29 MB^) from python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PYINSTALLER%' -UseBasicParsing; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 exit /b 1
if not exist "%PYINSTALLER%" exit /b 1
set "DLHASH="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -Path '%PYINSTALLER%').Hash"`) do set "DLHASH=%%H"
if /i not "!DLHASH!"=="%PY_SHA256%" (
    echo       ERROR: checksum mismatch - the download is corrupt or tampered.
    del "%PYINSTALLER%" >nul 2>&1
    exit /b 1
)
"%PYINSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1
set "PYRC=!errorlevel!"
del "%PYINSTALLER%" >nul 2>&1
if not "!PYRC!"=="0" (
    echo       ERROR: the Python installer exited with code !PYRC!.
    exit /b 1
)
exit /b 0


REM ============================================================
REM  Subroutine: find a real, MACHINE-WIDE Python 3.10 or newer.
REM  Rejects the Microsoft Store stub and per-user installs: the
REM  account that answered the admin prompt may not be the one
REM  that later double-clicks Start AppEase.
REM ============================================================
:FIND_PYTHON
set "PYEXE="
set "CAND="
for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "CAND=%%I"
if defined CAND call :VALIDATE_CAND
if defined PYEXE exit /b
set "CAND="
for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "CAND=%%I"
if defined CAND call :VALIDATE_CAND
if defined PYEXE exit /b
call :FIND_PYTHON_INSTALLED
exit /b

:VALIDATE_CAND
REM  Substring tests, not "echo | find": a pipe runs each side in a new
REM  cmd.exe with delayed expansion off, so !CAND! would never expand.
if /i not "!CAND:WindowsApps=!"=="!CAND!" exit /b
if /i not "!CAND:\AppData\=!"=="!CAND!" exit /b
if not exist "!CAND!" exit /b
"!CAND!" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 exit /b
set "PYEXE=!CAND!"
exit /b

:FIND_PYTHON_INSTALLED
set "PYEXE="
for /d %%D in ("%ProgramFiles%\Python3*") do if exist "%%D\python.exe" (
    set "CAND=%%D\python.exe"
    call :VALIDATE_CAND
)
exit /b
