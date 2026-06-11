@echo off
cd /d "%~dp0"

:: ============================================
::  BallonsTranslator-lite Launcher (GPU mode)
:: ============================================
:: - Uses embedded Python if available
:: - Falls back to system Python otherwise
:: - Detects GPU acceleration automatically
:: ============================================

set "PYTHON=%~dp0ballontrans_pylibs_win\python.exe"
set "BTRANSLATOR_GPU_MODE=1"

:: Try embedded Python first (full package users)
"%PYTHON%" -c "" >nul 2>nul
if %ERRORLEVEL% == 0 goto :launch

:: Fall back to system Python (lightweight/GitHub users)
where python >nul 2>nul
if %ERRORLEVEL% == 0 (
    set "PYTHON=python"
    echo [INFO] Using system Python (requires PyTorch with CUDA for GPU)
    goto :launch
)

:: No Python found at all
echo [ERROR] Python not found.
echo.
echo Please download the full package from the link in README.md,
echo or install Python 3.10+ from https://python.org
pause
exit /b 1

:launch
"%PYTHON%" launch.py %*
pause
