@echo off
setlocal

cd /d "%~dp0"
title Branch Builder Launcher

echo Branch Builder one-click launcher
echo Project directory: %CD%
echo.

set "PY_CMD="
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PY_CMD=py -3"
) else (
  where python >nul 2>nul
  if %ERRORLEVEL%==0 set "PY_CMD=python"
)

if not defined PY_CMD (
  echo ERROR: Python was not found.
  echo Install Python 3 first, then run this file again.
  echo Download: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo Using Python command: %PY_CMD%

%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not %ERRORLEVEL%==0 (
  echo WARNING: Python 3.9 or newer is recommended.
  %PY_CMD% -c "import sys; print('Current Python: ' + '.'.join(map(str, sys.version_info[:3])))"
  echo.
  echo Older Python versions may fail on newer project code or dependencies.
  echo If startup or C export fails, install Python 3.11 or newer and run this file again.
  echo Download: https://www.python.org/downloads/
  echo.
)

%PY_CMD% -c "import sympy" >nul 2>nul
if not %ERRORLEVEL%==0 (
  echo SymPy is not installed. Installing SymPy now...
  %PY_CMD% -m pip install sympy
  if not %ERRORLEVEL%==0 (
    echo.
    echo ERROR: Failed to install SymPy.
    echo Try running this manually:
    echo   %PY_CMD% -m pip install sympy
    echo.
    pause
    exit /b 1
  )
)

echo.
echo Starting local server...
start "Branch Builder Local Server" /D "%~dp0" cmd /k "%PY_CMD% local_server.py"

echo Waiting for the server to start...
timeout /t 2 /nobreak >nul

echo Opening browser: http://127.0.0.1:4177/
start "" "http://127.0.0.1:4177/"

echo.
echo If the page does not load immediately, wait a few seconds and refresh.
echo Keep the "Branch Builder Local Server" window open while using the app.
echo.
pause
