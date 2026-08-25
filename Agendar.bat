@echo off
REM Cria/atualiza a tarefa diaria do Portal Fiscal Nescon (06:00).
set PY=C:\Users\parce\AppData\Local\Programs\Python\Python313\python.exe
set SCRIPT=%~dp0run_diario.py
schtasks /create /tn "PortalFiscalNescon" /tr "\"%PY%\" \"%SCRIPT%\"" /sc daily /st 06:00 /f
echo.
echo Tarefa 'PortalFiscalNescon' criada para rodar todo dia as 06:00.
echo Para rodar agora:   schtasks /run /tn "PortalFiscalNescon"
echo Para remover:       schtasks /delete /tn "PortalFiscalNescon" /f
pause
