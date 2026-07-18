@echo off
setlocal

cd /d "%~dp0"
set "PYTHON=D:\Tools\MediaFlow\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo MediaFlow Pro Python environment was not found:
    echo %PYTHON%
    echo.
    echo Follow the development environment instructions in README.md first.
    pause
    exit /b 1
)

"%PYTHON%" -m mediaflow.desktop.app %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo MediaFlow Pro failed to start. Exit code: %EXIT_CODE%
    pause
)

exit /b %EXIT_CODE%
