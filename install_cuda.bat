@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

:: ============================================
::  Install CUDA PyTorch + ONNX Runtime GPU
::  into Bundled Python
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
echo  Installing CUDA PyTorch + ONNX Runtime GPU
echo ============================================
echo.
echo  This will download:
echo    PyTorch CUDA   ~2 GB
echo    onnxruntime-gpu ~214 MB
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
"%PYTHON_DIR%\python.exe" -c "import subprocess as s, sys; import os; p=os.popen('nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>nul'); cc=p.read().strip(); p.close(); print(cc.split('.')[0] if cc else '0')" > "%TEMP%\gpu_cc.txt" 2>&1
set /p GPU_CC=<"%TEMP%\gpu_cc.txt" 2>nul
if not defined GPU_CC set GPU_CC=0

:: -- Step 2: Read installed torch version (for display only) --
"%PYTHON_DIR%\python.exe" -c "import torch; print(torch.__version__)" > "%TEMP%\torch_ver.txt" 2>nul
set /p TORCH_VER=<"%TEMP%\torch_ver.txt" 2>nul

:: -- Step 2b: Check installed onnxruntime status --
"%PYTHON_DIR%\python.exe" -c "import onnxruntime; print('GOOD' if 'CUDAExecutionProvider' in onnxruntime.get_available_providers() else 'CPU')" > "%TEMP%\ort_status.txt" 2>nul
if not exist "%TEMP%\ort_status.txt" echo MISSING > "%TEMP%\ort_status.txt"
set /p ORT_STATUS=<"%TEMP%\ort_status.txt" 2>nul

:: -- Step 3: Map compute capability to CUDA version index --
:: Only GPUs with CC >= 6 (Pascal and later) are supported.
:: Each major CC generation maps to one CUDA toolkit - no model names needed.
:: onnxruntime-gpu: 1.19+ defaults to CUDA 12.x; <1.19 defaults to CUDA 11.x.
:: CUDA 13.x drivers are backward-compatible with CUDA 12.x binaries.
:: CUDA 13.x nightly builds exist but are unstable, so we use the CUDA 12.x
:: stable build for CC >= 9 (the driver handles compatibility).
:: The installed torch version (cuXXX) determines the PyTorch index URL.
if %GPU_CC% GEQ 10 (
    set CUDA_INDEX=cu132
    set CUDA_LABEL=CUDA 13.2
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if %GPU_CC% GEQ 9 (
    set CUDA_INDEX=cu130
    set CUDA_LABEL=CUDA 13.0
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if %GPU_CC% GEQ 8 (
    set CUDA_INDEX=cu126
    set CUDA_LABEL=CUDA 12.6
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if %GPU_CC% GEQ 7 (
    set CUDA_INDEX=cu124
    set CUDA_LABEL=CUDA 12.4
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if %GPU_CC% GEQ 6 (
    set CUDA_INDEX=cu118
    set CUDA_LABEL=CUDA 11.8
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.17,<1.19"
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
echo  ONNX Runtime status: !ORT_STATUS!
echo  Selected CUDA: %CUDA_LABEL% ^(%CUDA_INDEX%^)
echo.
echo  ── Planned actions ──────────────────────────
if /i "!TORCH_VER:cu=!"=="!TORCH_VER!" (
    echo  [ ] Install CUDA PyTorch ^(~2 GB^)
) else (
    echo  [SKIP] CUDA PyTorch already installed ^(!TORCH_VER!^)
)
if /i "!ORT_STATUS!"=="GOOD" (
    echo  [SKIP] onnxruntime-gpu already has CUDA support
) else if /i "!ORT_STATUS!"=="MISSING" (
    echo  [ ] Install onnxruntime-gpu ^(~214 MB^) — replacing missing onnxruntime
) else (
    echo  [ ] Upgrade onnxruntime to onnxruntime-gpu ^(~214 MB^)
)
echo  ─────────────────────────────────────────────

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
    echo  Manual mode - copy the commands below and
    echo  run them in your target Python environment:
    echo ============================================
    echo.
    echo  1. Install CUDA PyTorch:
    echo     !PIP_CMD!
    echo.
    echo  2. Install onnxruntime-gpu ^(replace CPU onnxruntime^):
    if /i "!ORT_STATUS!"=="MISSING" (
    echo     pip install "!ONNX_RT_SPEC!"
    ) else (
    echo     pip uninstall onnxruntime -y
    echo     pip install "!ONNX_RT_SPEC!"
    )
    echo.
    pause
    exit /b 0
)

:: -- Step 6: Install PyTorch (replace mode) --
:: Check if CUDA PyTorch is already installed (version string contains +cu).
set TORCH_HAS_CUDA=0
if defined TORCH_VER (
    echo !TORCH_VER! | findstr /C:"+cu" >nul && set TORCH_HAS_CUDA=1
)

if !TORCH_HAS_CUDA! EQU 1 (
    echo  [SKIP] CUDA PyTorch already installed ^(!TORCH_VER!^)
) else (
    echo  Installing CUDA PyTorch into %PYTHON_DIR% ...
    echo.
    !PIP_CMD!

    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] PyTorch installation failed.
        echo  Check your internet connection and try again.
        echo  If issues persist, visit https://pytorch.org for manual install instructions.
        pause
        exit /b 1
    )
)

:: -- Step 7: Install onnxruntime-gpu (replace mode) --
:: onnxruntime (CPU) and onnxruntime-gpu are separate packages and conflict.
:: We must uninstall the CPU version first, then install the GPU version.
if /i "!ORT_STATUS!"=="GOOD" (
    echo  [SKIP] onnxruntime-gpu already has CUDA support.
) else (
    echo  Installing onnxruntime-gpu into %PYTHON_DIR% ...
    echo  Package: !ONNX_RT_SPEC!
    echo  This will download ~214 MB.
    echo.

    :: Uninstall CPU onnxruntime first (may already be missing)
    "%PYTHON_DIR%\python.exe" -m pip uninstall onnxruntime -y >nul 2>&1

    :: Install onnxruntime-gpu
    "%PYTHON_DIR%\python.exe" -m pip install "!ONNX_RT_SPEC!" --prefer-binary

    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] onnxruntime-gpu installation failed.
        echo  Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo.
echo ============================================
echo  CUDA environment setup complete!
echo.
echo  What was done:
if %TORCH_HAS_CUDA% EQU 1 (
echo    PyTorch CUDA — already installed ^(skipped^)
) else (
echo    PyTorch CUDA — installed ^(replaced CPU version^)
)
if /i "!ORT_STATUS!"=="GOOD" (
echo    onnxruntime-gpu — already has CUDA support ^(skipped^)
) else (
echo    onnxruntime-gpu — installed ^(replaced CPU onnxruntime^)
)
echo.
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
