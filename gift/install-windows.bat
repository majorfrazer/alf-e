@echo off
REM Alf-E installer for Windows
REM Double-click this file in File Explorer.

setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Alf-E installer -- Windows
echo ============================================================
echo.

where docker >nul 2>nul
if errorlevel 1 (
  echo [X] Docker is not installed.
  echo.
  echo   Install Docker Desktop first:
  echo     https://www.docker.com/products/docker-desktop
  echo.
  echo   Opening the download page. Run this installer again
  echo   once Docker Desktop is running.
  echo.
  start "" "https://www.docker.com/products/docker-desktop"
  pause
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo [X] Docker is installed but not running.
  echo.
  echo   Start Docker Desktop, wait for the whale icon to go green,
  echo   then run this installer again.
  echo.
  pause
  exit /b 1
)

echo [OK] Docker is running
echo.

if not exist .env (
  copy .env.example .env >nul

  REM Generate a random ALFE_API_TOKEN using PowerShell
  for /f "delims=" %%T in ('powershell -NoProfile -Command "[Convert]::ToBase64String((1..32 ^| ForEach-Object { Get-Random -Minimum 0 -Maximum 256 }))"') do set ALFE_TOKEN=%%T

  REM Substitute into .env
  powershell -NoProfile -Command "(Get-Content .env) -replace 'ALFE_API_TOKEN=.*', 'ALFE_API_TOKEN=%ALFE_TOKEN%' | Set-Content .env"

  echo [OK] Generated your PWA login token ^(saved to .env^)
  echo.
  echo ------------------------------------------------------------
  echo   NOW: paste your two API keys into the .env file
  echo ------------------------------------------------------------
  echo.
  echo   Opening .env in Notepad. Replace:
  echo     ANTHROPIC_API_KEY=REPLACE_ME  --^> your Anthropic key
  echo     HA_API_TOKEN=REPLACE_ME                    --^> your HA token
  echo.
  echo   Save ^(Ctrl+S^), close Notepad, then run this installer again.
  echo.
  notepad .env
  pause
  exit /b 0
)

findstr /C:"REPLACE_ME" .env >nul
if not errorlevel 1 (
  echo [X] .env still has placeholder values.
  echo.
  echo   Edit .env and replace the REPLACE_ME placeholders, then
  echo   run this installer again.
  echo.
  notepad .env
  pause
  exit /b 1
)

echo [OK] .env looks good
echo.
echo Starting Alf-E... ^(first run downloads ~500MB, be patient^)
echo.

docker compose up -d

echo.
echo ============================================================
echo   Alf-E is starting. Give it 30 seconds, then open:
echo.
echo     http://localhost:8099
echo.
echo   Your PWA login token is in the .env file ^(ALFE_API_TOKEN^).
echo ============================================================
echo.

timeout /t 5 /nobreak >nul
start "" "http://localhost:8099"

pause
