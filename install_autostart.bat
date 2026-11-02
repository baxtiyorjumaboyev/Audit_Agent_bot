@echo off
chcp 65001 >nul
rem Kompyuter yoqilganda agent o'zi, oynasiz (yashirin) ishga tushadigan qiladi.
cd /d "%~dp0"
for /f "delims=" %%p in ('python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%p"
set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Audit Agent.vbs"
(
echo Set sh = CreateObject("WScript.Shell"^)
echo sh.CurrentDirectory = "%~dp0"
echo sh.Run """%PYW%"" ""%~dp0main.py"" run", 0, False
) > "%VBS%"
echo Avtomatik ishga tushish o'rnatildi: %VBS%
wscript "%VBS%"
echo Agent yashirin rejimda ishga tushirildi.
