$Host.UI.RawUI.WindowTitle = "Marketing Audit Web App - Live Logs"
Set-Location -LiteralPath "C:\Users\sasa\Desktop\模型建设\模型2：市场营销"
Write-Host "Starting web_app.py with live pipeline logs..." -ForegroundColor Cyan
Write-Host "Do not close this window while using http://127.0.0.1:5001" -ForegroundColor Yellow
& "C:\Users\sasa\AppData\Local\Python\pythoncore-3.14-64\python.exe" "C:\Users\sasa\Desktop\模型建设\模型2：市场营销\web_app.py"
