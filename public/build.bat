@echo off
setlocal EnableDelayedExpansion
title MediaGrab - construction de l'executable
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Construction de mediagrab.exe
echo  ============================================================
echo.

set "PY="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
  echo  Python introuvable. Installe-le depuis https://python.org
  pause
  exit /b 1
)
echo  [1/4] Python : %PY%

echo  [2/4] Dependances de construction
"%PY%" -m pip install -U pyinstaller yt-dlp --quiet --disable-pip-version-check
if errorlevel 1 (
  echo        ECHEC de l'installation des dependances.
  pause
  exit /b 1
)

REM Source unique : server.py vit dans helper\, on le copie au moment de
REM construire plutot que d'en maintenir deux versions divergentes.
echo  [3/4] Copie de server.py depuis helper\
copy /y "..\helper\server.py" "server.py" >nul
if errorlevel 1 (
  echo        ECHEC : ..\helper\server.py introuvable.
  pause
  exit /b 1
)

echo  [4/4] PyInstaller
rmdir /s /q build 2>nul
del /q mediagrab.spec 2>nul

REM --noconsole : le mode --serve ne doit jamais faire clignoter de fenetre.
REM --add-data  : l'extension voyage dans l'executable et sera extraite
REM               a l'installation.
"%PY%" -m PyInstaller ^
  --noconfirm --onefile --noconsole ^
  --name mediagrab ^
  --icon "..\extension\icons\app.ico" ^
  --add-data "..\extension;extension" ^
  --hidden-import server ^
  --collect-submodules yt_dlp ^
  mediagrab.py

if errorlevel 1 (
  echo.
  echo  ECHEC de la construction.
  pause
  exit /b 1
)

echo.
echo  ============================================================
echo   Termine : dist\mediagrab.exe
echo  ============================================================
for %%f in ("dist\mediagrab.exe") do echo   Taille : %%~zf octets
echo.
echo   C'est le seul fichier a distribuer.
echo.
pause
