@echo off
rem Zavodni inzenyr pro F1 25 - spousteni na Windows
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo Prvni spusteni: vytvarim prostredi a instaluji zavislosti...
  %PY% -m venv .venv || goto :error
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -r requirements.txt || goto :error
)
.venv\Scripts\python -m race_engineer %*
goto :eof
:error
echo.
echo Nepodarilo se pripravit Python. Nainstaluj Python 3.11+ z python.org (zaskrtni "Add to PATH").
pause
