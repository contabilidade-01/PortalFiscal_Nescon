@echo off
REM Modo PRODUCAO local (waitress). Requer: pip install waitress
cd /d "%~dp0"
set PY=C:\Users\parce\AppData\Local\Programs\Python\Python313\python.exe
echo Portal Fiscal (waitress) em http://localhost:5001 ...
"%PY%" -m waitress --listen=0.0.0.0:5001 app:app
pause
