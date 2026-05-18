@echo off
echo === Regenerating translations ===
cd /d "%~dp0.."
python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm
if %ERRORLEVEL% EQU 0 (
    echo Done. Restart the app to apply changes.
) else (
    echo Compilation failed!
    pause
)
