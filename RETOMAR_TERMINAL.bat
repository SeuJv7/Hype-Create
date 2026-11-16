@echo off
cd /d "%~dp0"
set /p PEDIDO=Digite o ID do pedido salvo: 
where python >nul 2>nul
if %errorlevel%==0 (
  python assistente.py --retomar "%PEDIDO%"
) else (
  py -3 assistente.py --retomar "%PEDIDO%"
)
echo.
pause
