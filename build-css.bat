@echo off
setlocal
REM ===================================================================
REM  AppEase - rebuild the Tailwind stylesheet
REM ===================================================================
REM  Requires tailwindcss.exe, TAILWIND v4.x, at the project root
REM  (the folder containing server\ and agent\). No Node, no npm.
REM
REM  tailwind-input.css holds every token and @source glob, so there is
REM  no -c config flag.
REM
REM  The window ALWAYS pauses before closing: a double-clicked .bat closes
REM  the instant it exits, which would hide any error.
REM ===================================================================

echo Checking tailwindcss.exe version...
tailwindcss.exe --help
echo.

REM --- guard 1: reject anything that isn't Tailwind v4 ---------------
REM  tailwind-input.css uses v4-only syntax (@import "tailwindcss",
REM  @theme, @source). A v3.x binary does not understand these directives
REM  and will not produce a usable stylesheet from this file.
tailwindcss.exe --help 2>&1 | findstr /C:"v4." >nul
if errorlevel 1 (
    echo.
    echo ABORTED - tailwindcss.exe does not report itself as v4.x.
    echo.
    echo   tailwind-input.css now uses v4-only CSS syntax ^(@theme,
    echo   @source^) that a v3.x binary cannot read.
    echo.
    echo   The full --help output is printed above this line - if it DOES
    echo   look like a v4 binary to you but this still aborted, the text
    echo   match here is wrong for your exact build; copy what printed
    echo   above and it can be fixed.
    echo.
    echo   Otherwise, download tailwindcss-windows-x64.exe from the
    echo   v4.3.3 release ^(or later 4.x^):
    echo   https://github.com/tailwindlabs/tailwindcss/releases
    pause
    exit /b 1
)

REM --- build ---------------------------------------------------------
echo Building...
tailwindcss.exe -i tailwind-input.css -o server\static\css\tailwind.css --minify
if errorlevel 1 (
    echo.
    echo BUILD FAILED - stylesheet was NOT regenerated. See the error
    echo printed above - it usually names the exact file and line.
    pause
    exit /b 1
)

REM --- guard 2: sanity-check the output size -------------------------
REM  A build that scanned no source files still "succeeds" but comes out
REM  far smaller. A healthy one, Basecoat included, is about 240 KB, well
REM  clear of this floor.
for %%A in (server\static\css\tailwind.css) do set SIZE=%%~zA
if %SIZE% LSS 15000 (
    echo.
    echo WARNING - output is only %SIZE% bytes; a healthy v4 build is about 240000.
    echo   This usually means no source files were scanned.
    echo   Are you running this from the project root?
    pause
    exit /b 1
)

echo.
echo OK - built server\static\css\tailwind.css  (%SIZE% bytes)
pause