@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

title DataHunt Autonomous Research AI Agent

echo =====================================================================
echo  DATAHUNT -- Autonomous Multi-Agent Research Workstation
echo =====================================================================
echo.

cd /d "%~dp0"

:: 1. Check Python installation
where python >nul 2>&1
if %errorlevel% neq 0 goto no_python

:: 2. Check and activate virtual environment if present
if exist ".venv\Scripts\activate.bat" goto activate_dot_venv
if exist "venv\Scripts\activate.bat" goto activate_venv
echo [INFO] No local virtual environment found. Using active Python environment.
goto check_env

:activate_dot_venv
echo [INFO] Activating virtual environment .venv ...
call .venv\Scripts\activate.bat
goto check_env

:activate_venv
echo [INFO] Activating virtual environment venv ...
call venv\Scripts\activate.bat
goto check_env

:check_env
:: 3. Check for .env file
if exist ".env" goto run_migration
if exist ".env.example" (
    echo [INFO] .env not found. Creating from .env.example ...
    copy .env.example .env >nul
    echo [NOTICE] Created .env file. Please ensure GEMINI_API_KEY is configured in .env
)

:run_migration
:: 4. Run database migrations
echo [INFO] Synchronizing SQLite schema and migrations...
python -m datahunt.cli migrate
if %errorlevel% neq 0 (
    echo [WARN] Migration returned non-zero code. Continuing launch...
)

:: 5. Start FastAPI server in the background
echo.
echo =====================================================================
echo  DataHunt Server running at: http://127.0.0.1:8000
echo  Interactive API Docs:       http://127.0.0.1:8000/docs
echo  Agent Cockpit:              http://127.0.0.1:8000/cockpit.html
echo  Job Tracker Kanban:         http://127.0.0.1:8000/jobs.html
echo.
echo  Press Ctrl+C in this terminal to terminate the server.
echo =====================================================================
echo.

start "DataHunt API" /b cmd /c "python -m uvicorn datahunt.api:app --host 127.0.0.1 --port 8000 --reload --reload-dir datahunt --reload-dir frontend"

:: 6. Wait for the server before opening the browser
set /a attempts=0
:wait_for_server
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/health; if ($r.StatusCode -eq 200) { exit 0 } } catch {} exit 1" >nul 2>&1
if not errorlevel 1 goto server_ready
set /a attempts+=1
if %attempts% GEQ 30 goto server_timeout
timeout /t 1 /nobreak >nul
goto wait_for_server

:server_ready
echo [INFO] Server ready. Launching DataHunt Cybernetic Hub in default browser...
start "" "http://127.0.0.1:8000"
goto end

:server_timeout
echo [WARN] Server did not answer within 30 seconds. Open http://127.0.0.1:8000 manually when ready.
goto end

:no_python
echo.
echo [ERROR] Python is not found in system PATH.
echo Please install Python 3.10+ from https://www.python.org/ and check Add to PATH.
echo.
goto end

:end
echo.
echo Server stopped. Press any key to exit...
pause >nul
