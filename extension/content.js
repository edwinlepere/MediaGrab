/**
 * Barre de telechargement inseree sous le lecteur YouTube.
 *
 * Selecteur de format (resolution, FPS, poids estime) + bouton Telecharger,
 * pour ne pas avoir a ouvrir le popup de l'extension a chaque video.
 */

const WRAP_ID = "mediagrab-wrap";

let currentVideoId = null;
let options = [];
let selected = null;

const send = (msg) =>
  new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));

// --- helpers ----------------------------------------------------------------

function videoIdFromUrl() {
  const u = new URL(location.href);
  if (u.pathname === "/watch") return u.searchParams.get("v");
  const m = u.pathname.match(/^\/(?:shorts|live|embed)\/([\w-]{11})/);
  return m ? m[1] : null;
}

function videoTitle() {
  const el = document.querySelector(
    "h1.ytd-watch-metadata yt-formatted-string, h1.title yt-formatted-string, #title h1"
  );
  return el?.textContent?.trim() || document.title.replace(/ - YouTube$/, "");
}

function humanSize(bytes) {
  if (!bytes) return null;
  const mb = bytes / 1048576;
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} Go`;
  if (mb >= 1) return `${mb.toFixed(2)} Mo`;
  return `${(bytes / 1024).toFixed(2)} Ko`;
}

/** Pastille de gauche : 2160p, 1080p, Audio… */
function chipOf(opt) {
  if (opt.kind === "audio") return "Audio";
  return `${opt.height}p`;
}

/** Exposant a cote de la pastille : 4K, 2K, HD, HDR */
function tagOf(opt) {
  if (opt.kind === "audio") return "";
  if (opt.hdr) return "HDR";
  if (opt.height >= 2160) return "4K";
  if (opt.height >= 1440) return "2K";
  if (opt.height >= 1080) return "HD";
  return "";
}

/** Deux paliers de couleur : jaune au dessus du 1080p, bleu pour le HD,
 *  neutre en dessous. */
function chipClass(tag) {
  if (!tag) return "";
  return tag === "HD" ? " ytg-chip--hd" : " ytg-chip--uhd";
}

function chipHtml(opt) {
  const tag = tagOf(opt);
  return `<span class="ytg-chip${chipClass(tag)}">${chipOf(opt)}${
    tag ? `<sup>${tag}</sup>` : ""
  }</span>`;
}

/** Ligne descriptive : 1920x1080 - 60FPS - ~571 Mo */
function labelOf(opt) {
  const size = humanSize(opt.filesize);
  const sized = size ? `${opt.estimated ? "~" : ""}${size}` : "taille inconnue";

  if (opt.kind === "audio") {
    const quality = opt.pref === "high" ? "Haute qualite" : "Basse qualite";
    return `${quality} - ${sized}`;
  }
  const parts = [`${opt.width}x${opt.height}`];
  if (opt.fps) parts.push(`${opt.fps}FPS`);
  parts.push(sized);
  return parts.join(" - ");
}

/** Charge utile envoyee au serveur pour l'option choisie. */
function payloadOf(opt) {
  return opt.kind === "audio"
    ? { mode: "audio", audio_pref: opt.pref }
    : { mode: "video", height: String(opt.height) };
}

// --- choix par defaut -------------------------------------------------------

async function pickDefault(list) {
  const { data } = await send({ type: "getSettings" });
  const prefKind = data?.mode === "audio" ? "audio" : "video";

  if (prefKind === "audio") {
    const audio = list.find((o) => o.kind === "audio");
    if (audio) return audio;
  }

  const wanted = Number(data?.height);
  const videos = list.filter((o) => o.kind === "video");
  if (!videos.length) return list[0] || null;

  if (Number.isFinite(wanted)) {
    const exact = videos.find((o) => o.height === wanted);
    if (exact) return exact;
    // La qualite memorisee n'existe pas ici : on prend la plus proche
    // en dessous, jamais au dessus, pour ne pas gonfler le fichier.
    const below = videos.find((o) => o.height < wanted);
    if (below) return below;
  }
  return videos[0];
}

// --- construction du widget -------------------------------------------------

const CHEVRON =
  '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">' +
  '<path fill="currentColor" d="M7.4 15.4 12 10.8l4.6 4.6 1.4-1.4-6-6-6 6z"/></svg>';

function itemHtml(opt, index) {
  return `
    <button class="ytg-item${opt === selected ? " is-active" : ""}" data-index="${index}" type="button">
      ${chipHtml(opt)}
      <span class="ytg-text">${labelOf(opt)}</span>
      ${opt.kind === "audio" ? '<span class="ytg-note">&#9834;</span>' : ""}
    </button>`;
}

function render(wrap) {
  const picker = wrap.querySelector(".ytg-picker");
  const menu = wrap.querySelector(".ytg-menu");

  if (selected) {
    // outerHTML, et non innerHTML : la classe de mise en avant appartient
    // a la pastille elle-meme et doit suivre le changement de selection.
    picker.querySelector(".ytg-chip").outerHTML = chipHtml(selected);
    picker.querySelector(".ytg-text").textContent = labelOf(selected);
  }
  // Trait de separation a la bascule video -> audio
  menu.innerHTML = options
    .map((opt, i) => {
      const startsAudio = opt.kind === "audio" && options[i - 1]?.kind === "video";
      return (startsAudio ? '<div class="ytg-sep"></div>' : "") + itemHtml(opt, i);
    })
    .join("");
}

function buildWidget() {
  const wrap = document.createElement("div");
  wrap.id = WRAP_ID;
  wrap.className = "ytg-wrap";
  wrap.innerHTML = `
    <div class="ytg-bar">
      <button class="ytg-picker" type="button" aria-haspopup="true" aria-expanded="false">
        <span class="ytg-chip">…</span>
        <span class="ytg-text">Lecture des formats…</span>
        <span class="ytg-chev">${CHEVRON}</span>
      </button>
      <button class="ytg-download" type="button">Telecharger</button>
      <button class="ytg-kebab" type="button" title="Plus d'options" aria-label="Plus d'options">
        <svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor"
          d="M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"/></svg>
      </button>
      <div class="ytg-menu" hidden></div>
      <div class="ytg-kmenu" hidden>
        <button type="button" data-act="codec" class="ytg-toggle">
          <span class="ytg-check">&#10003;</span>Fichiers legers (AV1)
        </button>
        <div class="ytg-sep"></div>
        <button type="button" data-act="folder">Ouvrir le dossier</button>
        <button type="button" data-act="settings">Reglages</button>
        <button type="button" data-act="hide">Masquer cette barre</button>
      </div>
    </div>
    <div class="ytg-progress" hidden><div class="ytg-progress__fill"></div></div>
    <div class="ytg-msg" hidden></div>`;

  const picker = wrap.querySelector(".ytg-picker");
  const menu = wrap.querySelector(".ytg-menu");
  const kmenu = wrap.querySelector(".ytg-kmenu");

  const closeAll = () => {
    menu.hidden = true;
    kmenu.hidden = true;
    picker.setAttribute("aria-expanded", "false");
  };

  picker.addEventListener("click", (e) => {
    e.stopPropagation();
    kmenu.hidden = true;
    const opening = menu.hidden;
    menu.hidden = !opening;
    picker.setAttribute("aria-expanded", String(opening));
  });

  menu.addEventListener("click", (e) => {
    const item = e.target.closest(".ytg-item");
    if (!item) return;
    e.stopPropagation();
    selected = options[Number(item.dataset.index)];
    closeAll();
    render(wrap);
    // On memorise le choix pour le proposer par defaut sur la video suivante
    chrome.storage.sync.set(
      selected.kind === "audio"
        ? { mode: "audio" }
        : { mode: "video", height: String(selected.height) }
    );
  });

  wrap.querySelector(".ytg-kebab").addEventListener("click", async (e) => {
    e.stopPropagation();
    menu.hidden = true;
    const opening = kmenu.hidden;
    kmenu.hidden = !opening;

    if (opening) {
      const { data } = await send({ type: "getSettings" });
      kmenu
        .querySelector(".ytg-toggle")
        .classList.toggle("is-on", data?.codec_pref === "small");
    }
  });

  kmenu.addEventListener("click", async (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (!act) return;
    e.stopPropagation();
    closeAll();

    if (act === "folder") send({ type: "openFolder", path: null });
    if (act === "settings") send({ type: "openOptions" });
    if (act === "hide") chrome.storage.sync.set({ showPageButton: false });

    if (act === "codec") {
      const { data } = await send({ type: "getSettings" });
      const next = data?.codec_pref === "small" ? "compat" : "small";
      await chrome.storage.sync.set({ codec_pref: next });
      // Les tailles annoncees dependent du codec : il faut les recharger.
      wrap.querySelector(".ytg-text").textContent = "Relecture des formats…";
      loadFormats(wrap);
    }
  });

  document.addEventListener("click", closeAll);

  wrap.querySelector(".ytg-download").addEventListener("click", (e) => {
    e.stopPropagation();
    closeAll();
    startDownload(wrap);
  });

  return wrap;
}

// --- telechargement ---------------------------------------------------------

function message(wrap, text, kind) {
  const el = wrap.querySelector(".ytg-msg");
  el.hidden = !text;
  el.textContent = text || "";
  el.className = `ytg-msg${kind ? ` ytg-msg--${kind}` : ""}`;
}

function progress(wrap, percent) {
  const bar = wrap.querySelector(".ytg-progress");
  bar.hidden = percent == null;
  if (percent != null) {
    wrap.querySelector(".ytg-progress__fill").style.width = `${percent}%`;
  }
}

async function startDownload(wrap) {
  if (!selected) return;

  const button = wrap.querySelector(".ytg-download");
  button.disabled = true;
  button.textContent = "…";
  message(wrap, "");

  const res = await send({
    type: "download",
    url: location.href.split("&list=")[0],
    title: videoTitle(),
    overrides: payloadOf(selected),
  });

  if (res?.error) {
    button.disabled = false;
    button.textContent = "Telecharger";
    message(wrap, res.error, "error");
    return;
  }
  follow(wrap, res.data);
}

function follow(wrap, jobId) {
  const button = wrap.querySelector(".ytg-download");

  const tick = async () => {
    const { data } = await send({ type: "jobs" });
    const job = data?.jobs?.find((j) => j.id === jobId);
    if (!job) return reset();

    if (job.state === "downloading") {
      const pct = job.percent;
      button.textContent = pct != null ? `${Math.round(pct)} %` : "…";
      progress(wrap, pct ?? 0);
    } else if (job.state === "processing") {
      button.textContent = "Fusion…";
      progress(wrap, 100);
    } else if (job.state === "done") {
      button.textContent = "Termine";
      button.classList.add("is-done");
      progress(wrap, null);
      message(wrap, "Enregistre dans le dossier de destination.", "ok");
      setTimeout(reset, 5000);
      return;
    } else if (job.state === "error") {
      progress(wrap, null);
      message(wrap, job.error || "echec du telechargement", "error");
      reset();
      return;
    }
    setTimeout(tick, 700);
  };

  const reset = () => {
    button.disabled = false;
    button.classList.remove("is-done");
    button.textContent = "Telecharger";
    progress(wrap, null);
  };

  tick();
}

// --- chargement des formats -------------------------------------------------

async function loadFormats(wrap) {
  const health = await send({ type: "health" });
  if (!health?.data?.running) {
    wrap.querySelector(".ytg-picker").disabled = true;
    wrap.querySelector(".ytg-download").disabled = true;
    wrap.querySelector(".ytg-text").textContent = "Serveur local arrete";
    message(wrap, "Lance install.bat dans le dossier helper, puis recharge la page.", "error");
    return;
  }

  const { data, error } = await send({ type: "info", url: location.href });
  if (error || !data?.options?.length) {
    wrap.querySelector(".ytg-text").textContent = "Formats indisponibles";
    if (error) message(wrap, error, "error");
    return;
  }

  options = data.options;
  selected = await pickDefault(options);
  wrap.querySelector(".ytg-picker").disabled = false;
  wrap.querySelector(".ytg-download").disabled = false;
  render(wrap);
}

// --- insertion dans la page -------------------------------------------------

// --- Shorts -----------------------------------------------------------------
// Format vertical : pas de barre sous le lecteur, mais un bouton rond ajoute
// en tete de la colonne d'actions, au dessus de J'aime.

const SHORT_CLASS = "ytg-short";

function isShorts() {
  return location.pathname.startsWith("/shorts/");
}

// Conteneurs possibles de la colonne d'actions, du plus precis au plus large.
// YouTube renomme regulierement ses composants : l'ancien selecteur
// "ytd-reel-video-renderer[is-active] #actions" ne correspond plus a rien,
// l'attribut is-active ayant disparu et #actions ne designant plus le rail.
const SHORT_RAILS = [
  "reel-action-bar-view-model",
  ".ytReelPlayerOverlayViewModelActionsContainer",
  "ytd-reel-player-overlay-renderer #actions",
];

function onScreen(el) {
  const b = el.getBoundingClientRect();
  return b.width > 0 && b.height > 0 && b.top < innerHeight && b.bottom > 0;
}

/** Plusieurs Shorts sont montes en meme temps ; le bon est celui qu'on voit. */
function activeRail() {
  for (const selector of SHORT_RAILS) {
    const hit = [...document.querySelectorAll(selector)].find(onScreen);
    if (hit) return hit;
  }
  return null;
}

function shortTitle() {
  const el = document.querySelector("yt-shorts-video-title-view-model");
  return (
    el?.textContent?.trim() ||
    document.title.replace(/^\(\d+\)\s*/, "").replace(/ - YouTube$/, "")
  );
}

function buildShortButton() {
  const host = document.createElement("div");
  host.className = SHORT_CLASS;
  host.innerHTML = `
    <button class="ytg-short__btn" type="button" title="Telecharger ce Short">
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
        <path fill="currentColor" d="M12 3v10.6l3.3-3.3 1.4 1.4L12 17.4l-4.7-4.7 1.4-1.4 3.3 3.3V3h2z"/>
        <path fill="currentColor" d="M5 19h14v2H5z"/>
      </svg>
    </button>
    <span class="ytg-short__label">Telecharger</span>`;

  host.querySelector("button").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    downloadShort(host);
  });

  return host;
}

async function downloadShort(host) {
  const button = host.querySelector(".ytg-short__btn");
  const label = host.querySelector(".ytg-short__label");

  const health = await send({ type: "health" });
  if (!health?.data?.running) {
    label.textContent = "Serveur arrete";
    setTimeout(() => (label.textContent = "Telecharger"), 4000);
    return;
  }

  button.disabled = true;
  label.textContent = "…";

  const res = await send({
    type: "download",
    url: location.href.split("?")[0],
    title: shortTitle(),
    overrides: {},
  });

  if (res?.error) {
    host.dataset.state = "error";
    label.textContent = "Erreur";
    setTimeout(reset, 5000);
    return;
  }

  const tick = async () => {
    const { data } = await send({ type: "jobs" });
    const job = data?.jobs?.find((j) => j.id === res.data);
    if (!job) return reset();

    if (job.state === "downloading") {
      label.textContent = job.percent != null ? `${Math.round(job.percent)} %` : "…";
    } else if (job.state === "processing") {
      label.textContent = "Fusion…";
    } else if (job.state === "done") {
      host.dataset.state = "done";
      label.textContent = "Termine";
      setTimeout(reset, 4000);
      return;
    } else if (job.state === "error") {
      host.dataset.state = "error";
      label.textContent = "Erreur";
      setTimeout(reset, 5000);
      return;
    }
    setTimeout(tick, 700);
  };

  function reset() {
    delete host.dataset.state;
    button.disabled = false;
    label.textContent = "Telecharger";
  }

  tick();
}

function injectShorts() {
  // La barre de la page video classique n'a rien a faire ici
  document.getElementById(WRAP_ID)?.remove();
  currentVideoId = null;

  const rail = activeRail();
  if (!rail) return;

  // Nettoyage des boutons laisses dans les Shorts sortis de l'ecran
  document.querySelectorAll(`.${SHORT_CLASS}`).forEach((el) => {
    if (!rail.contains(el)) el.remove();
  });

  if (rail.querySelector(`.${SHORT_CLASS}`)) return;
  rail.prepend(buildShortButton());
}

// --- page video classique ---------------------------------------------------

function inject() {
  if (isShorts()) return injectShorts();

  const id = videoIdFromUrl();
  if (!id) return;

  const existing = document.getElementById(WRAP_ID);
  if (existing && id === currentVideoId && existing.isConnected) return;
  existing?.remove();

  // Juste sous le lecteur, au dessus du titre.
  const host =
    document.querySelector("ytd-watch-flexy #below") ||
    document.querySelector("#primary-inner") ||
    document.querySelector("#below");
  if (!host) return;

  currentVideoId = id;
  options = [];
  selected = null;

  const wrap = buildWidget();
  host.prepend(wrap);
  loadFormats(wrap);
}

async function applyVisibility() {
  const { data } = await send({ type: "getSettings" });
  document.documentElement.classList.toggle(
    "mediagrab-hidden",
    data?.showPageButton === false
  );
}

// YouTube ne recharge pas la page : on observe le DOM et l'evenement maison.
// L'observateur est etrangle, YouTube declenchant des centaines de mutations
// par seconde alors qu'inject() interroge le DOM a chaque appel.
let pending = false;
const observer = new MutationObserver(() => {
  if (pending) return;
  pending = true;
  setTimeout(() => {
    pending = false;
    inject();
  }, 250);
});
observer.observe(document.documentElement, { childList: true, subtree: true });

window.addEventListener("yt-navigate-finish", () => {
  currentVideoId = null;
  setTimeout(inject, 300);
});

chrome.storage.onChanged.addListener(applyVisibility);

applyVisibility();
inject();
