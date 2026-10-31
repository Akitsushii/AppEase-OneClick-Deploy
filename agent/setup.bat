@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  AppEase Agent - one-shot setup for a new lab computer
REM  1. Ensures a REAL Python is installed (ignores MS Store stub)
REM  2. Asks for the AppEase server's address and checks it answers
REM  3. Copies agent.py to the system drive, e.g. C:\Appease\agent.py,
REM     with that address written into the installed copy
REM  4. Registers "AppEase Agent" scheduled task (runs at startup, whether
REM     logged on or not, as the user who started this script)
REM  5. Starts the agent immediately
REM  6. Installs Chocolatey if it is not already present
REM
REM  Re-running overwrites the file AND the task, and restarts a
REM  running agent so the new code actually takes effect. On a re-run
REM  the address already in use is offered: press Enter to keep it.
REM
REM  Nobody needs to edit agent.py by hand. If its SERVER_BASE_URL is
REM  already a real address, that is offered as the default instead.
REM
REM  Run this from the folder that also contains agent.py.
REM ============================================================

REM ---------- Capture the invoking (pre-elevation) user ----------
REM  The scheduled task must run as THIS user (the person setting up the
REM  lab PC / the account students use), not whatever admin answers UAC.
REM  We capture it before elevating and pass it through as arg 1.
set "TARGETUSER=%~1"
if not defined TARGETUSER set "TARGETUSER=%USERDOMAIN%\%USERNAME%"

REM ---------- Self-elevate to administrator ----------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%TARGETUSER%' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

REM ---------- Configuration ----------
set "INSTALLDIR=%SystemDrive%\Appease"
set "TASKNAME=AppEase Agent"
set "SRCAGENT=%~dp0agent.py"
set "PYEXE="

REM ---- Python fallback (used only if winget fails) ----
set "PY_WINGET_ID=Python.Python.3.14"
set "PY_VERSION=3.14.6"
set "PY_URL=https://www.python.org/ftp/python/3.14.6/python-3.14.6-amd64.exe"
set "PY_SHA256=14b3e9a710a3fcf0bd9b55ab6b60412bd91227563f813fc49040cabc0209e0bd"

echo(
echo ==== AppEase Agent Setup ====
echo Target folder : %INSTALLDIR%
echo Task name     : %TASKNAME%
echo(

REM ---------- Make sure agent.py is here ----------
if not exist "%SRCAGENT%" (
    echo ERROR: agent.py was not found next to setup.bat.
    echo Put setup.bat and agent.py in the same folder and try again.
    echo(
    pause
    exit /b 1
)

REM ---------- Step 1: detect a usable, MACHINE-WIDE Python ----------
REM  The task runs as the target user, and the admin who answers UAC may
REM  be a different account, so a Python under one user's AppData is not
REM  one the task can rely on. We only accept a Python that is
REM  machine-wide (under Program Files). A per-user Python is treated as
REM  "not usable" so we install machine-wide.
echo [1/6] Checking for an existing machine-wide Python...
call :FIND_PYTHON
if defined PYEXE (
    for /f "delims=" %%V in ('""!PYEXE!" --version" 2^>^&1') do set "PYVER=%%V"
    echo       Found machine-wide: !PYEXE!  ^(!PYVER!^)
    echo       Skipping installation.
) else (
    echo       No machine-wide Python found.
    echo       ^(Store stubs and per-user installs are intentionally ignored.^)

    REM --- Try winget first ---
    where winget >nul 2>&1
    if errorlevel 1 (
        echo       winget not available - will download from python.org instead.
    ) else (
        echo       Installing Python %PY_VERSION% via winget ^(machine-wide^)...
        winget install --id %PY_WINGET_ID% --scope machine --silent ^
            --accept-package-agreements --accept-source-agreements
        echo       Locating the installed python.exe...
        call :FIND_PYTHON_INSTALLED
        if not defined PYEXE call :FIND_PYTHON
    )

    REM --- Fallback: download the official installer from python.org ---
    if not defined PYEXE (
        echo       winget did not produce a usable Python.
        echo       Falling back to direct download from python.org...
        call :INSTALL_FROM_PYTHONORG
        call :FIND_PYTHON_INSTALLED
        if not defined PYEXE call :FIND_PYTHON
    )
)

if not defined PYEXE (
    echo(
    echo ERROR: Python could not be installed by winget OR direct download.
    echo Check this PC's internet connection, or install Python %PY_VERSION%
    echo manually with "Install for all users" + "Add to PATH", then re-run.
    echo(
    pause
    exit /b 1
)
echo       Using Python at: !PYEXE!

REM ---------- Step 2: which server? ----------
REM  agent.py itself checks and saves the address (its --*-server options),
REM  so the rules live in one tested place. Run from the SOURCE copy: the
REM  installed one may be an older agent that would start polling instead.
echo [2/6] AppEase server address...
set "DEFAULTURL="
for /f "delims=" %%U in ('""!PYEXE!" "%SRCAGENT%" --default-server "%SRCAGENT%" "%INSTALLDIR%\agent.py""') do set "DEFAULTURL=%%U"
echo       Type the IP address of the computer running the AppEase server,
echo       for example 192.168.1.10. Port 8080 is added for you.
:ASK_SERVER
set "ANSWER="
if defined DEFAULTURL (
    set /p "ANSWER=      Server address [press Enter for !DEFAULTURL!]: "
) else (
    set /p "ANSWER=      Server address: "
)
if not defined ANSWER set "ANSWER=!DEFAULTURL!"
if not defined ANSWER goto ASK_SERVER
set "SERVERURL="
for /f "delims=" %%U in ('""!PYEXE!" "%SRCAGENT%" --check-server "!ANSWER!""') do set "SERVERURL=%%U"
if not defined SERVERURL (
    echo       Please try again.
    goto ASK_SERVER
)
"!PYEXE!" "%SRCAGENT%" --probe-server "!SERVERURL!"
if errorlevel 1 (
    echo       The server did not answer. Check that it is turned on, that
    echo       AppEase is running on it, and that the address is right.
    choice /c YN /m "      Use !SERVERURL! anyway"
    if errorlevel 2 goto ASK_SERVER
)
echo       Using !SERVERURL!

REM ---------- Step 3: deploy agent.py ----------
echo [3/6] Deploying agent.py to %INSTALLDIR% ...
if not exist "%INSTALLDIR%" (
    mkdir "%INSTALLDIR%" || (
        echo ERROR: could not create %INSTALLDIR%.
        pause
        exit /b 1
    )
)
copy /Y "%SRCAGENT%" "%INSTALLDIR%\agent.py" >nul || (
    echo ERROR: could not copy agent.py into %INSTALLDIR%.
    pause
    exit /b 1
)
"!PYEXE!" "%INSTALLDIR%\agent.py" --set-server "!SERVERURL!" >nul || (
    echo ERROR: could not save the server address in %INSTALLDIR%\agent.py.
    pause
    exit /b 1
)
echo       Copied to %INSTALLDIR%\agent.py

REM ---------- Step 4: register (or overwrite) the scheduled task ----------
echo [4/6] Registering scheduled task "%TASKNAME%" ...

set "PS1=%TEMP%\appease_task_%RANDOM%.ps1"
> "%PS1%" echo $ErrorActionPreference = 'Stop'
>>"%PS1%" echo $action    = New-ScheduledTaskAction -Execute '!PYEXE!' -Argument 'agent.py' -WorkingDirectory '%INSTALLDIR%'
>>"%PS1%" echo $trigger   = New-ScheduledTaskTrigger -AtStartup
>>"%PS1%" echo $principal = New-ScheduledTaskPrincipal -UserId '%TARGETUSER%' -LogonType S4U -RunLevel Highest
>>"%PS1%" echo $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ^([TimeSpan]::Zero^) -RestartInterval ^(New-TimeSpan -Minutes 1^) -RestartCount 3
>>"%PS1%" echo Register-ScheduledTask -TaskName '%TASKNAME%' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force ^| Out-Null
>>"%PS1%" echo Write-Host '      Task registered for user %TARGETUSER% (runs at startup, whether logged on or not).'

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set "PSRESULT=%errorlevel%"
del "%PS1%" >nul 2>&1

if %PSRESULT% neq 0 (
    echo(
    echo ERROR: the scheduled task could not be registered.
    pause
    exit /b 1
)

echo [5/6] Starting the agent now ^(no reboot needed^)...
REM  End any instance from a previous install BEFORE starting the new
REM  one: the task ignores a start while one is running, so a re-run
REM  would leave the OLD agent.py in memory until reboot, with no error.
REM  Harmless on a first install; the errors are discarded either way.
schtasks /end /tn "%TASKNAME%" >nul 2>&1
REM  Ending is not instant: without a pause the task can still read as
REM  running and swallow the start below.
timeout /t 2 /nobreak >nul 2>&1
schtasks /run /tn "%TASKNAME%" >nul 2>&1

REM ---------- Step 6: install Chocolatey (only if missing) ----------
echo [6/6] Checking for Chocolatey...
where choco >nul 2>&1
if %errorlevel% equ 0 (
    echo       Chocolatey already installed - skipping.
) else (
    echo       Chocolatey not found - installing from community.chocolatey.org...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "try { Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1')); exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
    if errorlevel 1 (
        echo       WARNING: Chocolatey install failed ^(no internet, or blocked^).
        echo       The agent will still work for Local-file installers.
        echo       You can install Chocolatey manually later and re-run if needed.
    ) else (
        echo       Chocolatey installed.
    )
)

REM ---------- Which server will this machine talk to? ----------
REM  Read back from the installed file, not echoed from what was typed: a
REM  machine pointed at the wrong address looks exactly like a working one
REM  from its side, so the Done screen shows what will really be used.
REM
REM  Read from the INSTALLED copy, which the task runs. Matched by name,
REM  not line number, and the LAST match wins, as it does in Python. Not
REM  found, or PowerShell failing, leaves SERVERURL unset and says so.
set "SERVERURL="
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "$m = Select-String -LiteralPath '%INSTALLDIR%\agent.py' -CaseSensitive -Pattern '^SERVER_BASE_URL\s*=\s*[\x22'']([^\x22'']+)[\x22'']' | Select-Object -Last 1; if ($m) { $m.Matches[0].Groups[1].Value }"`) do set "SERVERURL=%%U"

echo(
echo ==== Done ====
echo Agent file : %INSTALLDIR%\agent.py
if defined SERVERURL (
    echo Server     : !SERVERURL!
) else (
    echo Server     : NOT FOUND - no SERVER_BASE_URL line could be read from
    echo              %INSTALLDIR%\agent.py
    echo              Open that file and check the address before relying on this PC.
)
echo Python     : !PYEXE!
echo Task       : %TASKNAME%  ^(user %TARGETUSER%, runs at startup, highest privileges^)
echo(
echo To check status : schtasks /query /tn "%TASKNAME%"
echo To remove       : schtasks /delete /tn "%TASKNAME%" /f
echo(
pause
exit /b 0


REM ============================================================
REM  Subroutine: download the official python.org installer,
REM  verify its SHA-256, and install it machine-wide silently.
REM  Used only when winget is unavailable or failed.
REM ============================================================
:INSTALL_FROM_PYTHONORG
REM Only 64-bit (amd64) is supported by the pinned installer.
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo       ERROR: this PC is not 64-bit x64; the pinned installer won't fit.
    echo       Install Python %PY_VERSION% manually for this architecture.
    exit /b 1
)

set "PYINSTALLER=%TEMP%\python-%PY_VERSION%-amd64.exe"

echo       Downloading Python %PY_VERSION% ^(~29 MB^) from python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PYINSTALLER%' -UseBasicParsing; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo       ERROR: download failed ^(no internet, or python.org unreachable^).
    exit /b 1
)
if not exist "%PYINSTALLER%" (
    echo       ERROR: installer did not download.
    exit /b 1
)

echo       Verifying SHA-256 checksum...
set "DLHASH="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -Path '%PYINSTALLER%').Hash"`) do set "DLHASH=%%H"
if /i not "!DLHASH!"=="%PY_SHA256%" (
    echo       ERROR: checksum mismatch - the download is corrupt or tampered.
    echo         expected: %PY_SHA256%
    echo         got     : !DLHASH!
    del "%PYINSTALLER%" >nul 2>&1
    exit /b 1
)
echo       Checksum OK.

echo       Installing Python %PY_VERSION% ^(machine-wide, silent^)...
"%PYINSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1
set "PYRC=!errorlevel!"
del "%PYINSTALLER%" >nul 2>&1
if not "!PYRC!"=="0" (
    echo       ERROR: the Python installer exited with code !PYRC!.
    exit /b 1
)
exit /b 0


REM ============================================================
REM  Subroutine: find a real, MACHINE-WIDE Python on this machine.
REM  Rejects: (a) the Microsoft Store execution-alias stub, and
REM           (b) any per-user install under a profile's AppData.
REM  Sets PYEXE only when the Python is machine-wide and exists.
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

REM Last resort: look in Program Files, for a machine install not on PATH.
call :FIND_PYTHON_INSTALLED
exit /b

:VALIDATE_CAND
REM Reject the Store stub (WindowsApps).
echo !CAND! | find /i "WindowsApps" >nul
if not errorlevel 1 exit /b
REM Reject per-user installs (under a user profile's AppData).
echo !CAND! | find /i "\AppData\" >nul
if not errorlevel 1 exit /b
REM Accept only if the file actually exists.
if exist "!CAND!" set "PYEXE=!CAND!"
exit /b


REM ============================================================
REM  Subroutine: find python.exe right after a machine-wide
REM  winget install, before PATH is refreshed in this session.
REM ============================================================
:FIND_PYTHON_INSTALLED
set "PYEXE="
for /d %%D in ("%ProgramFiles%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE if defined ProgramFiles(x86) (
    for /d %%D in ("%ProgramFiles(x86)%\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)
exit /b
