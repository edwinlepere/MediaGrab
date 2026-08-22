"""
MediaGrab - serveur local.

Ecoute uniquement sur 127.0.0.1 et n'accepte que les requetes provenant d'une
extension de navigateur (en-tete Origin: chrome-extension:// ou moz-extension://).
Aucun compte, aucun quota, aucune telemetrie, aucun serveur distant.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import yt_dlp
except ImportError:
    print("yt-dlp est manquant.  Lance :  pip install -U yt-dlp")
    sys.exit(1)

HOST = "127.0.0.1"
PORT = 8787

# Une fois empaquete par PyInstaller, __file__ pointe vers un dossier temporaire
# efface a la fermeture : config.json y serait perdu a chaque redemarrage.
# Il faut alors se referer a l'emplacement reel de l'executable.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "output_dir": os.path.join(os.path.expanduser("~"), "Downloads", "MediaGrab"),
    "ffmpeg_location": "",          # vide = recherche dans le PATH
    "cookies_from_browser": "",     # "chrome" / "firefox" / "edge" : videos privees ou 18+
    "filename_template": "%(title)s [%(id)s].%(ext)s",
    "concurrent_fragments": 4,
}

JOBS = {}
JOBS_LOCK = threading.Lock()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as exc:
            print("config.json illisible (%s), valeurs par defaut utilisees" % exc)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


CONFIG = load_config()


def find_ffmpeg():
    if CONFIG.get("ffmpeg_location"):
        return CONFIG["ffmpeg_location"]
    exe = shutil.which("ffmpeg")
    return os.path.dirname(exe) if exe else ""


# ---------------------------------------------------------------------------
# Construction des options yt-dlp
# ---------------------------------------------------------------------------

def build_format(opts):
    """Traduit les choix de l'interface en selecteur de format yt-dlp."""
    if opts.get("mode", "video") == "audio":
        # "Basse qualite" dans le selecteur de la page : le plus petit flux
        # audio disponible, utile pour un podcast ou une conference.
        if opts.get("audio_pref") == "low":
            return "worstaudio/worst"
        return "bestaudio/best"

    height = opts.get("height")
    container = opts.get("container", "mp4")
    codec_pref = opts.get("codec_pref", "compat")

    # A resolution egale, AV1 pese 3 a 5 fois moins que H.264, VP9 se situe
    # entre les deux. Le prix a payer est la compatibilite : H.264 est lu
    # partout, AV1 exige un lecteur recent. La chaine vide en fin de liste
    # signifie "n'importe quel codec".
    codecs = (["[vcodec^=av01]", "[vcodec^=vp09]", ""] if codec_pref == "small"
              else ["[vcodec^=avc1]", ""])

    # Les parentheses sont indispensables : la chaine complete est assemblee
    # avec des "/", donc un "/" nu dans le selecteur audio creerait une
    # alternative parasite. Sans elles, l'echec d'une branche video fait
    # retomber yt-dlp sur "bestaudio" seul et produit un fichier sans image.
    audio = ("(bestaudio[acodec^=mp4a]/bestaudio)"
             if container == "mp4" and codec_pref != "small" else "bestaudio")

    if not height or height == "best":
        chain = ["bestvideo*%s+%s" % (c, audio) for c in codecs]
        chain.append("best")
        return "/".join(chain)

    h = int(height)
    chain = []

    # 1. La hauteur demandee EXACTEMENT, codec prefere d'abord.
    #    Le "height=" est indispensable : avec un simple "height<=", un filtre
    #    de codec absent a cette resolution est quand meme satisfait par une
    #    resolution inferieure. Demander du 2160p en H.264 renvoyait ainsi du
    #    1080p sans le dire, YouTube ne publiant pas de H.264 au dela de 1080p.
    #    Le dernier element de "codecs" n'ayant aucun filtre, on obtient la
    #    resolution demandee quel que soit son encodage.
    for c in codecs:
        chain.append("bestvideo*[height=%d]%s+%s" % (h, c, audio))

    # 2. Hauteur exacte, mais fichier unique contenant deja le son.
    #    C'est le modele de la plupart des sites hors YouTube. Sans cette
    #    branche, toute source sans piste audio separee voyait echouer chacune
    #    des alternatives ci-dessus et retombait sur la plus basse qualite.
    chain.append("best[height=%d]" % h)
    chain.append("bestvideo*[height=%d]" % h)

    # 3. Cette hauteur n'existe pas : la meilleure sous le plafond demande.
    for c in codecs:
        chain.append("bestvideo*[height<=%d]%s+%s" % (h, c, audio))
    chain.append("best[height<=%d]" % h)
    chain.append("bestvideo*[height<=%d]" % h)

    # 4. Rien en dessous non plus : le plus petit disponible. Surtout pas
    #    "best", qui renverrait le fichier le plus lourd a quelqu'un qui
    #    demandait justement une qualite basse.
    chain.append("worstvideo*+bestaudio/worst")
    chain.append("worst")

    return "/".join(chain)


FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def build_ydl_opts(opts, job_id=None):
    outdir = opts.get("output_dir") or CONFIG["output_dir"]
    os.makedirs(outdir, exist_ok=True)

    # Un manifeste HLS ne porte aucun titre : sans ce repli, le fichier
    # s'appellerait "index" ou "master". Le titre de la page est bien plus
    # parlant, mais il faut le nettoyer des caracteres interdits par Windows.
    template = CONFIG["filename_template"]
    hint = (opts.get("title_hint") or "").strip()
    if hint:
        safe = FORBIDDEN.sub("_", hint).strip(" .")[:120]
        if safe:
            template = safe + ".%(ext)s"

    ydl = {
        "format": build_format(opts),
        "outtmpl": os.path.join(outdir, template),
        "noplaylist": not opts.get("playlist", False),
        "concurrent_fragment_downloads": CONFIG.get("concurrent_fragments", 4),
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 10,
        "postprocessors": [],
    }

    ffmpeg_dir = find_ffmpeg()
    if ffmpeg_dir:
        ydl["ffmpeg_location"] = ffmpeg_dir

    if CONFIG.get("cookies_from_browser"):
        ydl["cookiesfrombrowser"] = (CONFIG["cookies_from_browser"],)

    # Flux repere dans le trafic d'une page : beaucoup de serveurs refusent la
    # requete si elle ne porte pas le Referer de la page qui l'a declenchee.
    if opts.get("referer"):
        ydl["http_headers"] = {"Referer": opts["referer"]}

    if opts.get("mode", "video") == "audio":
        ydl["postprocessors"].append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": opts.get("audio_format", "mp3"),
            "preferredquality": str(opts.get("audio_quality", "192")),
        })
        ydl["postprocessors"].append({"key": "FFmpegMetadata"})
        if opts.get("embed_thumbnail", True):
            ydl["writethumbnail"] = True
            ydl["postprocessors"].append({"key": "EmbedThumbnail"})
    else:
        ydl["merge_output_format"] = opts.get("container", "mp4")
        ydl["postprocessors"].append({"key": "FFmpegMetadata"})

    if opts.get("audio_language"):
        # Piste audio doublee (fonction multi-langue de YouTube)
        ydl["extractor_args"] = {"youtube": {"lang": [opts["audio_language"]]}}

    if opts.get("subtitles"):
        ydl["writesubtitles"] = True
        ydl["writeautomaticsub"] = opts.get("auto_subs", False)
        ydl["subtitleslangs"] = opts.get("sub_langs", ["fr", "en"])
        ydl["subtitlesformat"] = "srt/best"
        ydl["postprocessors"].append({
            "key": "FFmpegSubtitlesConvertor", "format": "srt",
        })

    if opts.get("sponsorblock"):
        ydl["postprocessors"].insert(0, {
            "key": "SponsorBlock",
            "categories": ["sponsor", "selfpromo", "interaction"],
            "when": "after_filter",
        })
        ydl["postprocessors"].append({
            "key": "ModifyChapters",
            "remove_sponsor_segments": ["sponsor", "selfpromo", "interaction"],
        })

    if job_id:
        ydl["progress_hooks"] = [make_progress_hook(job_id)]
        ydl["postprocessor_hooks"] = [make_pp_hook(job_id)]

    return ydl


# ---------------------------------------------------------------------------
# Suivi de progression
# ---------------------------------------------------------------------------

def update_job(job_id, **fields):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def make_progress_hook(job_id):
    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            update_job(
                job_id,
                state="downloading",
                percent=round(done / total * 100, 1) if total else None,
                speed=d.get("speed"),
                eta=d.get("eta"),
                downloaded=done,
                total=total,
            )
        elif status == "finished":
            update_job(job_id, state="processing", percent=100)
        elif status == "error":
            update_job(job_id, state="error", error="echec du telechargement")
    return hook


def make_pp_hook(job_id):
    def hook(d):
        if d.get("status") == "started":
            update_job(job_id, state="processing", stage=d.get("postprocessor"))
    return hook


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run_job(job_id, url, opts):
    try:
        with yt_dlp.YoutubeDL(build_ydl_opts(opts, job_id)) as ydl:
            info = ydl.extract_info(url, download=True)
            if "entries" in info:
                entries = [e for e in info.get("entries") or [] if e]
                info = entries[0] if entries else info

            path = None
            requested = info.get("requested_downloads") or []
            if requested:
                path = requested[0].get("filepath")
            if not path:
                path = ydl.prepare_filename(info)

            update_job(job_id, state="done", percent=100,
                       filepath=path, title=info.get("title"))
    except Exception as exc:
        update_job(job_id, state="error", error=ANSI.sub("", str(exc))[:500])


# ---------------------------------------------------------------------------
# Couche HTTP
# ---------------------------------------------------------------------------

ALLOWED_ORIGIN_PREFIXES = (
    "chrome-extension://", "moz-extension://", "safari-web-extension://",
)


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaGrab/1.0"

    def log_message(self, fmt, *args):
        pass  # console silencieuse

    # -- utilitaires --------------------------------------------------------
    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # appel direct (curl, script local) : pas une page web
        return origin.startswith(ALLOWED_ORIGIN_PREFIXES)

    def _send(self, code, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        origin = self.headers.get("Origin", "*")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routes -------------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        if not self._origin_ok():
            return self._send(403, {"error": "origine refusee"})

        parts = urlparse(self.path)
        query = parse_qs(parts.query)

        if parts.path == "/":
            # Page d'accueil lisible : ouvrir 127.0.0.1:8787 dans un navigateur
            # est le premier reflexe pour verifier que le serveur tourne.
            return self._send_html(status_page())

        if parts.path == "/health":
            return self._send(200, {
                "ok": True,
                "version": "1.0.0",
                "yt_dlp": yt_dlp.version.__version__,
                "ffmpeg": bool(find_ffmpeg()),
                "output_dir": CONFIG["output_dir"],
            })

        if parts.path == "/progress":
            with JOBS_LOCK:
                job = JOBS.get(query.get("id", [""])[0])
            if not job:
                return self._send(404, {"error": "tache inconnue"})
            return self._send(200, job)

        if parts.path == "/jobs":
            with JOBS_LOCK:
                return self._send(200, {"jobs": list(JOBS.values())})

        if parts.path == "/config":
            return self._send(200, CONFIG)

        return self._send(404, {"error": "route inconnue"})

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {"error": "origine refusee"})

        parts = urlparse(self.path)
        try:
            data = self._body()
        except Exception:
            return self._send(400, {"error": "JSON invalide"})

        if parts.path == "/supported":
            url = data.get("url", "")
            if not url:
                return self._send(400, {"error": "url manquante"})
            name = supported_by(url)
            return self._send(200, {
                "extractor": name,
                "generic": name is None,
                "site": urlparse(url).hostname or "",
            })

        if parts.path == "/info":
            url = data.get("url", "")
            if not url:
                return self._send(400, {"error": "url manquante"})
            try:
                opts = {"quiet": True, "no_warnings": True, "skip_download": True}
                if CONFIG.get("cookies_from_browser"):
                    opts["cookiesfrombrowser"] = (CONFIG["cookies_from_browser"],)
                # Analyser un manifeste HLS repere dans le trafic exige le
                # Referer de sa page : sans lui le serveur refuse la requete.
                if data.get("referer"):
                    opts["http_headers"] = {"Referer": data["referer"]}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    # summarize interroge le selecteur de ydl : il doit rester
                    # dans le bloc "with", tant que l'instance est vivante.
                    payload = summarize(info,
                                        data.get("codec_pref", "compat"),
                                        ydl,
                                        data.get("container", "mp4"))
                return self._send(200, payload)
            except Exception as exc:
                return self._send(500, {"error": ANSI.sub("", str(exc))[:400]})

        if parts.path == "/download":
            url = data.get("url", "")
            if not url:
                return self._send(400, {"error": "url manquante"})
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "id": job_id, "url": url, "state": "queued", "percent": 0,
                    "title": data.get("title") or url, "created": time.time(),
                }
            threading.Thread(target=run_job, args=(job_id, url, data),
                             daemon=True).start()
            return self._send(200, {"id": job_id})

        if parts.path == "/open":
            path = data.get("path") or CONFIG["output_dir"]
            try:
                open_in_file_manager(path)
                return self._send(200, {"ok": True})
            except Exception as exc:
                return self._send(500, {"error": str(exc)})

        if parts.path == "/config":
            CONFIG.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
            save_config(CONFIG)
            return self._send(200, CONFIG)

        return self._send(404, {"error": "route inconnue"})


def open_in_file_manager(path):
    path = os.path.normpath(path)
    is_file = os.path.isfile(path)
    if sys.platform == "win32":
        if is_file:
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if is_file else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if is_file else path])


def status_page():
    ffmpeg_dir = find_ffmpeg()
    with JOBS_LOCK:
        active = sum(1 for j in JOBS.values()
                     if j.get("state") in ("queued", "downloading", "processing"))

    def row(label, value, ok=True):
        color = "#0f9d58" if ok else "#cc0000"
        return ("<tr><th>%s</th><td style='color:%s'>%s</td></tr>"
                % (label, color, value))

    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>MediaGrab</title>
<style>
 body{font:14px/1.6 "Segoe UI",system-ui,sans-serif;background:#212121;color:#f1f1f1;
      display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center}
 .card{background:#2c2c2c;border-radius:12px;padding:32px 36px;max-width:520px;
       box-shadow:0 8px 32px rgba(0,0,0,.4)}
 h1{margin:0 0 4px;font-size:20px}
 .sub{margin:0 0 20px;color:#aaa}
 table{border-collapse:collapse;width:100%%;margin-bottom:20px}
 th{text-align:left;font-weight:400;color:#aaa;padding:5px 16px 5px 0;white-space:nowrap}
 td{padding:5px 0}
 .ok{display:inline-block;width:9px;height:9px;border-radius:50%%;background:#0f9d58;
     margin-right:8px;vertical-align:middle}
 p.note{margin:0;padding:14px 16px;background:#1a1a1a;border-radius:8px;
        border-left:3px solid #cc0000;color:#ccc}
 code{background:rgba(255,255,255,.1);padding:2px 5px;border-radius:3px;font-size:13px}
</style></head><body><div class="card">
<h1><span class="ok"></span>MediaGrab</h1>
<p class="sub">Le serveur local fonctionne.</p>
<table>
%s%s%s%s
</table>
<p class="note">Cette page ne sert qu'a verifier l'etat du serveur.
Le telechargement se pilote depuis l'extension Chrome&nbsp;: ouvre une video
YouTube et utilise le bouton <strong>Telecharger</strong>, ou l'icone rouge
dans la barre d'outils.</p>
</div></body></html>""" % (
        row("yt-dlp", yt_dlp.version.__version__),
        row("ffmpeg", ffmpeg_dir or "INTROUVABLE", bool(ffmpeg_dir)),
        row("destination", CONFIG["output_dir"]),
        row("taches en cours", str(active)),
    )


_EXTRACTORS = None


def supported_by(url):
    """Nom de l'extracteur dedie pour cette URL, sinon None.

    Purement local : on interroge les motifs des 1700+ extracteurs, aucune
    requete reseau. Sert a savoir instantanement si une page est telechargeable
    avant de lancer l'analyse complete, bien plus lente.
    Retourner None ne veut pas dire "impossible" : l'extracteur generic sait
    encore trouver une balise video, un flux HLS ou DASH dans la page.
    """
    global _EXTRACTORS
    if _EXTRACTORS is None:
        from yt_dlp.extractor import gen_extractor_classes
        _EXTRACTORS = [ie for ie in gen_extractor_classes()
                       if ie.IE_NAME != "generic"]
    for ie in _EXTRACTORS:
        try:
            if ie.suitable(url):
                return ie.IE_NAME
        except Exception:
            continue
    return None


def _size(f):
    return f.get("filesize") or f.get("filesize_approx") or 0


def _size_or_estimate(f, duration):
    """(octets, estime).

    YouTube ne renseigne pas filesize sur les flux DASH : on retombe sur
    le debit moyen tbr (kbit/s) multiplie par la duree.
    tbr * 1000 / 8 = tbr * 125 octets par seconde.
    """
    exact = _size(f)
    if exact:
        return exact, False
    tbr = f.get("tbr")
    if tbr and duration:
        return int(tbr * 125 * duration), True
    return 0, False


def _codec_family(f):
    v = f.get("vcodec") or ""
    if v.startswith("av01"):
        return "AV1"
    if v.startswith("vp09") or v.startswith("vp9"):
        return "VP9"
    if v.startswith("avc1"):
        return "H.264"
    # Beaucoup de sites ne declarent aucun codec : mieux vaut ne rien afficher
    # qu'un point d'interrogation.
    return v.split(".")[0] if v else ""


def _codec_rank(f, pref):
    """Ordre de preference entre codecs, a hauteur d'image egale."""
    family = _codec_family(f)
    if pref == "small":
        # AV1 le plus compact, puis VP9, H.264 en dernier recours
        return {"AV1": 3, "VP9": 2, "H.264": 1}.get(family, 0)
    # Par defaut : compatibilite maximale
    return {"H.264": 3, "VP9": 2, "AV1": 1}.get(family, 0)


def _selected_formats(ydl, info, opts):
    """Les formats que yt-dlp retiendra reellement pour ces options.

    On interroge son propre selecteur plutot que de reimplementer sa logique
    de tri : sinon la taille annoncee finit par diverger du fichier obtenu.
    """
    try:
        selector = ydl.build_format_selector(build_format(opts))
        matches = list(selector({"formats": info.get("formats") or [],
                                 "incomplete_formats": {}}))
    except Exception:
        return []
    if not matches:
        return []
    chosen = matches[0]
    return chosen.get("requested_formats") or [chosen]


def build_options(info, codec_pref="compat", ydl=None, container="mp4"):
    """Liste des choix proposes dans le selecteur de la page YouTube.

    Une entree par hauteur d'image, plus deux entrees audio. Les poids
    annonces pour la video incluent la piste audio qui sera fusionnee,
    sinon le chiffre affiche serait trompeur (les flux DASH sont separes).
    """
    formats = info.get("formats") or []
    duration = info.get("duration")

    # Distinction essentielle hors YouTube : la chaine "none" signifie que le
    # site declare explicitement l'absence de piste, tandis que None signifie
    # qu'il ne declare rien. Confondre les deux revenait a prendre tous les
    # formats d'un site qui ne renseigne pas ses codecs pour de l'audio pur.
    audio_only = [f for f in formats
                  if f.get("vcodec") == "none"
                  and f.get("acodec") not in (None, "none")]
    audio_only.sort(key=lambda f: (f.get("abr") or 0, _size(f)))

    best_audio = audio_only[-1] if audio_only else None
    worst_audio = audio_only[0] if audio_only else None
    audio_size, audio_est = (_size_or_estimate(best_audio, duration)
                             if best_audio else (0, False))

    # Toute entree ayant une hauteur d'image est un candidat video, meme sans
    # vcodec renseigne. Exiger un codec declare revenait a ne rien proposer sur
    # la plupart des sites, qui servent un fichier unique progressif la ou
    # YouTube sert des flux DASH separes.
    video_only = [f for f in formats
                  if f.get("height") and f.get("vcodec") != "none"]

    # Une seule entree par hauteur : on garde le meilleur candidat, en
    # privilegiant h264 (compatible partout) puis le debit le plus eleve.
    per_height = {}
    for f in video_only:
        height = f["height"]
        score = (_codec_rank(f, codec_pref), f.get("tbr") or 0)
        if height not in per_height or score > per_height[height][0]:
            per_height[height] = (score, f)

    options = []
    seen_heights = set()

    for height in sorted(per_height, reverse=True):
        fallback = per_height[height][1]

        # Source de verite : le selecteur de yt-dlp, avec exactement les
        # options qui seront utilisees au telechargement.
        picked = _selected_formats(ydl, info, {
            "mode": "video", "height": str(height),
            "container": container, "codec_pref": codec_pref,
        }) if ydl else []

        if picked:
            total, estimated = 0, False
            for f in picked:
                size, est = _size_or_estimate(f, duration)
                total += size
                estimated = estimated or est
            video = next((f for f in picked if f.get("height")), picked[0])
        else:
            size, estimated = _size_or_estimate(fallback, duration)
            total = size + audio_size
            estimated = estimated or audio_est
            video = fallback

        # YouTube ne publie pas de H.264 au dela de 1080p : en mode
        # compatibilite, demander du 2160p renvoie en realite du 1080p.
        # On annonce donc la hauteur reellement obtenue, et on fusionne les
        # entrees qui aboutissent au meme fichier - promettre une resolution
        # qu'on ne peut pas livrer serait mensonger.
        actual_height = video.get("height") or height
        if actual_height in seen_heights:
            continue
        seen_heights.add(actual_height)

        options.append({
            "kind": "video",
            "height": actual_height,
            "width": video.get("width") or fallback.get("width"),
            "fps": round(video["fps"]) if video.get("fps") else None,
            "filesize": total or None,
            "estimated": estimated,
            "codec": _codec_family(video),
            "hdr": "hdr" in str(video.get("dynamic_range") or "").lower(),
        })

    for label, f in (("high", best_audio), ("low", worst_audio)):
        if f and (label == "high" or f is not best_audio):
            size, est = _size_or_estimate(f, duration)
            options.append({
                "kind": "audio",
                "pref": label,
                "abr": round(f["abr"]) if f.get("abr") else None,
                "filesize": size or None,
                "estimated": est,
            })

    # Aucune piste audio separee : ffmpeg sait tout de meme extraire le son du
    # fichier progressif. On propose donc l'entree, sans annoncer de taille
    # puisqu'elle depend du reencodage.
    if not audio_only and formats:
        options.append({
            "kind": "audio", "pref": "high",
            "abr": None, "filesize": None, "estimated": False,
        })

    return options


def summarize(info, codec_pref="compat", ydl=None, container="mp4"):
    """Ne renvoie a l'interface que l'utile, pas les 2 Mo de JSON de yt-dlp."""
    if "entries" in info:
        entries = [e for e in info.get("entries") or [] if e]
        first = entries[0] if entries else {}
        return {
            "playlist": True,
            "title": info.get("title"),
            "count": len(entries),
            "items": [{"id": e.get("id"), "title": e.get("title"),
                       "duration": e.get("duration")} for e in entries[:100]],
            "heights": sorted({f.get("height") for f in (first.get("formats") or [])
                               if f.get("height")}, reverse=True),
            "audio_languages": [],
        }

    heights = sorted({f.get("height") for f in (info.get("formats") or [])
                      if f.get("height")}, reverse=True)

    languages = {}
    for f in info.get("formats") or []:
        if f.get("acodec") not in (None, "none") and f.get("language"):
            languages[f["language"]] = f.get("format_note") or f["language"]

    return {
        "playlist": False,
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "is_live": info.get("is_live", False),
        "heights": heights,
        "options": build_options(info, codec_pref, ydl, container),
        "audio_languages": [{"code": k, "label": v}
                            for k, v in sorted(languages.items())],
        "filesize_hint": info.get("filesize_approx"),
    }


def cleanup_loop():
    """Purge les taches terminees depuis plus d'une heure."""
    while True:
        time.sleep(600)
        cutoff = time.time() - 3600
        with JOBS_LOCK:
            stale = [j for j, v in JOBS.items()
                     if v.get("state") in ("done", "error")
                     and v.get("created", 0) < cutoff]
            for job_id in stale:
                del JOBS[job_id]


def log_fatal(message):
    """Lance par pythonw, le serveur n'a pas de console : sans ce fichier,
    une erreur de demarrage serait totalement invisible."""
    line = "%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    try:
        with open(os.path.join(BASE_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(message)


def port_taken(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def main():
    port = PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    # Une seule instance a la fois. Sans ce garde-fou, un second lancement
    # echoue sur le bind et laisse un processus fantome invisible.
    if port_taken(port):
        log_fatal("Le port %d est deja utilise : une autre instance de "
                  "MediaGrab tourne probablement deja. Arret." % port)
        sys.exit(1)

    threading.Thread(target=cleanup_loop, daemon=True).start()

    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        log_fatal("Impossible d'ouvrir le port %d : %s" % (port, exc))
        sys.exit(1)

    print("=" * 60)
    print("  MediaGrab - serveur local pret")
    print("=" * 60)
    print("  adresse     : http://%s:%d" % (HOST, port))
    print("  yt-dlp      : %s" % yt_dlp.version.__version__)
    print("  ffmpeg      : %s" % (find_ffmpeg() or "INTROUVABLE - installe-le"))
    print("  destination : %s" % CONFIG["output_dir"])
    print()
    print("  Laisse cette fenetre ouverte. Ctrl+C pour arreter.")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\narret.")


if __name__ == "__main__":
    main()
