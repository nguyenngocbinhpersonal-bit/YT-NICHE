@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set PORT=8501
echo.
echo  YT-NICHE dang chay tai:  http://localhost:%PORT%
echo  (Ctrl+C de dung)
echo.
start "" /b cmd /c "timeout /t 3 >nul & start http://localhost:%PORT%"
"%PY%" -m streamlit run yt_niche\ui\app.py --server.port %PORT%
pause
