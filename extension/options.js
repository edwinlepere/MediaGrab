/** Page de reglages : cote serveur (config.json) et cote extension (storage). */

const $ = (id) => document.getElementById(id);
const send = (msg) =>
  new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));

// Champs stockes dans le config.json du serveur local
const SERVER_FIELDS = [
  "output_dir",
  "filename_template",
  "cookies_from_browser",
  "ffmpeg_location",
  "concurrent_fragments",
];

// Champs stockes dans chrome.storage.sync
const EXTENSION_FIELDS = ["showPageButton", "notify"];
const EXTENSION_SELECTS = ["codec_pref"];

async function load() {
  const health = await send({ type: "health" });
  const running = Boolean(health?.data?.running);
  $("status").className = `status status--${running ? "on" : "off"}`;
  $("status").title = running ? "serveur local actif" : "serveur local arrete";

  if (running) {
    const { data } = await send({ type: "getConfig" });
    for (const key of SERVER_FIELDS) {
      if (data?.[key] !== undefined) $(key).value = String(data[key]);
    }
  } else {
    for (const key of SERVER_FIELDS) $(key).disabled = true;
    $("save").title = "Lance le serveur local pour modifier ces reglages";
  }

  const { data: settings } = await send({ type: "getSettings" });
  for (const key of EXTENSION_FIELDS) {
    $(key).checked = settings?.[key] !== false;
  }
  for (const key of EXTENSION_SELECTS) {
    if (settings?.[key]) $(key).value = settings[key];
  }
}

$("save").addEventListener("click", async () => {
  const config = {};
  for (const key of SERVER_FIELDS) {
    if ($(key).disabled) continue;
    config[key] =
      key === "concurrent_fragments" ? Number($(key).value) : $(key).value.trim();
  }

  if (Object.keys(config).length) {
    const { error } = await send({ type: "setConfig", config });
    if (error) return alert(`Enregistrement impossible : ${error}`);
  }

  const settings = {};
  for (const key of EXTENSION_FIELDS) settings[key] = $(key).checked;
  for (const key of EXTENSION_SELECTS) settings[key] = $(key).value;
  await chrome.storage.sync.set(settings);

  $("saved").classList.add("is-visible");
  setTimeout(() => $("saved").classList.remove("is-visible"), 1800);
});

load();
