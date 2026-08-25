@echo off
REM Lancador do Portal Fiscal Nescon (NFe + NFSe)
cd /d "%~dp0"
echo Iniciando Portal Fiscal Nescon em http://localhost:5001 ...
"C:\Users\parce\AppData\Local\Programs\Python\Python313\python.exe" app.py
pause
