@echo off
setlocal EnableDelayedExpansion
title MediaGrab - installation
cd /d "%~dp0"

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\MediaGrab.lnk"

REM Chemin absolu du dossier de l'extension, sans le "\..\" intermediaire
set "EXT=%~dp0..\extension"
for %%i in ("%EXT%") do set "EXT=%%~fi"

echo.
echo  ============================================================
echo   MediaGrab - installation
echo  ============================================================
echo   Relancer ce fichier met aussi yt-dlp a jour.
echo.

REM --- 1. Python -----------------------------------------------------------
echo  [1/6] Python
set "PY="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"

if not defined PY (
  echo        INTROUVABLE.
  echo        Installe Python depuis https://python.org
  echo        en cochant "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

REM pythonw.exe = Python sans fenetre de console. On le derive du chemin de
REM python.exe plutot que via "where pythonw", qui renvoie l'alias Microsoft
REM Store et lance un processus intermediaire inutile.
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"

echo        %PY%

REM --- 2. ffmpeg -----------------------------------------------------------
echo  [2/6] ffmpeg
set "FF="
for /f "delims=" %%i in ('where ffmpeg 2^>nul') do if not defined FF set "FF=%%i"

if not defined FF (
  echo        INTROUVABLE - le muxage video et le MP3 ne marcheront pas.
  echo        Telecharge-le sur https://www.gyan.dev/ffmpeg/builds/
  echo        puis ajoute son dossier "bin" au PATH.
  echo.
) else (
  echo        %FF%
)

REM --- 3. yt-dlp -----------------------------------------------------------
echo  [3/6] yt-dlp ^(installation ou mise a jour^)
"%PY%" -m pip install -U yt-dlp --quiet --disable-pip-version-check
if errorlevel 1 (
  echo        ECHEC de l'installation de yt-dlp.
  pause
  exit /b 1
)
REM Passage par un fichier temporaire : un "for /f" sur une commande a la fois
REM entre guillemets (Program Files) et contenant elle-meme des guillemets
REM est mal decoupe par cmd.
set "YTV=inconnue"
"%PY%" -c "import yt_dlp;print(yt_dlp.version.__version__)" > "%TEMP%\ytg_ver.txt" 2>nul
if exist "%TEMP%\ytg_ver.txt" (
  set /p YTV=<"%TEMP%\ytg_ver.txt"
  del "%TEMP%\ytg_ver.txt"
)
echo        version !YTV!

REM --- 4. arret d'une instance deja en cours -------------------------------
echo  [4/6] Arret d'une eventuelle instance en cours
REM Deux methodes complementaires : la ligne de commande, puis le detenteur
REM du port 8787 - ce qui rattrape les instances lancees autrement.
powershell -NoProfile -Command "$ids=@(); Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'pythonw?\.exe' -and $_.CommandLine -like '*MediaGrab*server.py*' } | ForEach-Object { $ids += $_.ProcessId }; foreach ($c in @(Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue)) { $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -match 'pythonw?$') { $ids += $p.Id } }; $ids | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" 2>nul
echo        ok

REM --- 5. demarrage automatique -------------------------------------------
echo  [5/6] Demarrage automatique a l'ouverture de session
if exist "%LINK%" del "%LINK%"
REM Le chemin complet de server.py doit figurer dans les arguments : c'est
REM lui qui rend le processus identifiable ensuite (arret, desinstallation).
REM Avec un simple 'server.py', la ligne de commande ne contient aucune trace
REM de MediaGrab et le processus devient introuvable.
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LINK%'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0server.py\"'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Description='Serveur local MediaGrab'; $s.Save()"

if exist "%LINK%" (
  echo        active - sans fenetre, via pythonw.exe
) else (
  echo        ECHEC de la creation du raccourci.
)

REM --- 6. demarrage immediat ----------------------------------------------
echo  [6/6] Demarrage du serveur
start "" "%PYW%" "%~dp0server.py"

set "OK="
for /l %%n in (1,1,10) do (
  if not defined OK (
    powershell -NoProfile -Command "try{ $r=Invoke-WebRequest 'http://127.0.0.1:8787/health' -TimeoutSec 2 -UseBasicParsing; exit 0 }catch{ exit 1 }" >nul 2>&1
    if not errorlevel 1 set "OK=1"
  )
)

echo.
if defined OK (
  echo  ============================================================
  echo   Serveur demarre : http://127.0.0.1:8787
  echo  ============================================================
  echo.
  echo   Il reste une seule chose a faire, une seule fois :
  echo.
  echo     1. Ouvre  chrome://extensions
  echo     2. Active "Mode developpeur"  ^(en haut a droite^)
  echo     3. Clique "Charger l'extension non empaquetee"
  echo     4. Choisis le DOSSIER :
  echo        %EXT%
  echo.
  echo   Ensuite, un bouton "Telecharger" apparaitra sur YouTube.
) else (
  echo  ============================================================
  echo   Le serveur n'a pas repondu.
  echo  ============================================================
  echo   Consulte  %~dp0error.log
)
echo.
pause
