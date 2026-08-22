"""Genere les captures de presentation du projet.

Reprend les feuilles de style reelles de l'extension, avec du contenu neutre
et sous licence libre. Les images sont produites par Chrome en mode headless,
puis deposees dans public/screenshots/.
"""
import base64
import io
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "extension")
OUT = os.path.join(ROOT, "public", "screenshots")
TMP = os.path.join(os.environ.get("TEMP", "."), "mediagrab_shots")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    found = shutil.which("chrome") or shutil.which("msedge")
    if not found:
        sys.exit("Chrome introuvable : impossible de produire les captures.")
    return found


def css(name):
    with open(os.path.join(EXT, name), encoding="utf-8") as f:
        return f.read()


def trim(path, margin=24):
    """Recadre sur le contenu.

    On dimensionne les fenetres genereusement pour ne rien tronquer, ce qui
    laisse une large bande vide en bas. On la retire en cherchant la boite
    englobante de tout ce qui differe de la couleur de fond.
    """
    img = Image.open(path).convert("RGB")
    background = img.getpixel((1, 1))
    mask = Image.new("L", img.size, 0)
    pixels = img.load()
    mask_px = mask.load()

    # Tolerance : le lissage des polices cree des pixels tres proches du fond
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = pixels[x, y]
            if (abs(r - background[0]) + abs(g - background[1])
                    + abs(b - background[2])) > 12:
                mask_px[x, y] = 255

    box = mask.getbbox()
    if not box:
        return img.size

    left = max(0, box[0] - margin)
    top = max(0, box[1] - margin)
    right = min(img.width, box[2] + margin)
    bottom = min(img.height, box[3] + margin)
    cropped = img.crop((left, top, right, bottom))
    cropped.save(path)
    return cropped.size


def data_uri(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def thumbnail(w=640, h=360):
    """Vignette neutre : un degrade et une montagne, aucun contenu reel."""
    img = Image.new("RGB", (w, h), (44, 62, 84))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)],
               fill=(int(58 + 60 * t), int(92 + 70 * t), int(130 + 40 * t)))
    d.polygon([(0, h), (w * 0.32, h * 0.42), (w * 0.60, h)], fill=(38, 74, 66))
    d.polygon([(w * 0.42, h), (w * 0.72, h * 0.30), (w, h)], fill=(30, 60, 54))
    d.ellipse([w * 0.74, h * 0.12, w * 0.86, h * 0.30], fill=(248, 226, 160))
    return img


# ---------------------------------------------------------------------------
# Gabarits
# ---------------------------------------------------------------------------

SHELL = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<style>
  html,body{margin:0;padding:0}
  body{background:#212121}
  %s
</style></head><body>%s</body></html>"""


def head():
    return """<header class="head">
      <h1>MediaGrab</h1><span class="status status--on"></span>
    </header>"""


def video_card(thumb):
    return """<section class="video">
      <img src="%s" alt="">
      <div class="video__meta">
        <p class="video__title">Big Buck Bunny 60fps 4K</p>
        <p class="video__sub">Blender Foundation &middot; 10:35</p>
      </div>
    </section>""" % thumb


def tabs(active):
    return """<div class="tabs">
      <button class="tab%s">Video</button>
      <button class="tab%s">Audio</button>
    </div>""" % (" is-active" if active == "video" else "",
                 " is-active" if active == "audio" else "")


def field(label, options, selected=0):
    opts = "".join(
        '<option%s>%s</option>' % (" selected" if i == selected else "", o)
        for i, o in enumerate(options))
    return """<label class="field"><span>%s</span>
      <select>%s</select></label>""" % (label, opts)


def checks(items):
    return "".join(
        '<label class="check"><input type="checkbox"%s><span>%s</span></label>'
        % (" checked" if on else "", text) for text, on in items)


EXTRA = [
    ("Sous-titres (.srt)", False),
    ("Couper les segments sponsorises", False),
    ("Telecharger toute la playlist", False),
]

FOOT = """<footer class="foot">
  <button class="link">Ouvrir le dossier</button>
  <button class="link">Reglages</button>
</footer>"""


def shot_video(thumb):
    body = """<main>%s%s%s
      <section class="opts">%s%s%s</section>
      <section class="opts opts--extra">%s</section>
      <button class="btn btn--primary btn--wide">Telecharger</button>%s
    </main>""" % (
        head(), video_card(thumb), tabs("video"),
        field("Qualite", ["2160p &mdash; 60FPS &middot; ~689 Mo",
                          "1440p &mdash; 60FPS &middot; ~308 Mo",
                          "1080p &mdash; 60FPS &middot; ~255 Mo"]),
        field("Conteneur", ["MP4 (compatible partout)", "MKV (garde AV1/VP9)"]),
        field("Langue audio", ["Langue par defaut"]),
        checks(EXTRA), FOOT)
    return SHELL % (css("popup.css") + "\nbody{width:340px;padding:12px}", body)


def shot_audio(thumb):
    body = """<main>%s%s%s
      <section class="opts">%s%s%s</section>
      <section class="opts opts--extra">%s</section>
      <button class="btn btn--primary btn--wide">Telecharger</button>%s
    </main>""" % (
        head(), video_card(thumb), tabs("audio"),
        field("Format", ["MP3", "M4A (AAC)", "Opus", "FLAC (sans perte)"]),
        field("Debit", ["320 kbps", "192 kbps", "128 kbps"]),
        checks([("Integrer la miniature comme pochette", True)]),
        checks(EXTRA), FOOT)
    return SHELL % (css("popup.css") + "\nbody{width:340px;padding:12px}", body)


def shot_settings():
    with open(os.path.join(EXT, "options.html"), encoding="utf-8") as f:
        page = f.read()

    # On ne garde que le corps, et on remplace la feuille externe par son
    # contenu : Chrome en mode fichier ne resout pas les chemins relatifs.
    body = page.split("<body>", 1)[1].split("</body>", 1)[0]
    inline = page.split("<style>", 1)[1].split("</style>", 1)[0]

    remplacements = [
        ('id="output_dir" class="input" type="text" spellcheck="false"',
         'id="output_dir" class="input" type="text" value="C:\\Users\\Public\\Downloads\\MediaGrab"'),
        ('id="filename_template" class="input" type="text" spellcheck="false"',
         'id="filename_template" class="input" type="text" value="%(title)s [%(id)s].%(ext)s"'),
        ('id="ffmpeg_location" class="input" type="text" spellcheck="false"',
         'id="ffmpeg_location" class="input" type="text" value="C:\\ffmpeg\\bin"'),
        ('<input type="checkbox" id="showPageButton">',
         '<input type="checkbox" id="showPageButton" checked>'),
        ('<input type="checkbox" id="notify">',
         '<input type="checkbox" id="notify" checked>'),
        ('class="status status--unknown"', 'class="status status--on"'),
    ]
    for old, new in remplacements:
        body = body.replace(old, new)
    body = body.replace('<script src="options.js"></script>', "")

    return SHELL % (css("popup.css") + inline + "\nbody{background:#212121}", body)


def shot_bar():
    """La barre injectee sous le lecteur, menu deroule."""
    rows = [
        ("2160p", "4K", "3840x2160 - 60FPS - ~689 Mo", "uhd", False),
        ("1440p", "2K", "2560x1440 - 60FPS - ~308 Mo", "uhd", False),
        ("1080p", "HD", "1920x1080 - 60FPS - ~255 Mo", "hd", True),
        ("720p", "", "1280x720 - 60FPS - ~153 Mo", "", False),
        ("480p", "", "854x480 - 30FPS - ~37 Mo", "", False),
    ]
    items = ""
    for chip, tag, text, kind, active in rows:
        items += ('<button class="ytg-item%s"><span class="ytg-chip%s">%s%s</span>'
                  '<span class="ytg-text">%s</span></button>') % (
            " is-active" if active else "",
            " ytg-chip--" + kind if kind else "", chip,
            "<sup>%s</sup>" % tag if tag else "", text)
    items += '<div class="ytg-sep"></div>'
    for pref, text in (("high", "Haute qualite - 9.80 Mo"),
                       ("low", "Basse qualite - 3.69 Mo")):
        items += ('<button class="ytg-item"><span class="ytg-chip">Audio</span>'
                  '<span class="ytg-text">%s</span>'
                  '<span class="ytg-note">&#9834;</span></button>') % text

    chevron = ('<svg viewBox="0 0 24 24" width="18" height="18"><path '
               'fill="currentColor" d="M7.4 15.4 12 10.8l4.6 4.6 1.4-1.4-6-6-6 6z"/></svg>')
    kebab = ('<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor"'
             ' d="M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0'
             ' 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"/></svg>')

    # Le menu s'ouvre vers le haut et sort du flux : la marge doit etre en
    # haut, sinon il deborde hors du cadre et la capture ne montre que la barre.
    body = """<div style="padding:360px 26px 26px">
      <div class="ytg-wrap"><div class="ytg-bar">
        <button class="ytg-picker" aria-expanded="true">
          <span class="ytg-chip ytg-chip--hd">1080p<sup>HD</sup></span>
          <span class="ytg-text">1920x1080 - 60FPS - ~255 Mo</span>
          <span class="ytg-chev">%s</span></button>
        <button class="ytg-download">Telecharger</button>
        <button class="ytg-kebab">%s</button>
        <div class="ytg-menu">%s</div>
      </div></div>
    </div>""" % (chevron, kebab, items)

    return SHELL % (css("content.css") + "\nbody{background:#0f0f0f}", body)


# ---------------------------------------------------------------------------

SHOTS = [
    ("1-formats-video", shot_video, (380, 620)),
    ("2-extraction-mp3", shot_audio, (380, 590)),
    ("3-reglages", shot_settings, (700, 1080)),
    ("4-barre-youtube", shot_bar, (560, 560)),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    thumb = data_uri(thumbnail())
    exe = chrome()

    for name, builder, (w, h) in SHOTS:
        html = builder(thumb) if builder in (shot_video, shot_audio) else builder()
        src = os.path.join(TMP, name + ".html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(html)

        dest = os.path.join(OUT, name + ".png")
        subprocess.run([
            exe, "--headless", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=2",       # rendu net sur ecran haute densite
            "--default-background-color=00000000",
            "--screenshot=" + dest,
            "--window-size=%d,%d" % (w, h),
            src,
        ], capture_output=True)

        if os.path.exists(dest):
            size = trim(dest)
            print("  %-22s %sx%s  %d ko" % (name + ".png", size[0], size[1],
                                            os.path.getsize(dest) // 1024))
        else:
            print("  %-22s ECHEC" % (name + ".png"))

    print("\nCaptures dans %s" % OUT)


if __name__ == "__main__":
    main()
