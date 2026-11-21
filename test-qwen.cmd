@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1
rem Opt-in local model download (~6.2 GB) and synthetic vision test only.
rem Does NOT read real patient PDFs, touch input/output, or modify .env.
if not exist "%~dp0scripts\test-qwen-vl.ps1" (
  echo ERROR: scripts\test-qwen-vl.ps1 missing. Update the project first.
  goto :failed
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\test-qwen-vl.ps1" -Install
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Press any key to close this test window . . .
pause >nul
endlocal & exit /b %EXIT_CODE%
:failed
echo.
pause >nul
endlocal & exit /b 2
