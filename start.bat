@echo off
cd /d "%~dp0"
set "MSA_PUBLIC_HOST=online.nubix.playit.plus"
set "MSA_PUBLIC_PORT=51516"
set "MSA_PORT=42069"

echo Iniciando servidor de Metal Slug Attack...
where python.exe >nul 2>nul
if not errorlevel 1 (
  python server.py
  goto :end
)
py -3 server.py
if errorlevel 1 echo Instala Python 3 desde python.org y vuelve a ejecutar este archivo.
:end
pause
