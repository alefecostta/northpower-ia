@echo off
chcp 65001 >nul
cd /d "%~dp0"
title North Power - Atendente IA

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY goto sempython

if not exist "venv\Scripts\python.exe" (
    echo.
    echo  Preparando pela primeira vez, aguarde um minuto...
    %PY% -m venv venv
    if errorlevel 1 goto erro
)

venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto erro

echo.
echo  ==========================================================
echo   North Power - Atendente IA
echo   O painel vai abrir no navegador: http://127.0.0.1:8765
echo   DEIXE ESTA JANELA ABERTA enquanto quiser o robo rodando.
echo   Para desligar, feche esta janela.
echo  ==========================================================
echo.
venv\Scripts\python.exe app.py
echo.
echo  O programa foi encerrado.
pause
exit /b

:sempython
echo.
echo  O Python nao esta instalado neste computador.
echo  Vou tentar instalar agora (pode pedir permissao do Windows)...
echo.
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
echo.
echo  Se a instalacao terminou, FECHE esta janela e abra o INICIAR.bat de novo.
echo  Se deu erro, baixe o Python em https://www.python.org/downloads/
echo  e marque a opcao "Add python.exe to PATH" na instalacao.
pause
exit /b

:erro
echo.
echo  Algo deu errado na preparacao. Tire um print desta janela e mande pro Claude.
pause
exit /b
