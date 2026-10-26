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


rem ---- 1. Use only the bundled Python shipped with this project ----
set "PYTHON=%~dp0ballontrans_pylibs_win\python.exe"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONNOUSERSITE=1"
if not exist "%PYTHON%" (
    echo [ERROR] Missing ballontrans_pylibs_win\python.exe.
    echo [ERROR] Extract the matching complete package beside launch.bat.
    pause
    exit /b 1
)
"%PYTHON%" -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Bundled Python cannot start or is too old.
    echo [ERROR] Re-extract the matching complete package and check the VC++ runtime.
    pause
    exit /b 1
)
echo [OK] Using bundled Python: %PYTHON%

rem ---- 2. Detect Git vs ZIP distribution mode ----
if exist ".git" (
    set "BTRANSLATOR_UPDATE_MODE=git"
) else (
    set "BTRANSLATOR_UPDATE_MODE=zip"
)


rem ---- 3. ZIP mode: apply pending updates ----
if "%BTRANSLATOR_UPDATE_MODE%"=="zip" (
    if exist "_update\ready" (
        "%PYTHON%" scripts\check_update.py --validate-pending
        if errorlevel 1 (
            echo [ERROR] Update does not match this environment. Get the matching complete package.
            exit /b 1
        )
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


rem ---- 5. GPU hardware detection (skip if --cpu is set) ----
rem     A listed ONNX Runtime provider does not prove its CUDA DLLs can run.
if defined CPU_FLAG goto :skip_gpu_check

set "GPU_DETECTED="

where nvidia-smi >nul 2>nul
if !ERRORLEVEL! == 0 (
    nvidia-smi >nul 2>nul
    if !ERRORLEVEL! == 0 (
        set "GPU_DETECTED=1"
        echo [INFO] NVIDIA GPU detected via nvidia-smi
    )
)

if defined GPU_DETECTED (
    set "BTRANSLATOR_GPU_MODE=1"
    echo [INFO] GPU hardware detected; launch.py will check the bundled runtime.
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
