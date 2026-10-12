@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo  EdgeSidekick
echo  ----------------------------------------------------
echo.

call "%~dp0scripts\ensure-python.bat"
if errorlevel 1 goto :fail

echo  Python: %SIDEKICK_PYTHON%
echo.

echo [1/2] Installing Python packages...
call "%~dp0scripts\install-python-deps.bat"
if errorlevel 1 goto :fail

echo.
echo [2/2] Starting server (Ctrl+C to stop)...
echo.
"%SIDEKICK_PYTHON%" main.py %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo EdgeSidekick exited with code %ERR%
  goto :fail_pause
)
pause
exit /b 0

:fail
echo.
echo Failed. Fix the error above, then double-click start.bat again.
:fail_pause
pause
exit /b 1
