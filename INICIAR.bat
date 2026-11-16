@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe "%~dp0painel.py"
  exit /b
)
where pyw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pyw.exe -3 "%~dp0painel.py"
  exit /b
)
echo Python nao encontrado. Instale Python 3 e tente novamente.
pause
