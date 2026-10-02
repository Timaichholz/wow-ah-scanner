@echo off
cd /d "%~dp0"
echo Installiere Abhaengigkeiten ...
python -m pip install -r requirements.txt
if not exist config.toml (
  copy config.example.toml config.toml >nul
  echo.
  echo config.toml wurde angelegt. Bitte client_id, client_secret und Realm eintragen,
  echo dann diese Datei erneut starten.
  notepad config.toml
  pause
  exit /b
)
python -m ahscanner setup
pause
