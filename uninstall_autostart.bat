@echo off
chcp 65001 >nul
rem Avtomatik ishga tushishni o'chiradi va ishlab turgan agentni to'xtatadi.
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Audit Agent.vbs" 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*main.py*run*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Avtomatik ishga tushish o'chirildi, agent to'xtatildi.
pause
