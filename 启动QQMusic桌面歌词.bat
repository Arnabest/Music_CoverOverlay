@echo off
cd /d "%~dp0"
title QQMusic Overlay NG

REM Python 3.14+ uses mimalloc by default, which can cause
REM "unable to alloc" crashes on some Windows configurations.
REM Force the system malloc allocator as a workaround.
set PYTHONMALLOC=malloc

set PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
if not exist "%PY%" set PY=

if "%PY%"=="" (
    for /f "tokens=*" %%a in ('where python 2^>nul') do set PY=%%a
)

if "%PY%"=="" (
    echo [ERROR] Python not found
    echo Install Python 3.13+ or edit this file to set the correct path.
    pause
    exit /b 1
)

echo QQMusic Overlay NG Starting...
echo Python: %PY%
echo.

"%PY%" "%~dp0main.py"

echo.
echo Exit code: %ERRORLEVEL%
if %ERRORLEVEL% neq 0 (
    echo.
    echo If the window did not appear, check the log file:
    echo %TEMP%\qqoverlay_ng.log
    pause
)
exit /b %ERRORLEVEL%