@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

:: ============================================
::  Install CUDA PyTorch into Bundled Python
:: ============================================
::
:: -- User Config ------------------------------
::
:: Install mode:
::   "replace" - overwrite CPU torch in ballontrans_pylibs_win (default)
::   "manual"  - only print the pip command, you run it manually
::
:: For environment coexistence: set mode to "manual", copy the printed
:: command, and run it against your target Python environment.
::
set INSTALL_MODE=replace
::
:: ============================================

echo ============================================
echo  Installing CUDA PyTorch
echo ============================================
echo.
echo  This will download ~2 GB of CUDA libraries.
echo  Make sure you have a stable internet connection.
echo.

:: -- Verify environment --
set PYTHON_DIR=ballontrans_pylibs_win
if not exist "%PYTHON_DIR%\python.exe" (
    echo [ERROR] Embedded Python not found at %PYTHON_DIR%.
    echo  This script must be run from the one-click bundle directory
    echo  containing %PYTHON_DIR%\.
    pause
    exit /b 1
)

:: -- Step 1: Detect GPU compute capability --
:: Uses compute_cap (architectural property) instead of GPU model names,
:: so the mapping works across generations without hardcoding model lists.
echo  Detecting GPU architecture...
"%PYTHON_DIR%\python.exe" -c "import subprocess as s, sys; r=s.run(['nvidia-smi','--query-gpu=compute_cap','--format=csv,noheader'],capture_output=True,text=True,timeout=10); cc=r.stdout.strip(); print(cc.split('.')[0] if r.returncode==0 and cc else '0')" > "%TEMP%\gpu_cc.txt" 2>&1
set /p GPU_CC=<"%TEMP%\gpu_cc.txt" 2>nul
if not defined GPU_CC set GPU_CC=0

:: -- Step 2: Read installed torch version (for display only) --
"%PYTHON_DIR%\python.exe" -c "import torch; print(torch.__version__)" > "%TEMP%\torch_ver.txt" 2>nul
set /p TORCH_VER=<"%TEMP%\torch_ver.txt" 2>nul

:: -- Step 3: Map compute capability to CUDA version index --
:: Only GPUs with CC >= 6 (Pascal and later) are supported.
:: Each major CC generation maps to one CUDA toolkit - no model names needed.
if %GPU_CC% GEQ 10 (
    set CUDA_INDEX=cu132
    set CUDA_LABEL=CUDA 13.2
) else if %GPU_CC% GEQ 9 (
    set CUDA_INDEX=cu130
    set CUDA_LABEL=CUDA 13.0
) else if %GPU_CC% GEQ 8 (
    set CUDA_INDEX=cu126
    set CUDA_LABEL=CUDA 12.6
) else if %GPU_CC% GEQ 7 (
    set CUDA_INDEX=cu124
    set CUDA_LABEL=CUDA 12.4
) else if %GPU_CC% GEQ 6 (
    set CUDA_INDEX=cu118
    set CUDA_LABEL=CUDA 11.8
) else (
    set CUDA_INDEX=
)

:: -- Step 4: Report --
echo.
if not defined CUDA_INDEX (
    echo  GPU Compute Capability: %GPU_CC%.x
    echo  [INFO] No supported NVIDIA GPU detected ^(need CC >= 6^).
    echo  The CPU-only PyTorch will be kept.
    echo  Check your drivers if you believe a GPU is present.
    pause
    exit /b 0
)

echo  GPU Compute Capability: %GPU_CC%.x
if defined TORCH_VER (
    echo  PyTorch installed: !TORCH_VER!
) else (
    echo  PyTorch version detection: failed ^(will install latest^)
)
echo  Selected: %CUDA_LABEL% ^(%CUDA_INDEX%^)
echo.

:: -- Step 5: Build install command --
:: Uses -U (upgrade) so pip picks the CUDA variant over the installed CPU one.
:: Version is not pinned - the index only serves matching CUDA builds,
:: and pip handles the +cpu -> +cuXX upgrade via PEP 440 local version comparison.
::
:: torchaudio is deliberately excluded: newer CUDA indexes (cu132) lack it,
:: and older ones (cu126/cu130) trail the current torch version.  This app
:: does not use audio I/O, so torchaudio is unnecessary.
set "PIP_CMD=%PYTHON_DIR%\python.exe -m pip install -U torch torchvision --index-url https://download.pytorch.org/whl/%CUDA_INDEX% --prefer-binary"

if /i "!INSTALL_MODE!"=="manual" (
    echo ============================================
    echo  Manual mode - copy the command below and
    echo  run it in your target Python environment:
    echo ============================================
    echo.
    echo !PIP_CMD!
    echo.
    pause
    exit /b 0
)

:: -- Step 6: Install (replace mode) --
echo  Installing CUDA PyTorch into %PYTHON_DIR% ...
echo.
!PIP_CMD!

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
echo  Launch the app normally via launch.bat -
echo  GPU mode will be detected automatically.
echo.
echo  To force CPU mode at any time, set:
echo    set BALLOONTRANS_CPU_ONLY=1
echo.
echo  [Note] pip 警告 ultralytics 缺少 polars 属于误报。
echo   polars 仅模型训练需要，用户端推理不依赖它。
echo ============================================
pause
