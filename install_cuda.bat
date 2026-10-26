@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "PYTHON_EXE=%~dp0ballontrans_pylibs_win\python.exe"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONNOUSERSITE=1"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Missing the matching complete package: ballontrans_pylibs_win\python.exe
    exit /b 1
)
if /i "%~1"=="--replace" goto :install
if /i "%~1"=="--install" goto :install
if /i "%~1"=="--json" goto :json
if not "%~1"=="" if /i not "%~1"=="--check" (
    echo Usage: install_cuda.bat [--check^|--json^|--install]
    exit /b 2
)
"%PYTHON_EXE%" scripts\install_cuda_bundle.py
exit /b %ERRORLEVEL%
:install
"%PYTHON_EXE%" scripts\install_cuda_bundle.py --install
exit /b %ERRORLEVEL%
:json
"%PYTHON_EXE%" scripts\install_cuda_bundle.py --json
exit /b %ERRORLEVEL%
