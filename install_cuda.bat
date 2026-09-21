@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

:: ============================================
::  Install CUDA PyTorch + ONNX Runtime GPU
:: ============================================
::
:: Two audiences, both handled by this one script:
::   1. One-click bundle  -> installs into the embedded Python
::                           (ballontrans_pylibs_win\) automatically.
::   2. Source / own venv -> prints the pip commands to run yourself.
::
:: Usage:
::   install_cuda.bat            auto: install into bundle if present,
::                               otherwise print commands for your Python
::   install_cuda.bat --manual   always just print the commands
::   install_cuda.bat --replace  always install into the bundled Python
::
:: The bundled directory name is discovered from the script's own
:: location rather than hardcoded, so a renamed bundle folder still works.
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

:: -- User Config ------------------------------
:: Install mode:
::   "auto"    - install into the bundled Python when found, otherwise
::               fall back to printing the commands (default)
::   "replace" - always install into the bundled Python
::   "manual"  - only print the pip commands, you run them yourself
::
:: Command line wins over this setting: --manual / --replace
set INSTALL_MODE=auto
:: ============================================

if /i "%~1"=="--manual" set INSTALL_MODE=manual
if /i "%~1"=="manual" set INSTALL_MODE=manual
if /i "%~1"=="--replace" set INSTALL_MODE=replace
if /i "%~1"=="replace" set INSTALL_MODE=replace
if /i "%~1"=="--help" goto :usage

:: -- Locate the bundled Python (do NOT hardcode the folder name) --
:: Search the script directory, then one level up, so both a bundle root
:: and a source checkout nested inside a bundle are covered.
set "PYTHON_EXE="
for %%D in ("%~dp0." "%~dp0..") do (
    if not defined PYTHON_EXE (
        for /d %%P in ("%%~fD\ballontrans_pylibs_win*") do (
            if not defined PYTHON_EXE (
                if exist "%%~fP\python.exe" set "PYTHON_EXE=%%~fP\python.exe"
            )
        )
    )
)

:: Remember the previous torch build so we can warn about a downgrade.
::
:: NOTE the `call` inside the backticks.  Without it, cmd parses the quoted
:: interpreter path as a single command *name*; when that path contains a
:: space (e.g. the bundle was extracted to "D:\My Tools\"), the command is
:: not found, `%%V` stays empty, and the failure is completely silent.
:: Every `for /f ... in (`"..."`)` below uses `call` for this reason.
set "OLD_TORCH="
if defined PYTHON_EXE (
    for /f "usebackq delims=" %%V in (`call "%PYTHON_EXE%" -c "import torch;print(torch.__version__)" 2^>nul`) do set "OLD_TORCH=%%V"
)


:: ------------------------------------------------------------------
:: Decide whether we can install automatically
:: ------------------------------------------------------------------
set "CAN_AUTO=0"
if /i "!INSTALL_MODE!"=="manual" set "CAN_AUTO=0"
if /i "!INSTALL_MODE!"=="replace" if defined PYTHON_EXE set "CAN_AUTO=1"
if /i "!INSTALL_MODE!"=="auto" if defined PYTHON_EXE set "CAN_AUTO=1"

if "!CAN_AUTO!"=="0" goto :manual_mode

:: ------------------------------------------------------------------
:: Auto / replace mode - we have a bundled Python
:: ------------------------------------------------------------------
echo  Python environment: !PYTHON_EXE!
echo.

:: -- Step 1: Detect GPU compute capability --
:: Uses compute_cap (architectural property) instead of GPU model names,
:: so the mapping works across generations without hardcoding model lists.
echo  Detecting GPU architecture...
set "GPU_CC=0"
for /f "usebackq delims=" %%C in (`call "%PYTHON_EXE%" -c "import os;p=os.popen('nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>nul');cc=p.read().strip();cc=cc.splitlines()[0] if cc else '';p.close();print(cc.split('.')[0] if cc else '')" 2^>nul`) do set "GPU_CC=%%C"
if not defined GPU_CC set GPU_CC=0
:: Accept an optional decimal part as well: some drivers return "12.0" even
:: though we only ask for the major.  Rejecting it here used to silently
:: downgrade the machine to "no GPU detected".
echo !GPU_CC!| findstr /R "^[0-9][0-9]*[0-9.]*$" >nul || set GPU_CC=0
:: Normalise "12.0" -> "12" so the numeric comparisons below are reliable.
for /f "delims=. tokens=1" %%M in ("!GPU_CC!") do set "GPU_CC=%%M"

:: -- Step 2: Read installed torch version --
set "TORCH_VER="
for /f "usebackq delims=" %%V in (`call "%PYTHON_EXE%" -c "import torch;print(torch.__version__)" 2^>nul`) do set "TORCH_VER=%%V"

:: -- Step 2b: Check installed onnxruntime status --
:: NOTE: pre-seed the sentinel with MISSING, then let python overwrite it.
:: An `if not exist` fallback cannot work here: the `>` redirection
:: creates the file during parsing, so it always exists (possibly zero
:: bytes) and `set /p` on an empty file leaves the variable *undefined*
:: - which would match neither GOOD nor MISSING and send us down the
:: wrong branch, installing onnxruntime-gpu when it is only missing.
(echo MISSING)> "%TEMP%\bt_ort_status.txt"
"%PYTHON_EXE%" -c "import onnxruntime; print('GOOD' if 'CUDAExecutionProvider' in onnxruntime.get_available_providers() else 'CPU')" > "%TEMP%\bt_ort_status.txt" 2>nul
set "ORT_STATUS="
set /p ORT_STATUS=<"%TEMP%\bt_ort_status.txt"
if not defined ORT_STATUS set ORT_STATUS=MISSING

:: -- Step 3: Map compute capability to CUDA version index --
:: Only GPUs with CC >= 6 (Pascal and later) are supported.
:: IMPORTANT: cu124 is EOL - the last torch it serves is 2.6.0, while the
:: project ships 2.13.x.  Using it silently downgrades torch by several
:: minor versions.  Everything below CC 9 therefore maps to cu126, which
:: carries the same 2.x line as the newer indexes.
:: onnxruntime-gpu: 1.19+ defaults to CUDA 12.x; <1.19 defaults to CUDA 11.x.
if !GPU_CC! GEQ 10 (
    set CUDA_INDEX=cu132
    set CUDA_LABEL=CUDA 13.2
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if !GPU_CC! GEQ 9 (
    set CUDA_INDEX=cu130
    set CUDA_LABEL=CUDA 13.0
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if !GPU_CC! GEQ 6 (
    set CUDA_INDEX=cu126
    set CUDA_LABEL=CUDA 12.6
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else (
    set CUDA_INDEX=
)

:: -- Step 4: Report --
echo.
if not defined CUDA_INDEX (
    echo  GPU Compute Capability: !GPU_CC!.x
    echo  [INFO] No supported NVIDIA GPU detected ^(need CC ^>= 6^).
    echo  The CPU-only PyTorch will be kept.
    echo  Check your drivers if you believe a GPU is present.
    pause
    exit /b 0
)

echo  GPU Compute Capability: !GPU_CC!.x
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
set "PIP_CMD="%PYTHON_EXE%" -m pip install -U torch torchvision --index-url https://download.pytorch.org/whl/%CUDA_INDEX% --prefer-binary"

:: -- Step 6: Install PyTorch (replace mode) --
:: Check if CUDA PyTorch is already installed (version string contains +cu).
set TORCH_HAS_CUDA=0
if defined TORCH_VER (
    echo !TORCH_VER! | findstr /C:"+cu" >nul && set TORCH_HAS_CUDA=1
)

if !TORCH_HAS_CUDA! EQU 1 (
    echo  [SKIP] CUDA PyTorch already installed ^(!TORCH_VER!^)
) else (
    echo  Installing CUDA PyTorch into "!PYTHON_EXE!" ...
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

:: -- Step 6b: Downgrade guard --
:: The index we install from must not leave the user with an older torch
:: than they started with.  Warn loudly instead of letting a silent
:: downgrade go unnoticed.
set "DOWNGRADED="
set "NEW_TORCH="
for /f "usebackq delims=" %%V in (`call "%PYTHON_EXE%" -c "import torch;print(torch.__version__)" 2^>nul`) do set "NEW_TORCH=%%V"
if defined OLD_TORCH if defined NEW_TORCH (
    if /i not "!OLD_TORCH!"=="!NEW_TORCH!" (
        for /f "usebackq delims=" %%R in (`call "%PYTHON_EXE%" -c "import sys;a=sys.argv[1].split('+')[0].split('.');b=sys.argv[2].split('+')[0].split('.');n=max(len(a),len(b));a=[int(x) if x.isdigit() else 0 for x in a+['0']*(n-len(a))];b=[int(x) if x.isdigit() else 0 for x in b+['0']*(n-len(b))];print(1 if a>b else 0)" "!OLD_TORCH!" "!NEW_TORCH!" 2^>nul`) do set "DOWNGRADED=%%R"
    )
)
if "!DOWNGRADED!"=="1" (
    echo.
    echo [WARN] torch was DOWNGRADED: !OLD_TORCH! -^> !NEW_TORCH!
    echo  The !CUDA_INDEX! index does not carry your previous version.
    echo  If a module needs the newer torch, reinstall from a newer index:
    echo    "%PYTHON_EXE%" -m pip install -U torch torchvision --index-url https://download.pytorch.org/whl/cu126
    echo.
)

:: -- Step 7: Install onnxruntime-gpu (replace mode) --
:: onnxruntime (CPU) and onnxruntime-gpu are separate packages but they
:: share the same `onnxruntime` import name and the same capi DLLs, and
:: each leaves its own dist-info behind.  Installing one over the other
:: without removing both produces a mixed state where `pip uninstall
:: onnxruntime` no longer finds its metadata and refuses to act.  Remove
:: BOTH names first, then install the GPU variant cleanly.
if /i "!ORT_STATUS!"=="GOOD" (
    echo  [SKIP] onnxruntime-gpu already has CUDA support.
) else (
    echo  Installing onnxruntime-gpu into "!PYTHON_EXE!" ...
    echo  Package: !ONNX_RT_SPEC!
    echo  This will download ~214 MB.
    echo.

    :: Remove both CPU and GPU variants (either may already be missing)
    "%PYTHON_EXE%" -m pip uninstall onnxruntime onnxruntime-gpu -y >nul 2>&1

    :: Install onnxruntime-gpu
    "%PYTHON_EXE%" -m pip install "!ONNX_RT_SPEC!" --prefer-binary

    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] onnxruntime-gpu installation failed.
        echo  Check your internet connection and try again.
        pause
        exit /b 1
    )
)

:: Verify the CUDA provider actually became available
set "ORT_AFTER="
for /f "usebackq delims=" %%P in (`call "%PYTHON_EXE%" -c "import onnxruntime;print(','.join(onnxruntime.get_available_providers()))" 2^>nul`) do set "ORT_AFTER=%%P"
echo.
if defined ORT_AFTER (
    echo  onnxruntime providers now: !ORT_AFTER!
    echo !ORT_AFTER! | findstr /C:"CUDAExecutionProvider" >nul
    if !ERRORLEVEL! NEQ 0 (
        echo  [WARN] onnxruntime-gpu installed but the CUDA provider is NOT available.
        echo   Usually the NVIDIA driver is too old for this build.
        echo   GPU text detection / OCR will fall back to CPU.
    )
) else (
    echo  [WARN] Could not import onnxruntime after installation.
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
exit /b 0

:: ==================================================================
:: Manual mode - print commands for the user's own Python environment
:: ==================================================================
:manual_mode

:: Pick the CUDA index.  Without a bundled Python we cannot query
:: nvidia-smi through it, so use whatever is on PATH.  The major part is
:: taken from the first non-empty line; `delims=. tokens=1` yields the
:: major even when the query behaves differently across driver versions.
set "GPU_CC="
for /f "usebackq delims=. tokens=1" %%M in (`nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2^>nul`) do (
    if not defined GPU_CC set "GPU_CC=%%M"
)
if not defined GPU_CC set "GPU_CC=0"
echo !GPU_CC!| findstr /R "^[0-9][0-9]*[0-9.]*$" >nul || set GPU_CC=0
for /f "delims=. tokens=1" %%M in ("!GPU_CC!") do set "GPU_CC=%%M"

if !GPU_CC! GEQ 10 (
    set CUDA_INDEX=cu132
    set CUDA_LABEL=CUDA 13.2
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if !GPU_CC! GEQ 9 (
    set CUDA_INDEX=cu130
    set CUDA_LABEL=CUDA 13.0
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else if !GPU_CC! GEQ 6 (
    set CUDA_INDEX=cu126
    set CUDA_LABEL=CUDA 12.6
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
) else (
    set CUDA_INDEX=cu126
    set CUDA_LABEL=CUDA 12.6 ^(default^)
    set "ONNX_RT_SPEC=onnxruntime-gpu>=1.20,<1.29"
)

:: Detect this Python's current torch (if any) for the report
set "MANUAL_TORCH="
for /f "usebackq delims=" %%V in (`call python -c "import torch;print(torch.__version__)" 2^>nul`) do set "MANUAL_TORCH=%%V"

echo ============================================
echo  Manual mode - nothing will be installed here
echo ============================================
echo.
if defined PYTHON_EXE (
    echo  A bundled Python was found at:
    echo    !PYTHON_EXE!
    echo  but manual mode was requested, so it is left untouched.
) else (
    echo  No bundled Python ^(ballontrans_pylibs_win^) was found next to
    echo  this script, so there is nothing to install into automatically.
    echo  That is normal when you run the app from source - use the
    echo  commands below in your own environment instead.
)
echo.
if not "!GPU_CC!"=="0" (
    echo  Detected GPU compute capability: !GPU_CC!.x
    echo  Selected CUDA: !CUDA_LABEL! ^(!CUDA_INDEX!^)
) else (
    echo  Could not detect an NVIDIA GPU ^(nvidia-smi unavailable^).
    echo  Defaulting to !CUDA_LABEL! - change the index below if needed.
)
if defined MANUAL_TORCH echo  Current torch for "python": !MANUAL_TORCH!
echo.
echo  Run these in YOUR OWN Python environment ^(the venv, conda env
echo  or system Python you launch the app with^):
echo.
echo  1. Install CUDA PyTorch:
echo     python -m pip install -U torch torchvision --index-url https://download.pytorch.org/whl/!CUDA_INDEX! --prefer-binary
echo.
echo  2. Install onnxruntime-gpu ^(replace CPU onnxruntime^):
echo     python -m pip uninstall onnxruntime onnxruntime-gpu -y
echo     python -m pip install "!ONNX_RT_SPEC!" --prefer-binary
echo.
echo  Notes:
echo    - torchaudio is not installed on purpose; this app has no audio I/O.
echo    - Do NOT use the cu124 index: it stops at torch 2.6.0 and will
echo      silently downgrade an up-to-date install.
echo    - Verify with:
echo        python -c "import torch;print(torch.__version__, torch.cuda.is_available())"
echo.
pause
exit /b 0

:usage
echo Usage: install_cuda.bat [--manual ^| --replace]
echo.
echo   ^(no argument^)  Install into the bundled Python when one is found;
echo                  otherwise print the pip commands for your own Python.
echo   --manual      Only print the pip commands. Use this for source
echo                  installs, virtual environments and conda environments.
echo   --replace     Always install into the bundled Python.
exit /b 0
