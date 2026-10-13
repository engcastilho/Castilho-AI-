@echo off
REM Inicia o Castilho AI e abre o navegador. Basta dar dois cliques neste arquivo.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente nao encontrado. Rode primeiro, nesta pasta:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist ".env" (
    echo Arquivo .env nao encontrado. Copie o .env.example para .env e coloque sua chave.
    pause
    exit /b 1
)

echo Iniciando o Castilho AI em http://localhost:8000
echo Para desligar, feche esta janela ou aperte Ctrl+C.
start "" cmd /c "timeout /t 3 /nobreak >/dev/null & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app
pause
