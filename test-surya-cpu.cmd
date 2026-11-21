@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1
rem Installs an ISOLATED CPU-only OCR pilot and runs a fictional test page.
rem Does not open actual input PDFs, edit outputs, or change .env.
if not exist "%~dp0scripts\test-surya-cpu.ps1" (
    echo ERROR: scripts\test-surya-cpu.ps1 is missing. Update PDF Merger first.
    goto :failed
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\test-surya-cpu.ps1" -Install
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Press any key to close this window . . .
pause >nul
endlocal & exit /b %EXIT_CODE%
:failed
echo.
pause >nul
endlocal & exit /b 2
