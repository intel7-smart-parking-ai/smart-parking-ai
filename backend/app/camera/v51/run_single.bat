@echo off
call "%~dp0run_windows.bat" --single %*
exit /b %errorlevel%
