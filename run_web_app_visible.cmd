@echo off
title Marketing Audit Web App - Live Logs
cd /d "%~dp0"
echo Starting web_app.py with live pipeline logs...
echo Do not close this window while using http://127.0.0.1:5001
"C:\Users\sasa\AppData\Local\Python\pythoncore-3.14-64\python.exe" "%~dp0web_app.py"
echo.
echo web_app.py exited. Press any key to close this window.
pause >nul
