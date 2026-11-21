@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1

rem Runs from any current directory, including logs\. Keep the launcher
rem small: PowerShell performs the safe git update before starting start.cmd.
if not exist "%~dp0update-and-start.ps1" (
    echo ERROR: update-and-start.ps1 is missing. Download the complete project.
    goto :update_failed
)
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo ERROR: Windows PowerShell is not available.
    goto :update_failed
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-and-start.ps1"
set "RESULT=%ERRORLEVEL%"
rem start.cmd already holds the console open, including application errors.
if "%RESULT%"=="2" goto :update_failed
endlocal & exit /b %RESULT%

:update_failed
echo.
echo Update failed; PDF Merger was not started.
echo Press any key to close this window . . .
pause >nul
endlocal & exit /b 2
