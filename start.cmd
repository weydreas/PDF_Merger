@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "EXIT_CODE=1"
if not exist "%~dp0merge_windows.ps1" (
    echo ERROR: merge_windows.ps1 is missing. Download the complete project.
    goto :finish
)
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo ERROR: Windows PowerShell is not available.
    goto :finish
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0merge_windows.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
:finish
echo.
echo Press any key to close this window . . .
pause >nul
endlocal & exit /b %EXIT_CODE%
