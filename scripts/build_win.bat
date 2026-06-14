@echo off
chcp 65001 >nul
cd /d "%~dp0.."

:: ============================================
::  BallonsTranslator-lite  Nuitka Build
:: ============================================
:: 构建独立的 Windows 可执行文件（含 embedded Python）
:: 前置：pip install nuitka pip install -e .[gpu,onnx]
:: ============================================

set "VERSION=1.0.0"

:: 查看 Nuitka 版本
nuitka --version

:: ── 构建 ──
nuitka --standalone --mingw64 --show-memory --show-progress ^
    --enable-plugin=pyqt6 --include-qt-plugins=sensible,styles ^
    --nofollow-import-to=torch,torchvision,transformers,diffusers,ultralytics ^
    --nofollow-import-to=paddleocr,paddlepaddle,paddlex ^
    --nofollow-import-to=matplotlib,notebook,jupyter,pytest,tests,docs ^
    --follow-import-to=modules,ui,utils,config,translate,scripts ^
    --windows-console-mode=force ^
    --windows-product-version=%VERSION% ^
    --windows-company-name=BallonsTranslator ^
    --windows-product-name=BallonsTranslator-lite ^
    --output-dir=release ^
    launch.py

:: ── 复制额外资源 ──
if exist "release\launch.dist" (
    echo.
    echo ============================================
    echo  Copying additional resources...
    echo ============================================

    :: 数据目录
    if not exist "release\launch.dist\data" mkdir "release\launch.dist\data"

    :: 模型目录（留空，用户按需下载）
    if not exist "release\launch.dist\data\models" mkdir "release\launch.dist\data\models"

    :: 配置目录
    xcopy /E /I /Y "config" "release\launch.dist\config"

    :: 翻译文件
    xcopy /E /I /Y "translate" "release\launch.dist\translate"

    :: 图标
    if exist "icons" xcopy /E /I /Y "icons" "release\launch.dist\icons"

    :: 启动脚本（发行版用）
    copy /Y "launch.bat" "release\launch.dist\launch.bat"

    :: 模型下载脚本
    copy /Y "scripts\download_models.bat" "release\launch.dist\"

    :: manifest
    if exist "manifest.json" copy /Y "manifest.json" "release\launch.dist\manifest.json"

    echo  Resources copied.
)

echo.
echo ============================================
echo  Build complete! Output in release\launch.dist
echo ============================================
pause
