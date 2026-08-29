/** Interface principale. Aucune logique reseau ici : tout passe par le worker. */

const $ = (id) => document.getElementById(id);
const send = (msg) =>
  new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));

let currentUrl = null;
// Un flux repere dans le trafic ne se telecharge qu'avec le Referer de sa
// page ; son titre vient de l'onglet, le manifeste n'en portant aucun.
let currentReferer = null;
let currentTitle = "";
let currentMode = "video";
let currentIsLive = false;

// --- persistance des reglages ----------------------------------------------

const CONTROLS = {
  height: "height",
  container: "container",
  audio_format: "audioFormat",
  audio_quality: "audioQuality",
  embed_thumbnail: "embedThumbnail",
  subtitles: "subtitles",
  sponsorblock: "sponsorblock",
  playlist: "playlist",
};

async function restoreSettings() {
  const { data } = await send({ type: "getSettings" });
  if (!data) return;

  currentMode = data.mode || "video";
  applyMode(currentMode);

  for (const [key, id] of Object.entries(CONTROLS)) {
    const el = $(id);
    if (!el || data[key] === undefined) continue;
    if (el.type === "checkbox") el.checked = Boolean(data[key]);
    else el.value = String(data[key]);
  }
}

function collect() {
  const out = { mode: currentMode };
  for (const [key, id] of Object.entries(CONTROLS)) {
    const el = $(id);
    if (!el) continue;
    out[key] = el.type === "checkbox" ? el.checked : el.value;
  }
  return out;
}

function persist() {
  chrome.storage.sync.set(collect());
}

// --- onglets video / audio --------------------------------------------------

function applyMode(mode) {
  currentMode = mode;
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("is-active", t.dataset.mode === mode)
  );
  $("opts-video").hidden = mode !== "video";
  $("opts-audio").hidden = mode !== "audio";
}

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    applyMode(tab.dataset.mode);
    persist();
  })
);

document.querySelectorAll("select, input").forEach((el) =>
  el.addEventListener("change", persist)
);

// --- etat du serveur --------------------------------------------------------

async function checkHealth() {
  const { data } = await send({ type: "health" });
  const running = Boolean(data?.running);

  $("status").className = `status status--${running ? "on" : "off"}`;
  $("status").title = running
    ? `yt-dlp ${data.yt_dlp}${data.ffmpeg ? "" : " — ffmpeg introuvable !"}`
    : "serveur local arrete";
  $("offline").hidden = running;
  $("main").hidden = !running;

  return running;
}

$("retry").addEventListener("click", init);

// --- video active -----------------------------------------------------------

/** Seules les pages internes du navigateur sont hors de portee : partout
 *  ailleurs, yt-dlp a soit un extracteur dedie (plus de 1700 sites), soit
 *  son extracteur generic qui inspecte la page. */
function isWebPage(url) {
  return /^https?:\/\//.test(url || "");
}

/** Repli quand yt-dlp ne trouve rien : les flux vus passer dans le trafic.
 *  C'est le seul moyen d'attraper une video chargee en JavaScript apres coup,
 *  invisible dans le HTML que yt-dlp analyse. */
async function showStreams() {
  const { data } = await send({ type: "streams" });
  const found = data?.streams || [];
  const list = $("streamList");

  $("streams").hidden = false;
  $("video").hidden = true;
  list.innerHTML = "";

  if (!found.length) {
    $("streamsHead").textContent =
      "Aucune video detectee. Lance la lecture, puis rouvre ce menu.";
    return;
  }

  currentReferer = data.pageUrl;
  currentTitle = data.pageTitle || "";

  // Une URL brute ne dit ni la duree, ni les qualites disponibles. On confie
  // donc le manifeste a yt-dlp, qui sait le lire, et on presente le resultat
  // comme n'importe quelle autre video. Les mieux classes d'abord : un
  // manifeste maitre decrit toutes les qualites, une variante une seule.
  $("streamsHead").textContent = "Analyse du flux detecte…";

  const failures = [];
  for (const s of found.slice(0, 3)) {
    const probe = await send({ type: "info", url: s.url, referer: data.pageUrl });
    if (!probe?.error && probe?.data) {
      currentUrl = s.url;
      renderInfo(probe.data, currentTitle, data.poster);
      return;
    }
    failures.push({ stream: s, message: probe?.error || "flux illisible" });
  }

  // Aucun flux exploitable : on montre lesquels ont ete vus et pourquoi
  // chacun a echoue, plutot qu'un "erreur" sans explication.
  $("streamsHead").textContent =
    `${found.length} flux vu${found.length > 1 ? "s" : ""}, aucun exploitable :`;

  for (const { stream, message } of failures) {
    const row = document.createElement("div");
    row.className = "stream stream--dead";

    const tag = document.createElement("span");
    tag.className = `stream__tag stream__tag--${stream.kind.toLowerCase()}`;
    tag.textContent = stream.kind;

    const name = document.createElement("span");
    name.className = "stream__name";
    name.textContent = message;
    name.title = `${stream.url}\n\n${message}`;

    row.append(tag, name);
    list.appendChild(row);
  }
}

async function loadCurrentVideo() {
  currentIsLive = false;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (!isWebPage(tab?.url)) {
    showStreams();
    return;
  }

  currentUrl = tab.url;

  // Test local instantane : il evite d'afficher "analyse en cours" pendant
  // plusieurs secondes sur une page qui n'a manifestement aucune video.
  const { data } = await send({ type: "supported", url: currentUrl });
  if (data?.extractor) {
    $("uploader").textContent = data.extractor;
  }

  fetchInfo(currentUrl);
}

function humanSize(bytes) {
  if (!bytes) return null;
  const mb = bytes / 1048576;
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} Go`;
  if (mb >= 1) return `${mb.toFixed(0)} Mo`;
  return `${(bytes / 1024).toFixed(0)} Ko`;
}

async function fetchInfo(url) {
  $("title").textContent = "Analyse de la page…";
  $("video").hidden = false;

  const { data, error } = await send({ type: "info", url });

  if (error || !data) {
    // yt-dlp n'a rien trouve dans le HTML : on bascule sur ce que le trafic
    // reseau a revele, ce qui rattrape les lecteurs charges en JavaScript.
    $("video").hidden = true;
    showStreams();
    return;
  }

  renderInfo(data);
}

/** Remplit la fiche video. Partage entre l'analyse directe de la page et
 *  celle d'un flux repere dans le trafic, qui doivent aboutir au meme ecran. */
function renderInfo(data, titleOverride, poster) {
  $("video").hidden = false;
  $("streams").hidden = true;

  // Priorite au titre de l'onglet pour un flux repere dans le trafic :
  // yt-dlp nomme le manifeste d'apres son URL, ce qui donne "master" ou
  // "index". Non vide, ce titre gagnait sur un simple repli.
  $("title").textContent = titleOverride || data.title || "Video";
  $("uploader").textContent = [
    data.uploader,
    data.duration ? formatDuration(data.duration) : null,
    data.playlist ? `playlist — ${data.count} videos` : null,
  ].filter(Boolean).join(" · ");

  // Vignette de la source si elle existe, sinon celle relevee dans la page.
  // Un manifeste HLS n'en porte aucune, d'ou la cascade cote page. Si les
  // deux echouent, on retire l'element plutot que de laisser un cadre vide.
  const image = data.thumbnail || poster || "";
  $("thumb").hidden = !image;
  if (image) {
    // L'adresse vient d'une page quelconque : elle peut etre morte, protegee
    // par un referer, ou disparaitre. Mieux vaut alors ne rien afficher que
    // laisser l'icone d'image brisee du navigateur.
    $("thumb").onerror = () => {
      $("thumb").hidden = true;
    };
    $("thumb").src = image;
  }

  // Memes intitules que la barre injectee dans la page : resolution,
  // images par seconde et poids, plutot qu'un simple "1080p".
  const videos = (data.options || []).filter((o) => o.kind === "video");
  if (videos.length) {
    const select = $("height");
    const wanted = select.value;
    select.innerHTML = '<option value="best">La meilleure disponible</option>';
    for (const o of videos) {
      const size = humanSize(o.filesize);
      const detail = [
        o.fps ? `${o.fps}FPS` : null,
        size ? `${o.estimated ? "~" : ""}${size}` : null,
      ].filter(Boolean).join(" · ");
      const opt = document.createElement("option");
      opt.value = String(o.height);
      opt.textContent = detail ? `${o.height}p — ${detail}` : `${o.height}p`;
      select.appendChild(opt);
    }
    select.value = videos.some((o) => String(o.height) === wanted)
      ? wanted
      : "best";
  }

  // Pistes audio doublees, quand la video en propose
  if (data.audio_languages?.length > 1) {
    const select = $("audioLanguage");
    select.innerHTML = '<option value="">Langue par defaut</option>';
    for (const lang of data.audio_languages) {
      const opt = document.createElement("option");
      opt.value = lang.code;
      opt.textContent = lang.label;
      select.appendChild(opt);
    }
    $("langField").hidden = false;
  }

  currentIsLive = !!data.is_live;
  if (data.is_live) {
    $("go").textContent = "Enregistrer le direct";
  }
}

function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

// --- nom du fichier ---------------------------------------------------------

/** Entree valide au lieu d'inserer un saut de ligne : le champ sert a nommer
 *  un fichier, une valeur sur plusieurs lignes n'aurait aucun sens. */
$("title").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("title").blur();
  }
});

// Un collage depuis une page web arrive avec sa mise en forme : on ne garde
// que le texte brut.
$("title").addEventListener("paste", (e) => {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData("text");
  document.execCommand("insertText", false, text.replace(/\s+/g, " ").trim());
});

function chosenTitle() {
  return $("title").textContent.replace(/\s+/g, " ").trim();
}

// --- lancement --------------------------------------------------------------

$("go").addEventListener("click", async () => {
  if (!currentUrl) return;

  $("go").disabled = true;
  const overrides = collect();
  const lang = $("audioLanguage").value;
  if (lang) overrides.audio_language = lang;

  const { error } = await send({
    type: "download",
    url: currentUrl,
    title: chosenTitle() || currentTitle,
    referer: currentReferer,
    // Uniquement pour un flux repere dans le trafic, ou quand le nom a ete
    // modifie a la main : ailleurs, le titre extrait par yt-dlp est meilleur.
    titleHint: currentReferer ? (chosenTitle() || currentTitle) : null,
    is_live: currentIsLive,
    overrides,
  });

  $("go").disabled = false;
  if (error) alert(error);
});

// --- file d'attente ---------------------------------------------------------

const LABELS = {
  queued: "en attente",
  downloading: "",
  processing: "fusion…",
  done: "termine",
  error: "erreur",
};

let lastJobs = [];

function renderJobs(jobs) {
  const host = $("jobs");
  if (!jobs.length) {
    host.innerHTML = "";
    lastJobs = [];
    return;
  }

  const visible = jobs.slice(-4);
  lastJobs = visible;

  host.innerHTML = visible
    .map((job) => {
      const pct = job.percent ?? 0;
      const isDownloading = job.state === "downloading";
      // Un direct demarre en "queued" tant que ffmpeg n'a pas encore appele
      // le hook : on l'affiche quand meme comme "en direct".
      const isLiveActive = job.is_live && (isDownloading || job.state === "queued");
      const right = isLiveActive
        ? `● En direct${job.speed ? ` · ${formatSpeed(job.speed)}` : ""}`
        : isDownloading
          ? `${Math.round(pct)} %${job.speed ? ` · ${formatSpeed(job.speed)}` : ""}`
          : LABELS[job.state] || job.state;
      const barWidth = isLiveActive ? 100 : pct;
      const pctClass = isLiveActive ? "job__pct job__pct--live" : "job__pct";
      return `
        <div class="job job--${job.state}">
          <div class="job__row">
            <span class="job__name" title="${escapeHtml(job.error || job.title || "")}">${escapeHtml(job.title || "")}</span>
            <span class="${pctClass}">${escapeHtml(right)}</span>
          </div>
          <div class="bar"><div class="bar__fill" style="width:${barWidth}%"></div></div>
        </div>`;
    })
    .join("");

  // Ajout des boutons stop via DOM (evite d'injecter job.id dans le HTML)
  host.querySelectorAll(".job").forEach((el, i) => {
    const job = visible[i];
    const isLiveActive = job?.is_live && (job.state === "downloading" || job.state === "queued");
    if (job?.state !== "downloading" && !isLiveActive) return;
    const row = el.querySelector(".job__row");
    const btn = document.createElement("button");
    btn.className = "job__stop";
    btn.title = "Arreter l'enregistrement";
    btn.textContent = "■";
    btn.dataset.id = job.id;
    row.appendChild(btn);
  });
}

$("jobs").addEventListener("click", async (e) => {
  const btn = e.target.closest(".job__stop");
  if (!btn) return;
  btn.disabled = true;
  await send({ type: "cancel", id: btn.dataset.id });
});

function formatSpeed(bytesPerSecond) {
  const mb = bytesPerSecond / 1048576;
  return mb >= 1 ? `${mb.toFixed(1)} Mo/s` : `${(bytesPerSecond / 1024).toFixed(0)} ko/s`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function pollJobs() {
  const { data } = await send({ type: "jobs" });
  renderJobs(data?.jobs || []);
  setTimeout(pollJobs, 600);
}

// --- pied de page -----------------------------------------------------------

$("openFolder").addEventListener("click", () => send({ type: "openFolder", path: null }));
$("openOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());

// --- demarrage --------------------------------------------------------------

async function init() {
  if (!(await checkHealth())) return;
  await restoreSettings();
  await loadCurrentVideo();
  pollJobs();
}

init();
