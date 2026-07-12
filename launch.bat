@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

:: ============================================
::  BallonsTranslator-lite Launcher
:: ============================================
:: Auto-detection: Python, GPU/CPU, Git/ZIP update mode
:: Usage: launch.bat [--cpu] [--update|--check-update] [launch.py args...]
:: ============================================

rem ---- 0. Detect known flags (%* is NOT affected by shift) ----
set "CPU_FLAG="
set "UPDATE_FLAG="
set "CHECK_UPDATE_FLAG="

:parse_args
if "%~1"=="" goto :parse_done
if /i "%~1"=="--cpu" set "CPU_FLAG=1"
if /i "%~1"=="--update" set "UPDATE_FLAG=1"
if /i "%~1"=="--check-update" set "CHECK_UPDATE_FLAG=1"
shift
goto :parse_args
:parse_done


rem ---- 1. Python discovery (embedded -> py launcher -> python3 -> python PATH) ----

rem Option A: bundled/embedded Python (full release package)
set "PYTHON=%~dp0ballontrans_pylibs_win\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" -c "" >nul 2>&1
    if !ERRORLEVEL! == 0 (
        echo [OK] Using embedded Python: %PYTHON%
        goto :python_found
    ) else (
        echo [WARN] Found embedded Python at %PYTHON%
        echo [WARN]   but it failed to run (exit code: !ERRORLEVEL!^).
        echo [WARN]   Common causes:
        echo [WARN]     - Missing VC++ Redistributable 2015-2022
        echo [WARN]       Download: https://aka.ms/vs/17/release/vc_redist.x64.exe
        echo [WARN]     - Windows blocked the .exe file
        echo [WARN]       Right-click python.exe ^> Properties ^> "Unblock"
        echo [WARN]     - Corrupted download or wrong architecture (32-bit vs 64-bit)
        echo.
    )
) else (
    echo [WARN] Embedded Python not found at: %PYTHON%
    echo [WARN]   Expected directory structure:
    echo [WARN]     BallonsTranslator-lite\
    echo [WARN]     ├── launch.bat
    echo [WARN]     ├── launch.py
    echo [WARN]     └── ballontrans_pylibs_win\
    echo [WARN]             └── python.exe
    echo [WARN]   If you downloaded the dependency package, make sure
    echo [WARN]   the folder is named exactly "ballontrans_pylibs_win"
    echo [WARN]   and placed in the project root directory.
    echo.
)

rem Option B: Python launcher (most reliable for officially-installed Python)
rem     The `py` launcher ships with the official Python installer and will
rem     find any installed Python even when it's not on PATH.
where py >nul 2>nul
if %ERRORLEVEL% == 0 (
    py -3 -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if !ERRORLEVEL! == 0 (
        for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
        if defined PYTHON (
            for /f "delims=" %%V in ('py -3 -c "import sys; print('.'.join(map(str, sys.version_info[:2])))" 2^>nul') do set "PYTHON_VER=%%V"
            echo [OK] Using Python !PYTHON_VER! via py launcher: !PYTHON!
            goto :python_found
        )
    )
)

rem Option C: python3 (Microsoft Store Python installs this in WindowsApps)
rem     Works when the Store Python stub is present but `py` launcher isn't.
where python3 >nul 2>nul
if %ERRORLEVEL% == 0 (
    python3 -c "" >nul 2>nul
    if !ERRORLEVEL! == 0 (
        for /f "delims=" %%P in ('python3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
        echo [INFO] Using Python 3 via python3: !PYTHON!
        goto :python_found
    )
)

rem Option D: system PATH (works if Python is in %PATH%)
rem     Also handle the WindowsApps stub: it passes `-c ""` but fails on real imports.
rem     We verify by checking that python can actually import a module.
where python >nul 2>nul
if %ERRORLEVEL% == 0 (
    python -c "import sys; exit(0)" >nul 2>nul
    if !ERRORLEVEL! == 0 (
        set "PYTHON=python"
        echo [INFO] Using system Python
        goto :python_found
    ) else (
        echo [WARN] `python` found on PATH but failed to run ^(WindowsApps stub?^)
        echo [WARN] Skipping — will try alternative Python discovery.
    )
)

rem No Python found at all
echo [ERROR] ============================================================
echo [ERROR]  Python could not be found or started.
echo [ERROR] ============================================================
echo.
echo    Locations checked:
echo      1. ballontrans_pylibs_win\python.exe (embedded/distributed)
echo      2. py launcher ^(requires Python 3.10+^)
echo      3. python3 on PATH
echo      4. python on PATH
echo.
echo    Common causes:
echo      - Missing VC++ Redistributable 2015-2022 (needed by embedded Python)
echo        Download: https://aka.ms/vs/17/release/vc_redist.x64.exe
echo.
echo      - ballontrans_pylibs_win not in the right location or wrong name
echo        Expected:
echo          BallonsTranslator-lite\ballontrans_pylibs_win\python.exe
echo.
echo      - Windows blocked the downloaded .exe file (mark-of-the-web)
echo        Right-click ballontrans_pylibs_win\python.exe ^> Properties
echo        ^> Check "Unblock" if present, then Apply.
echo.
echo      - Python 3.10+ not installed (if not using embedded Python)
echo        Download from: https://python.org
echo.
pause
exit /b 1

:python_found


rem ---- 2. Detect Git vs ZIP distribution mode ----
if exist ".git" (
    set "BTRANSLATOR_UPDATE_MODE=git"
) else (
    set "BTRANSLATOR_UPDATE_MODE=zip"
)


rem ---- 3. ZIP mode: apply pending updates ----
if "%BTRANSLATOR_UPDATE_MODE%"=="zip" (
    if exist "_update\ready" (
        echo.
        echo ============================================
        echo  Applying update ...
        echo ============================================

        rem Delete obsolete files listed in delta manifest
        if exist "_update\deleted.txt" (
            echo  Removing obsolete files...
            for /f "usebackq tokens=*" %%f in ("_update\deleted.txt") do (
                if exist "%%f" del /f /q "%%f"
            )
        )

        rem Copy update files (delta format: _update/files/ ; full ZIP: _update/)
        if exist "_update\files" (
            xcopy /E /Y "_update\files\*" "." >nul 2>&1
        ) else (
            xcopy /E /Y "_update\*" "." >nul 2>&1
        )

        if !ERRORLEVEL! == 0 (
            rem Copy new manifest if present
            if exist "_update\manifest.json" (
                copy /Y "_update\manifest.json" "manifest.json" >nul 2>&1
            )
            rmdir /S /Q "_update"
            echo  Update applied successfully.
        ) else (
            echo  WARNING: Update failed. Launching with current version.
        )
        echo.
    )
)


rem ---- 4. --update / --check-update handling ----
rem     Git mode -> launch.py --update (git pull)
rem     ZIP mode -> check_update.py (direct download)
if defined UPDATE_FLAG (
    if "%BTRANSLATOR_UPDATE_MODE%"=="git" (
        echo [INFO] Git mode: updating via git pull...
        "%PYTHON%" launch.py --update %*
        exit /b !ERRORLEVEL!
    ) else (
        echo [INFO] ZIP mode: checking for updates...
        "%PYTHON%" scripts\check_update.py
        if exist "_update\ready" (
            echo.
            echo  Update downloaded. Restart to apply.
        )
        exit /b 0
    )
)
if defined CHECK_UPDATE_FLAG (
    "%PYTHON%" scripts\check_update.py
    exit /b 0
)


rem ---- 5. GPU auto-detection (skip if --cpu is set) ----
rem     Checks onnxruntime CUDA provider first, then falls back to
rem     nvidia-smi (hardware check).  If either succeeds, set
rem     BTRANSLATOR_GPU_MODE so launch.py knows an NVIDIA GPU is
rem     available.  launch.py decides whether the current Python
rem     environment can use it (no cross-Python ABI injection).
if defined CPU_FLAG goto :skip_gpu_check

set "GPU_DETECTED="

rem Check A: onnxruntime has CUDA provider (fastest, most reliable)
"%PYTHON%" -c "import onnxruntime; exit(0 if 'CUDA' in onnxruntime.get_available_providers() else 1)" >nul 2>nul
if !ERRORLEVEL! == 0 set "GPU_DETECTED=1"

rem Check B: nvidia-smi reports a working GPU (fallback for bundled Python
rem         where onnxruntime-gpu hasn't been installed yet)
if not defined GPU_DETECTED (
    where nvidia-smi >nul 2>nul
    if !ERRORLEVEL! == 0 (
        nvidia-smi >nul 2>nul
        if !ERRORLEVEL! == 0 (
            set "GPU_DETECTED=1"
            echo [INFO] GPU detected via nvidia-smi ^(onnxruntime CUDA provider not yet available^)
        )
    )
)

if defined GPU_DETECTED (
    set "BTRANSLATOR_GPU_MODE=1"
    echo [OK] GPU mode ^(CUDA^)
) else (
    echo [INFO] GPU not available, using CPU mode
)
:skip_gpu_check

rem ---- 6. Launch ----
echo.
"%PYTHON%" launch.py %*
set "LAUNCH_EXIT=!ERRORLEVEL!"
pause
exit /b !LAUNCH_EXIT!
