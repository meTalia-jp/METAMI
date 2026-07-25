@echo off
setlocal
cd /d "%~dp0"

set "METAMI_PYTHON=%~dp0.venv\Scripts\python.exe"
set "QT_LOGGING_RULES=qt.multimedia.ffmpeg.info=false"

if not exist "%METAMI_PYTHON%" (
    echo [M.E.T.A.M.I.] Setup is incomplete: .venv was not found.
    echo.
    echo See "Setup and Launch" in README.md.
    echo.
    pause
    exit /b 1
)

"%METAMI_PYTHON%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo [M.E.T.A.M.I.] Required packages are not installed.
    echo.
    echo See "Setup and Launch" in README.md.
    echo.
    pause
    exit /b 1
)

"%METAMI_PYTHON%" "%~dp0src\main.py"
set "METAMI_EXIT_CODE=%ERRORLEVEL%"

if not "%METAMI_EXIT_CODE%"=="0" (
    echo.
    echo [M.E.T.A.M.I.] The app exited with error code %METAMI_EXIT_CODE%.
    pause
)

exit /b %METAMI_EXIT_CODE%
