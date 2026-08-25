@echo off
cd /d "%~dp0"
echo Gerando backup do Portal Fiscal na Area de trabalho...
python ferramentas\backup_dados.py
if errorlevel 1 (
  echo Falhou. Confira se o Python abre nesta pasta.
  pause
  exit /b 1
)
echo.
echo Pronto. O ZIP esta na Area de trabalho.
pause
