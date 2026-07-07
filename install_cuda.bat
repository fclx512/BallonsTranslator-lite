@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

:: ============================================
::  Install CUDA PyTorch into Bundled Python
::  Run this once after extracting the one-click
::  bundle to enable GPU acceleration.
:: ============================================

echo ============================================
echo  Installing CUDA PyTorch (2.7.1)
echo ============================================
echo.
echo  This will download ~2 GB of CUDA libraries.
echo  Make sure you have a stable internet connection.
echo.

:: Verify embedded Python exists
if not exist "ballontrans_pylibs_win\python.exe" (
    echo [ERROR] Embedded Python not found.
    echo  This script must be run from the one-click bundle directory
    echo  containing ballontrans_pylibs_win\.
    pause
    exit /b 1
)

echo  Detecting GPU architecture...
ballontrans_pylibs_win\python.exe -c "
import subprocess, sys
try:
    r = subprocess.run(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],
        capture_output=True, text=True, timeout=10)
    if r.returncode == 0 and r.stdout.strip():
        print('  GPU: ' + r.stdout.strip())
    else:
        print('  No NVIDIA GPU detected (CUDA may still work if torch finds one)')
except Exception:
    print('  nvidia-smi not available')
"

echo.
echo  Installing torch, torchvision, torchaudio with CUDA 12.4 support...
echo.
ballontrans_pylibs_win\python.exe -m pip install ^
    torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 ^
    --index-url https://download.pytorch.org/whl/cu124 ^
    --prefer-binary

if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] Installation failed.
    echo  Check your internet connection and try again.
    echo  If issues persist, visit https://pytorch.org for manual install instructions.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  CUDA PyTorch installed successfully!
echo  Launch the app normally via launch.bat —
echo  GPU mode will be detected automatically.
echo ============================================
pause
