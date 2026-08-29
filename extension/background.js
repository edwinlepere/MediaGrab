/**
 * Service worker : unique point de contact avec le serveur local.
 * Le popup et le content script passent tous par ici, ce qui evite de
 * dupliquer la gestion d'erreur et le suivi des taches.
 */

const HELPER = "http://127.0.0.1:8787";

const DEFAULTS = {
  mode: "video",
  height: "1080",
  container: "mp4",
  // "compat" = H.264, lisible partout. "small" = AV1/VP9, 3 a 5 fois plus
  // leger a resolution egale, mais exige un lecteur recent.
  codec_pref: "compat",
  audio_format: "mp3",
  audio_quality: "192",
  embed_thumbnail: true,
  subtitles: false,
  auto_subs: false,
  sub_langs: ["fr", "en"],
  sponsorblock: false,
  playlist: false,
  notify: true,
  showPageButton: true,
};

async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(HELPER + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/** Le serveur local repond-il ? */
async function health() {
  try {
    return { running: true, ...(await call("/health")) };
  } catch {
    return { running: false };
  }
}

// --- suivi des taches -------------------------------------------------------
// On garde l'etat ici : le popup se ferme sans arreter le telechargement.

const tracked = new Map();

async function poll(jobId) {
  let job;
  try {
    job = await call(`/progress?id=${jobId}`);
  } catch {
    tracked.delete(jobId);
    return;
  }

  tracked.set(jobId, job);
  updateBadge();

  if (job.state === "done" || job.state === "error") {
    tracked.set(jobId, job);
    await finish(job);
    setTimeout(() => {
      tracked.delete(jobId);
      updateBadge();
    }, 60_000);
    return;
  }
  setTimeout(() => poll(jobId), 700);
}

async function finish(job) {
  const { notify } = await getSettings();
  if (!notify) return;

  const ok = job.state === "done";
  chrome.notifications.create(`ytg-${job.id}`, {
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/128.png"),
    title: ok ? "Telechargement termine" : "Echec du telechargement",
    message: ok ? (job.title || "").slice(0, 120)
                : (job.error || "erreur inconnue").slice(0, 200),
    priority: ok ? 0 : 2,
  });
}

function updateBadge() {
  const active = [...tracked.values()].filter(
    (j) => j.state === "downloading" || j.state === "processing" || j.state === "queued"
  );
  if (!active.length) {
    chrome.action.setBadgeText({ text: "" });
    return;
  }
  const pct = active[0].percent;
  chrome.action.setBadgeBackgroundColor({ color: "#c00" });
  chrome.action.setBadgeText({
    text: active.length > 1 ? String(active.length)
        : pct != null ? `${Math.round(pct)}` : "…",
  });
}

// Clic sur la notification : ouvre le fichier dans l'explorateur
chrome.notifications.onClicked.addListener(async (id) => {
  const job = tracked.get(id.replace(/^ytg-/, ""));
  if (job?.filepath) {
    call("/open", { method: "POST", body: { path: job.filepath } }).catch(() => {});
  }
  chrome.notifications.clear(id);
});

// --- lancement d'un telechargement ------------------------------------------

async function startDownload({ url, title, referer, titleHint, is_live = false, overrides = {} }) {
  const settings = await getSettings();
  const payload = {
    url,
    title,
    // Un flux repere dans le trafic est souvent refuse s'il est demande
    // hors du contexte de sa page : on transmet l'adresse d'origine.
    referer,
    // Et son manifeste ne porte aucun titre : on impose celui de la page,
    // sinon le fichier s'appellerait "index".
    title_hint: titleHint,
    is_live,
    mode: settings.mode,
    height: settings.height,
    container: settings.container,
    codec_pref: settings.codec_pref,
    audio_format: settings.audio_format,
    audio_quality: settings.audio_quality,
    embed_thumbnail: settings.embed_thumbnail,
    subtitles: settings.subtitles,
    auto_subs: settings.auto_subs,
    sub_langs: settings.sub_langs,
    sponsorblock: settings.sponsorblock,
    playlist: settings.playlist,
    ...overrides,
  };

  const { id } = await call("/download", { method: "POST", body: payload });
  tracked.set(id, { id, state: "queued", percent: 0, title: title || url });
  updateBadge();
  poll(id);
  return id;
}

// --- detection des flux par le trafic reseau --------------------------------
// yt-dlp analyse le HTML de la page ; il ne voit donc pas un flux charge en
// JavaScript apres coup. On observe le trafic pour les reperer, comme le font
// les extensions specialisees. Observation seule : rien n'est bloque ni
// modifie, et aucun contenu de requete n'est lu.

const PLAYLIST_RE = /\.(m3u8|mpd)(\?|$)/i;
const FILE_RE = /\.(mp4|m4v|webm|mov|mpeg|mpg|avi|flv|mkv|mp3|m4a|aac|ogg|wav)(\?|$)/i;

// Un flux HLS ou DASH emet des centaines de segments : seul le manifeste
// nous interesse, sinon la liste serait noyee.
const SEGMENT_RE = /\.(ts|m4s|cmfv|cmfa)(\?|$)/i;

const MAX_PER_TAB = 25;
const streams = new Map(); // tabId -> Map(url -> flux)

function classify(url, resourceType) {
  if (SEGMENT_RE.test(url)) return null;
  if (PLAYLIST_RE.test(url)) return /\.mpd(\?|$)/i.test(url) ? "DASH" : "HLS";
  if (FILE_RE.test(url)) return "HTTP";
  if (resourceType === "media") return "HTTP";
  return null;
}

/** Type de flux d'apres l'en-tete Content-Type.
 *
 *  Indispensable : beaucoup de sites servent leurs videos depuis des adresses
 *  sans extension de fichier, que le classement par nom laisse passer.
 *  Le serveur, lui, declare toujours ce qu'il envoie.
 */
function classifyByType(contentType) {
  const type = (contentType || "").toLowerCase();
  if (type.includes("mpegurl")) return "HLS";
  if (type.includes("dash+xml")) return "DASH";
  // video/mp2t est un segment de flux HLS, pas un fichier telechargeable
  if (type.startsWith("video/mp2t")) return null;
  if (type.startsWith("video/") || type.startsWith("audio/")) return "HTTP";
  return null;
}

function record(tabId, url, kind) {
  let forTab = streams.get(tabId);
  if (!forTab) {
    forTab = new Map();
    streams.set(tabId, forTab);
  }
  if (forTab.has(url)) return;

  // Les plus anciens sautent en premier : sur une page longue, ce qui vient
  // d'etre lance interesse plus que ce qui a defile il y a dix minutes.
  if (forTab.size >= MAX_PER_TAB) {
    forTab.delete(forTab.keys().next().value);
  }

  let name = "";
  try {
    name = decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
  } catch {}

  forTab.set(url, { url, kind, name, at: Date.now() });
}

chrome.webRequest.onBeforeRequest.addListener(
  ({ tabId, url, type }) => {
    if (tabId < 0 || !/^https?:/i.test(url)) return;
    const kind = classify(url, type);
    if (kind) record(tabId, url, kind);
  },
  { urls: ["<all_urls>"] }
);

// Second filet, sur la reponse cette fois : rattrape tout ce que l'adresse
// seule ne trahissait pas.
chrome.webRequest.onHeadersReceived.addListener(
  ({ tabId, url, responseHeaders }) => {
    if (tabId < 0 || !/^https?:/i.test(url) || SEGMENT_RE.test(url)) return;

    const header = (responseHeaders || []).find(
      (h) => h.name.toLowerCase() === "content-type"
    );
    const kind = classifyByType(header?.value);
    if (kind) record(tabId, url, kind);
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// Une navigation remet le compteur a zero : les flux de la page precedente
// n'ont plus rien a faire dans la liste. On passe par tabs.onUpdated plutot
// que webNavigation, qui coute une permission supplementaire pour rien.
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading" && info.url) streams.delete(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => streams.delete(tabId));

// Un manifeste maitre liste toutes les qualites disponibles ; une variante
// n'en decrit qu'une seule, et un flux audio isole aucune image. Analyser la
// mauvaise entree donne soit une seule resolution, soit un echec.
const MASTER_RE = /(master|manifest|playlist)\.(m3u8|mpd)/i;
const VARIANT_RE = /(index|chunklist|media|-v\d|-a\d)/i;

function streamRank(s) {
  if (s.kind === "HTTP") return 3;
  if (MASTER_RE.test(s.url)) return 0;
  if (VARIANT_RE.test(s.url)) return 2;
  return 1;
}

function streamsFor(tabId) {
  const found = [...(streams.get(tabId)?.values() || [])];
  return found.sort((a, b) => streamRank(a) - streamRank(b) || b.at - a.at);
}

// Bruit courant des titres de pages de streaming, qui ferait un tres mauvais
// nom de fichier. On ne retire que des formules sans ambiguite.
const NOISE_RE = new RegExp(
  "\\b(en\\s+)?(streaming|stream)\\b.*$|\\b(complet|integrale?|gratuit(ement)?|" +
  "en\\s+ligne|vf|vostfr|hd|full\\s*hd|4k|voir|regarder|telecharger)\\b",
  "gi"
);

function cleanTitle(raw, hostname = "") {
  let out = (raw || "").replace(/\s+/g, " ").replace(NOISE_RE, " ");

  // Suffixe de site (" - MonSite", " | MonSite"), retire uniquement s'il
  // correspond vraiment au domaine. Un decoupage aveugle transformerait
  // "Reacher - Saison 4" en "Reacher", detruisant l'information utile.
  const host = hostname.toLowerCase().replace(/^www\./, "").replace(/\..*$/, "");
  if (host.length >= 3) {
    out = out.replace(/\s*[-|–—]\s*([^-|–—]{2,30})$/, (whole, tail) => {
      const flat = tail.toLowerCase().replace(/[^a-z0-9]/g, "");
      return flat && (host.includes(flat) || flat.includes(host)) ? "" : whole;
    });
  }

  return out
    .replace(/[\s\-–—_|]+$/, "")
    .replace(/^[\s\-–—_|]+/, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** Meilleure image possible pour la page en cours.
 *
 *  Cascade de cinq sources, de la plus fiable a la plus opportuniste. Aucune
 *  ne marche partout, mais leur enchainement couvre la quasi-totalite des cas.
 *  Tout s'execute dans la page, donc rien n'est telecharge inutilement.
 */
async function pageImage(tabId) {
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const abs = (u) => {
          try {
            return new URL(u, location.href).href;
          } catch {
            return "";
          }
        };
        const ok = (u) => /^https?:\/\//.test(u);

        // 1. Metadonnees de partage. C'est leur raison d'etre : fournir
        //    l'image representative de la page. Presentes presque partout.
        const metas = [
          'meta[property="og:image:secure_url"]',
          'meta[property="og:image"]',
          'meta[name="og:image"]',
          'meta[name="twitter:image"]',
          'meta[name="twitter:image:src"]',
          'link[rel="image_src"]',
        ];
        for (const sel of metas) {
          const el = document.querySelector(sel);
          const raw = el?.getAttribute("content") || el?.getAttribute("href");
          if (raw && ok(abs(raw))) return abs(raw);
        }

        // 2. Donnees structurees schema.org. Les fiches de films en portent
        //    souvent. On n'accepte l'image que si elle appartient a un noeud
        //    decrivant bien un contenu : sans ce filtre, une descente aveugle
        //    ramene l'icone generique de la collection ou du site.
        const CONTENT = /^(movie|video|videoobject|musicvideoobject|clip|episode|tvepisode|tvseries|creativework|article|newsarticle|blogposting)$/i;
        const dig = (node, depth = 0) => {
          if (!node || depth > 6) return "";
          if (Array.isArray(node)) {
            for (const n of node) {
              const hit = dig(n, depth + 1);
              if (hit) return hit;
            }
            return "";
          }
          if (typeof node !== "object") return "";

          const types = [].concat(node["@type"] || []);
          if (types.some((t) => CONTENT.test(String(t).replace(/\s/g, "")))) {
            for (const key of ["image", "thumbnailUrl", "poster"]) {
              const val = node[key];
              const candidate =
                typeof val === "string" ? val
                : Array.isArray(val) ? (typeof val[0] === "string" ? val[0] : val[0]?.url)
                : val?.url;
              if (candidate && ok(abs(candidate))) return abs(candidate);
            }
          }
          for (const key of Object.keys(node)) {
            const hit = dig(node[key], depth + 1);
            if (hit) return hit;
          }
          return "";
        };
        for (const tag of document.querySelectorAll('script[type="application/ld+json"]')) {
          try {
            const hit = dig(JSON.parse(tag.textContent));
            if (hit) return hit;
          } catch {}
        }

        // 3. Affiche declaree par le lecteur lui-meme.
        for (const v of document.querySelectorAll("video[poster]")) {
          if (ok(abs(v.poster))) return abs(v.poster);
        }

        // 4. A defaut, la plus grande image du contenu. Sur une fiche de film,
        //    c'est l'affiche. On ecarte en-tetes, menus et pieds de page, ou
        //    ne vivent que logos et bannieres.
        let best = "";
        let bestArea = 0;
        for (const img of document.images) {
          if (img.closest("header, nav, footer, aside")) continue;

          // Les images differees n'ont pas encore de src exploitable
          const raw =
            img.currentSrc || img.src ||
            img.dataset.src || img.dataset.lazySrc || img.dataset.original;
          if (!raw || !ok(abs(raw))) continue;

          // Un SVG est une icone d'interface, jamais une affiche. Etirees par
          // la mise en page, elles passaient le filtre de taille a l'ecran.
          if (/\.svg(\?|$)/i.test(raw)) continue;

          // On juge sur les dimensions reelles du fichier, pas sur celles
          // affichees : une icone de 24 px agrandie en CSS reste une icone.
          let w = img.naturalWidth;
          let h = img.naturalHeight;

          // Une image en chargement differe n'est pas encore decodee et
          // annonce 0x0. On se rabat alors sur la place qu'elle occupe,
          // sinon le repli serait aveugle sur tous les sites modernes.
          if (!w || !h) {
            const box = img.getBoundingClientRect();
            w = box.width;
            h = box.height;
          }
          if (w < 200 || h < 200) continue;

          // Ecarte les bandeaux et les bandes etroites : une affiche ou une
          // vignette reste dans des proportions raisonnables.
          const ratio = w / h;
          if (ratio < 0.4 || ratio > 2.5) continue;

          if (w * h > bestArea) {
            bestArea = w * h;
            best = abs(raw);
          }
        }
        return best;
      },
    });
    return res?.result || "";
  } catch {
    // Page interne ou onglet sans autorisation : ce n'est pas une erreur.
    return "";
  }
}

/** Meilleur nom possible pour la page en cours.
 *
 *  Le titre de l'onglet est souvent bourre de mots-cles. og:title, la
 *  metadonnee de partage, est en general bien plus propre, et le premier
 *  titre de la page vient juste apres. Rien de tout cela n'est fiable a
 *  100 %, d'ou le champ modifiable dans l'interface. */
async function pageTitle(tabId, fallback, pageUrl) {
  let host = "";
  try {
    host = new URL(pageUrl).hostname;
  } catch {}

  let candidates = [];
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const txt = (s) => (s || "").replace(/\s+/g, " ").trim();
        const meta = document.querySelector(
          'meta[property="og:title"], meta[name="og:title"], meta[name="twitter:title"]'
        );
        return [txt(meta?.content), txt(document.querySelector("h1")?.textContent)];
      },
    });
    candidates = (res?.result || []).filter(Boolean);
  } catch {
    // Page interne, ou onglet sans autorisation : on se rabat sur l'onglet.
  }

  for (const raw of [...candidates, fallback]) {
    const cleaned = cleanTitle(raw, host);
    if (cleaned.length >= 3) return cleaned;
  }
  return cleanTitle(fallback, host) || "video";
}

// --- routage des messages ---------------------------------------------------

const handlers = {
  health,
  getSettings,
  jobs: async () => ({ jobs: [...tracked.values()] }),
  // Test instantane, sans reseau : la page est-elle telechargeable ?
  supported: ({ url }) => call("/supported", { method: "POST", body: { url } }),

  // Flux reperes dans le trafic de l'onglet actif
  streams: async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    // Nom et image viennent tous deux de la page : autant les lire ensemble.
    const [pageTitleValue, poster] = tab?.id
      ? await Promise.all([
          pageTitle(tab.id, tab.title, tab.url),
          pageImage(tab.id),
        ])
      : ["", ""];

    return {
      streams: tab ? streamsFor(tab.id) : [],
      pageUrl: tab?.url || "",
      // Le manifeste ne porte ni nom ni image exploitables : ils viennent
      // de la page qui l'heberge.
      pageTitle: pageTitleValue,
      poster,
    };
  },

  // Le codec choisi change les tailles annoncees : /info doit le connaitre,
  // sinon le selecteur afficherait des poids qui ne correspondent a rien.
  info: async ({ url, referer }) => {
    const { codec_pref } = await getSettings();
    return call("/info", { method: "POST", body: { url, codec_pref, referer } });
  },
  download: startDownload,
  cancel: ({ id }) => call("/cancel", { method: "POST", body: { id } }),
  openFolder: ({ path }) => call("/open", { method: "POST", body: { path } }),
  // Un content script ne peut pas ouvrir la page d'options lui-meme
  openOptions: async () => {
    chrome.runtime.openOptionsPage();
    return { ok: true };
  },
  setConfig: ({ config }) => call("/config", { method: "POST", body: config }),
  getConfig: () => call("/config"),
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const handler = handlers[msg?.type];
  if (!handler) {
    sendResponse({ error: `action inconnue: ${msg?.type}` });
    return false;
  }
  Promise.resolve(handler(msg))
    .then((data) => sendResponse({ data }))
    .catch((err) => sendResponse({ error: err.message }));
  return true; // reponse asynchrone
});
