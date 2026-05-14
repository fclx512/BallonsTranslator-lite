@echo off
cd %~dp0

:: GPU mode - uses embedded Python 3.13 from the portable environment
:: PyTorch with CUDA will be detected from user's system Python installation
set PYTHON=%~dp0ballontrans_pylibs_win\python.exe
set BTRANSLATOR_GPU_MODE=1

set ERROR_REPORTING=FALSE

mkdir tmp 2>NUL

%PYTHON% -c "" >tmp/stdout.txt 2>tmp/stderr.txt
if %ERRORLEVEL% == 0 goto :launch
echo Error: Embedded Python not found. The portable environment may be corrupted.
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