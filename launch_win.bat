@echo off
cd %~dp0

:: Keep system python.exe first (has PyTorch+CUDA); pylibs at end for DLL fallback
set PATH=%PATH%;%~dp0ballontrans_pylibs_win
set PYTHON=python.exe

set ERROR_REPORTING=FALSE

mkdir tmp 2>NUL

%PYTHON% -c "" >tmp/stdout.txt 2>tmp/stderr.txt
if %ERRORLEVEL% == 0 goto :launch
echo Error: Python not found. Please install Python with PyTorch and CUDA.
goto :show_stdout_stderr

:launch
%PYTHON% launch.py %*
pause
exit /b


:show_stdout_stderr

echo.
echo exit code: %errorlevel%

for /f %%i in ("tmp\stdout.txt") do set size=%%~zi
if %size% equ 0 goto :show_stderr
echo.
echo stdout:
type tmp\stdout.txt

:show_stderr
for /f %%i in ("tmp\stderr.txt") do set size=%%~zi
if %size% equ 0 goto :endofscript
echo.
echo stderr:
type tmp\stderr.txt

:endofscript

echo.
echo Launch unsuccessful. Exiting.
pause