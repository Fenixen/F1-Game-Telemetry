@echo off
rem Sestavi samostatny RaceEngineer.exe (bez nutnosti instalovat Python na cilovem PC)
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .build-venv %PY% -m venv .build-venv
.build-venv\Scripts\python -m pip install --quiet pyinstaller
.build-venv\Scripts\python -m PyInstaller --noconfirm --onefile --name RaceEngineer --add-data "frontend;frontend" race_engineer\__main__.py
echo Hotovo: dist\RaceEngineer.exe
pause
