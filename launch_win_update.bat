@echo off
cd %~dp0

:: GPU mode with auto-update check
:: Checks for and applies code updates before launching.
:: If you prefer to skip update checks, use launch_win.bat instead.

set PYTHON=%~dp0ballontrans_pylibs_win\python.exe
set BTRANSLATOR_GPU_MODE=1
set ERROR_REPORTING=FALSE

mkdir tmp 2>NUL

:: ── Phase 1: Apply previously downloaded update ──
if exist "_update\ready" (
    echo ============================================
    echo An update is ready to install.
    echo.
    echo If you are in the middle of work and prefer not to
    echo update right now, close this window and use launch_win.bat
    echo to skip the update for this session.
    echo ============================================
    choice /c YN /t 10 /d Y /m "Apply update now"
    if errorlevel 2 goto :skip_update

    echo Applying update...
    xcopy /E /Y "_update\*" "." >nul 2>&1
    if %ERRORLEVEL% == 0 (
        rmdir /S /Q "_update"
        echo Update applied. Restarting with new version...
        echo ============================================
        echo.
        :: Re-launch to pick up updated scripts
        goto :check_python
    ) else (
        echo WARNING: Update failed. Launching with current version.
        echo ============================================
        echo.
    )
    goto :check_python

    :skip_update
    echo Update skipped. Will be offered again next launch.
    echo ============================================
    echo.
)

:: ── Phase 2: Verify embedded Python ──
:check_python
%PYTHON% -c "" >tmp/stdout.txt 2>tmp/stderr.txt
if %ERRORLEVEL% == 0 goto :check_update
echo Error: Embedded Python not found. The portable environment may be corrupted.
goto :show_stdout_stderr

:: ── Phase 3: Check for new updates (download only, does not apply) ──
:check_update
%PYTHON% scripts\check_update.py
echo.

:: ── Phase 4: Launch ──
:launch
%PYTHON% launch.py %*
pause
exit /b


:: ── Error display ──
:show_stdout_stderr
echo.
echo exit code: %errorlevel%

for /f %%i in ("tmp\stdout.txt") do set size=%%~zi
if %size% equ 0 goto :show_stderr
echo.
echo stdout:
type tmp\stdout.txt

:show_stderr
for /f %%i in ("tmp\stderr.txt") do set size=%%~zi
if %size% equ 0 goto :endofscript
echo.
echo stderr:
type tmp\stderr.txt

:endofscript
echo.
echo Launch unsuccessful. Exiting.
pause
