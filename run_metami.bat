@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "METAMI_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%METAMI_PYTHON%" (
    echo [M.E.T.A.M.I.] 仮想環境 .venv が見つかりません。
    echo.
    echo PowerShellで次のコマンドを実行してください。
    echo   py -3.12 -m venv .venv
    echo   .\.venv\Scripts\Activate.ps1
    echo   python -m pip install --upgrade pip
    echo   python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%METAMI_PYTHON%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo [M.E.T.A.M.I.] PySide6が仮想環境へインストールされていません。
    echo   .\.venv\Scripts\Activate.ps1
    echo   python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%METAMI_PYTHON%" "%~dp0src\main.py"
set "METAMI_EXIT_CODE=%ERRORLEVEL%"

if not "%METAMI_EXIT_CODE%"=="0" (
    echo.
    echo [M.E.T.A.M.I.] アプリがエラー終了しました。終了コード: %METAMI_EXIT_CODE%
    pause
)

exit /b %METAMI_EXIT_CODE%
