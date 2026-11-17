@echo off
rem Starts NewsSharp from this directory (newssharp.cmd): `manage.py serve` applies migrations and runs the
rem web and worker processes. Arguments are passed to serve.
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set PYTHON=python
"%PYTHON%" manage.py serve %*
exit /b %ERRORLEVEL%
