@echo off
setlocal
cd /d "%~dp0"
if defined SMART_PARKING_PYTHON (
  set "parking_python=%SMART_PARKING_PYTHON%"
  goto validate
)
set "parking_python=%~dp0.venv\Scripts\python.exe"
if exist "%parking_python%" goto run
echo No project Python environment was found.
echo Create .venv here and install requirements-v51.txt; see README.md.
echo You may also set SMART_PARKING_PYTHON to the full python.exe path.
if "%~1"=="" pause
exit /b 1
:validate
if exist "%parking_python%" goto run
echo SMART_PARKING_PYTHON does not point to an existing python.exe.
if "%~1"=="" pause
exit /b 1
:run
echo Python: "%parking_python%"
"%parking_python%" -B "%~dp0main.py" %*
set "parking_exit=%errorlevel%"
if not "%parking_exit%"=="0" if "%~1"=="" pause
exit /b %parking_exit%
