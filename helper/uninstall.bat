@echo off
setlocal EnableDelayedExpansion
title MediaGrab - desinstallation
cd /d "%~dp0"

set "ROOT=%~dp0.."
for %%i in ("%ROOT%") do set "ROOT=%%~fi"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MediaGrab.lnk"
set "DOWNLOADS=%USERPROFILE%\Downloads\MediaGrab"

set "PY="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"

echo.
echo  ============================================================
echo   MediaGrab - desinstallation
echo  ============================================================
echo   Dossier : %ROOT%
echo.
echo   Tes videos telechargees ne seront PAS supprimees.
echo   Chaque etape sensible demande confirmation.
echo.

REM --- 1. serveur ----------------------------------------------------------
echo  [1/5] Arret du serveur
REM Deux criteres, pour ne jamais tuer un autre script Python de l'utilisateur :
REM la ligne de commande doit mentionner MediaGrab\...\server.py, ou bien le
REM processus doit detenir le port 8787 tout en etant un python.
powershell -NoProfile -Command "$ids=@(); Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'pythonw?\.exe' -and $_.CommandLine -like '*MediaGrab*server.py*' } | ForEach-Object { $ids += $_.ProcessId }; foreach ($c in @(Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue)) { $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -match 'pythonw?$') { $ids += $p.Id } }; $u = @($ids | Select-Object -Unique); $u | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; Write-Host ('        ' + $u.Count + ' processus arrete(s)')" 2>nul

REM --- 2. demarrage automatique -------------------------------------------
echo  [2/5] Demarrage automatique
if exist "%LINK%" (
  del "%LINK%"
  echo        raccourci supprime
) else (
  echo        aucun raccourci
)

REM --- 3. reglages ---------------------------------------------------------
echo  [3/5] Fichier de reglages
if exist "%~dp0config.json" (
  choice /c ON /n /m "        Supprimer config.json ? [O/N] "
  if errorlevel 2 (
    echo        conserve
  ) else (
    del "%~dp0config.json"
    echo        supprime
  )
) else (
  echo        aucun config.json
)
if exist "%~dp0error.log" del "%~dp0error.log"

REM --- 4. yt-dlp -----------------------------------------------------------
echo  [4/5] Bibliotheque yt-dlp
echo        Attention : yt-dlp est un outil generaliste,
echo        tu l'utilises peut-etre pour autre chose.
choice /c ON /n /m "        Desinstaller yt-dlp ? [O/N] "
if errorlevel 2 (
  echo        conserve
) else (
  if defined PY (
    "%PY%" -m pip uninstall -y yt-dlp --quiet >nul 2>&1
    echo        desinstalle
  ) else (
    echo        Python introuvable, ignore
  )
)

REM --- 5. extension + dossier ---------------------------------------------
echo  [5/5] Extension Chrome
echo        A retirer a la main, Chrome l'interdit depuis un script :
echo          chrome://extensions  ^>  MediaGrab  ^>  Supprimer
echo.

if exist "%DOWNLOADS%" (
  set "N=0"
  for %%f in ("%DOWNLOADS%\*") do set /a N+=1
  echo        Tes !N! fichier^(s^) dans %DOWNLOADS%
  echo        seront conserves dans tous les cas.
  echo.
)

choice /c ON /n /m "        Supprimer le dossier du programme ? [O/N] "
if errorlevel 2 goto :keep

REM Ce script se trouve dans le dossier a effacer : on delegue la suppression
REM a un processus detache qui attend d'abord que celui-ci se termine.
start "" cmd /c "timeout /t 2 /nobreak >nul & rmdir /s /q "%ROOT%""
echo.
echo   Dossier supprime dans 2 secondes. Desinstallation terminee.
timeout /t 3 /nobreak >nul
exit /b 0

:keep
echo.
echo  ============================================================
echo   Desinstallation terminee. Dossier conserve.
echo  ============================================================
echo   Pour tout reactiver : install.bat
echo.
pause
