@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if errorlevel 1 goto :error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo.
echo Setup complete. Place PDFs in input and run merge_windows.bat.
pause
exit /b 0
:error
echo.
echo Setup failed. Ensure Python 3.10+ is installed.
pause
exit /b 1
