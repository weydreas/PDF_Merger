@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup_windows.bat once before using this utility.
  pause
  exit /b 2
)
.venv\Scripts\python.exe pdf_merger.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if %EXIT_CODE% NEQ 0 echo Some PDFs could not be processed. See errors above.
pause
exit /b %EXIT_CODE%
