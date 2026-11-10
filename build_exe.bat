@echo off
rem Sestavi samostatny RaceEngineer.exe (bez nutnosti instalovat Python na cilovem PC)
cd /d "%~dp0"
if not exist .venv call start.bat --help >nul
.venv\Scripts\python -m pip install --quiet pyinstaller
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --name RaceEngineer --add-data "frontend;frontend" race_engineer\__main__.py
echo Hotovo: dist\RaceEngineer.exe
pause
