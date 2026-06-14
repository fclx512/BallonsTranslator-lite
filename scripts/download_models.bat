@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================
echo  BallonsTranslator-lite 模型下载脚本
echo ============================================
echo.
echo 模型文件将下载到 data/models/ 目录
echo 如果下载中断，重新运行本脚本即可（会跳过已有文件）
echo.

set MODELS_DIR=data\models
if not exist "%MODELS_DIR%" mkdir "%MODELS_DIR%"

:: PowerShell 下载辅助函数（带进度条）
set "PS_DOWNLOAD=powershell -Command "$wc = New-Object System.Net.WebClient; try{ $wc.DownloadFile('%%~1', '%%~2') } catch{ exit 1 }""

:: ============================================
:: 1. ComicTextDetector (torch)
:: ============================================
set "URL=https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3"
set "FILE=%MODELS_DIR%\comictextdetector.pt"
if not exist "%FILE%" (
    echo [1/6] 下载 ComicTextDetector (torch) ...
    powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('%URL%/comictextdetector.pt', '%FILE%')"
    if %ERRORLEVEL% equ 0 (echo   ✓ 完成) else (echo   ✗ 失败 & exit /b 1)
) else (echo [1/6] ComicTextDetector (torch) — 已存在，跳过)

:: ============================================
:: 2. ComicTextDetector (ONNX, CPU用)
:: ============================================
set "FILE=%MODELS_DIR%\comictextdetector.pt.onnx"
if not exist "%FILE%" (
    echo [2/6] 下载 ComicTextDetector (ONNX) ...
    powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('%URL%/comictextdetector.pt.onnx', '%FILE%')"
    if %ERRORLEVEL% equ 0 (echo   ✓ 完成) else (echo   ✗ 失败 & exit /b 1)
) else (echo [2/6] ComicTextDetector (ONNX) — 已存在，跳过)

:: ============================================
:: 3. MIT48pxCTC OCR（从 zip 解压）
:: ============================================
set "FILE=%MODELS_DIR%\mit48pxctc_ocr.ckpt"
if not exist "%FILE%" (
    echo [3/6] 下载 MIT48pxCTC OCR ...
    powershell -Command "$wc = New-Object System.Net.WebClient; $tmp='%TEMP%\ocr-ctc.zip'; $wc.DownloadFile('%URL%/ocr-ctc.zip', $tmp); Expand-Archive $tmp -DestinationPath '%TEMP%\ocr-ctc' -Force; move '%TEMP%\ocr-ctc\ocr-ctc.ckpt' '%FILE%'; copy '%TEMP%\ocr-ctc\alphabet-all-v5.txt' 'data\alphabet-all-v5.txt'; rm $tmp; rm '%TEMP%\ocr-ctc' -Recurse -ErrorAction SilentlyContinue"
    if %ERRORLEVEL% equ 0 (echo   ✓ 完成) else (echo   ✗ 失败 & exit /b 1)
) else (echo [3/6] MIT48pxCTC OCR — 已存在，跳过)

:: ============================================
:: 4. AOT Inpainter
:: ============================================
set "FILE=%MODELS_DIR%\aot_inpainter.ckpt"
if not exist "%FILE%" (
    echo [4/6] 下载 AOT Inpainter ...
    powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('%URL%/inpainting.ckpt', '%FILE%')"
    if %ERRORLEVEL% equ 0 (echo   ✓ 完成) else (echo   ✗ 失败 & exit /b 1)
) else (echo [4/6] AOT Inpainter — 已存在，跳过)

:: ============================================
:: 5. LaMa MPE Inpainter
:: ============================================
set "FILE=%MODELS_DIR%\lama_mpe.ckpt"
if not exist "%FILE%" (
    echo [5/6] 下载 LaMa MPE Inpainter ...
    powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('%URL%/inpainting_lama_mpe.ckpt', '%FILE%')"
    if %ERRORLEVEL% equ 0 (echo   ✓ 完成) else (echo   ✗ 失败 & exit /b 1)
) else (echo [5/6] LaMa MPE Inpainter — 已存在，跳过)

:: ============================================
:: 6. LaMa Large 512px（来自 HuggingFace）
:: ============================================
set "FILE=%MODELS_DIR%\lama_large_512px.ckpt"
if not exist "%FILE%" (
    echo [6/6] 下载 LaMa Large 512px (HuggingFace, ~195MB) ...
    powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('https://huggingface.co/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt', '%FILE%')"
    if %ERRORLEVEL% equ 0 (echo   ✓ 完成) else (echo   ✗ 失败 & exit /b 1)
) else (echo [6/6] LaMa Large 512px — 已存在，跳过)

:: ============================================
:: pkuseg 模型 —— 由应用首次启动时自动下载
:: ============================================
echo.
echo pkuseg 分词模型将由应用首次启动时自动下载，无需提前准备。
echo.
echo ============================================
echo  所有模型下载完成！
echo ============================================
echo.
echo 现在可以运行 launch.bat 启动应用（会自动检测 GPU 或 CPU 模式）
echo.

pause
