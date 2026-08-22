"""
MediaGrab - point d'entree de la version distribuable.

Un seul executable, trois modes :
  mediagrab.exe             fenetre d'installation
  mediagrab.exe --serve     serveur local, sans fenetre (demarrage automatique)
  mediagrab.exe --uninstall fenetre de desinstallation

Construit avec --noconsole : le mode --serve ne doit jamais afficher de
fenetre, et l'installateur a sa propre interface tkinter.
"""
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile

APP = "MediaGrab"
PORT = 8787
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def install_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP)


def startup_link():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup", "%s.lnk" % APP)


def bundled(name):
    """Chemin d'une ressource embarquee dans l'executable."""
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, name)


def running_from_install():
    if not getattr(sys, "frozen", False):
        return False
    return os.path.dirname(sys.executable).lower() == install_dir().lower()


# ---------------------------------------------------------------------------
# Mode serveur
# ---------------------------------------------------------------------------

def run_server():
    import server
    server.main()


# ---------------------------------------------------------------------------
# Etapes d'installation
# ---------------------------------------------------------------------------

def stop_running(log):
    """Arrete une instance deja lancee, sinon la copie du .exe echouerait."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'mediagrab.exe' -and "
         "$_.ProcessId -ne %d } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
         % os.getpid()],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    log("Instance precedente arretee.")


OLD_APP = "YTGrabber"


def migrate_old_install(log):
    """Retire les traces de l'installation portant l'ancien nom.

    Sans ce nettoyage, l'ancien serveur continuerait a se lancer a l'ouverture
    de session et occuperait le port 8787 avant le nouveau, qui refuserait
    alors de demarrer sans que rien ne l'explique.
    """
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "
         "'%s.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
         % OLD_APP.lower()],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    old_link = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                            "Start Menu", "Programs", "Startup",
                            "%s.lnk" % OLD_APP)
    if os.path.exists(old_link):
        os.remove(old_link)
        log("Ancien demarrage automatique (%s) retire." % OLD_APP)

    old_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), OLD_APP)
    if os.path.isdir(old_dir):
        log("Ancien dossier laisse en place, a supprimer si tu veux :")
        log("   %s" % old_dir)


def copy_self(target, log):
    if not getattr(sys, "frozen", False):
        log("Mode developpement : copie de l'executable ignoree.")
        return os.path.join(target, "mediagrab.exe")

    dest = os.path.join(target, "mediagrab.exe")
    if os.path.abspath(sys.executable).lower() != os.path.abspath(dest).lower():
        shutil.copy2(sys.executable, dest)
    log("Programme installe dans %s" % target)
    return dest


def copy_extension(target, log):
    src = bundled("extension")
    dest = os.path.join(target, "extension")
    if not os.path.isdir(src):
        log("ATTENTION : dossier extension introuvable dans le paquet.")
        return dest
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
    log("Extension Chrome extraite.")
    return dest


def ensure_ffmpeg(target, log):
    """ffmpeg fusionne l'image et le son : sans lui, pas de video ni de MP3."""
    found = shutil.which("ffmpeg")
    if found:
        log("ffmpeg deja present sur le systeme.")
        return os.path.dirname(found)

    local = os.path.join(target, "ffmpeg")
    if os.path.exists(os.path.join(local, "ffmpeg.exe")):
        log("ffmpeg deja telecharge.")
        return local

    log("Telechargement de ffmpeg (environ 30 Mo)…")
    os.makedirs(local, exist_ok=True)
    archive = os.path.join(target, "_ffmpeg.zip")

    try:
        with urllib.request.urlopen(FFMPEG_URL, timeout=120) as r, \
                open(archive, "wb") as f:
            shutil.copyfileobj(r, f)

        log("Extraction…")
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                name = os.path.basename(member)
                if name in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(member) as src, \
                            open(os.path.join(local, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
        log("ffmpeg installe.")
    except Exception as exc:
        log("ECHEC du telechargement de ffmpeg : %s" % exc)
        log("Installe-le manuellement depuis https://www.gyan.dev/ffmpeg/builds/")
        return ""
    finally:
        if os.path.exists(archive):
            os.remove(archive)

    return local


def write_config(target, ffmpeg_dir, log):
    import json
    path = os.path.join(target, "config.json")
    config = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            config = {}

    config.setdefault("output_dir",
                      os.path.join(os.path.expanduser("~"), "Downloads", APP))
    if ffmpeg_dir:
        config["ffmpeg_location"] = ffmpeg_dir

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    log("Destination : %s" % config["output_dir"])


def make_shortcut(exe, log):
    link = startup_link()
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s'; $s.Arguments='--serve';"
        "$s.WorkingDirectory='%s'; $s.WindowStyle=7;"
        "$s.Description='Serveur local MediaGrab'; $s.Save()"
        % (link, exe, os.path.dirname(exe))
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    log("Demarrage automatique active." if os.path.exists(link)
        else "ATTENTION : raccourci de demarrage non cree.")


def start_server(exe, log):
    subprocess.Popen([exe, "--serve"], cwd=os.path.dirname(exe),
                     creationflags=subprocess.CREATE_NO_WINDOW)
    log("Serveur demarre sur http://127.0.0.1:%d" % PORT)


def do_install(log, done):
    try:
        target = install_dir()
        os.makedirs(target, exist_ok=True)

        stop_running(log)
        migrate_old_install(log)
        exe = copy_self(target, log)
        copy_extension(target, log)
        ffmpeg_dir = ensure_ffmpeg(target, log)
        write_config(target, ffmpeg_dir, log)
        make_shortcut(exe, log)
        start_server(exe, log)

        log("")
        log("Installation terminee.")
        # On renvoie le dossier d'installation, et non extension\ : le bouton
        # doit ouvrir le dossier parent pour laisser glisser "extension"
        # directement dans Chrome.
        done(target)
    except Exception as exc:
        log("")
        log("ECHEC : %s" % exc)
        done(None)


def do_uninstall(log, done):
    target = install_dir()
    stop_running(log)

    link = startup_link()
    if os.path.exists(link):
        os.remove(link)
        log("Demarrage automatique retire.")

    log("")
    log("Retire l'extension dans chrome://extensions,")
    log("puis supprime ce dossier :")
    log("   %s" % target)
    log("")
    log("Tes videos telechargees ne sont pas touchees.")
    done(None)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def gui(mode="install"):
    import tkinter as tk
    from tkinter import scrolledtext

    uninstalling = mode == "uninstall"

    root = tk.Tk()
    root.title("%s - %s" % (APP, "desinstallation" if uninstalling else "installation"))
    root.configure(bg="#1f1f1f")
    root.geometry("620x460")
    root.resizable(False, False)

    tk.Label(root, text=APP, bg="#1f1f1f", fg="#f1f1f1",
             font=("Segoe UI", 17, "bold")).pack(pady=(20, 2))
    tk.Label(root,
             text="Telechargeur YouTube local. Aucun compte, aucun quota.",
             bg="#1f1f1f", fg="#aaaaaa", font=("Segoe UI", 9)).pack()

    box = scrolledtext.ScrolledText(
        root, height=14, bg="#141414", fg="#d0d0d0", bd=0,
        font=("Consolas", 9), insertbackground="#d0d0d0", wrap="word")
    box.pack(fill="both", expand=True, padx=20, pady=16)
    box.configure(state="disabled")

    def log(line):
        box.configure(state="normal")
        box.insert("end", line + "\n")
        box.see("end")
        box.configure(state="disabled")
        root.update_idletasks()

    bar = tk.Frame(root, bg="#1f1f1f")
    bar.pack(fill="x", padx=20, pady=(0, 18))

    action = tk.Button(
        bar, text="Desinstaller" if uninstalling else "Installer",
        bg="#cc0000" if uninstalling else "#3ea6ff", fg="#0a0a0a",
        font=("Segoe UI", 10, "bold"), relief="flat", padx=22, pady=8,
        cursor="hand2", activebackground="#63b8ff")
    action.pack(side="right")

    def open_folder(path):
        subprocess.Popen(["explorer", os.path.normpath(path)])

    def finished(target):
        # Un bouton grise en fin de parcours donne l'impression d'etre bloque :
        # on en fait un vrai bouton de fermeture.
        action.configure(state="normal", text="Fermer", bg="#3f3f3f",
                         fg="#f1f1f1", activebackground="#4f4f4f",
                         command=root.destroy)
        if not target:
            return

        # Rien ne s'ouvre tout seul : ni Chrome, ni l'explorateur. L'utilisateur
        # decide quand, via le bouton ci-dessous.
        log("")
        log("=" * 58)
        log("DERNIERE ETAPE - a faire une seule fois")
        log("=" * 58)
        log("1. Clique sur 'Ouvrir le dossier' en bas a gauche")
        log("2. Dans Chrome, ouvre :  chrome://extensions")
        log("3. Active 'Mode developpeur', en haut a droite")
        log("4. Glisse le dossier 'extension' dans la page Chrome")
        log("")
        log("Un bouton Telecharger apparaitra alors sur YouTube.")

        tk.Button(bar, text="Ouvrir le dossier",
                  bg="#2f2f2f", fg="#f1f1f1", font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=8, cursor="hand2",
                  command=lambda: open_folder(target)).pack(side="left")

    def go():
        action.configure(state="disabled", text="En cours…")
        job = do_uninstall if uninstalling else do_install
        threading.Thread(
            target=job,
            args=(lambda m: root.after(0, log, m),
                  lambda p: root.after(0, finished, p)),
            daemon=True).start()

    action.configure(command=go)

    if uninstalling:
        log("Retire MediaGrab de cet ordinateur.")
        log("Les videos deja telechargees seront conservees.")
    else:
        log("Sera installe dans :")
        log("   %s" % install_dir())
        log("")
        log("Etapes : ffmpeg, extension Chrome, demarrage automatique.")
        log("Clique sur Installer pour commencer.")

    root.mainloop()


def main():
    args = [a.lower() for a in sys.argv[1:]]
    if "--serve" in args:
        run_server()
    elif "--uninstall" in args:
        gui("uninstall")
    else:
        gui("install")


if __name__ == "__main__":
    main()
