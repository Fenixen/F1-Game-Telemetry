@echo off
rem Zavodni inzenyr pro F1 25 - spousteni na Windows
rem Pouziva jen standardni knihovnu Pythonu: nic se nestahuje ani neinstaluje.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (
  py -3 -m race_engineer %*
) || (
  where python >nul 2>nul && (
    python -m race_engineer %*
  ) || (
    echo Python nebyl nalezen. Nainstaluj Python 3.10+ z python.org a zaskrtni "Add python.exe to PATH".
  )
)
if errorlevel 1 pause
