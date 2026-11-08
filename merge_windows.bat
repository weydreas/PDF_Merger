@echo off
rem Backward-compatible Windows launcher; all interactive UI lives in PowerShell.
call "%~dp0start.cmd" %*
exit /b %ERRORLEVEL%
