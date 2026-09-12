"use strict";

/* ==========================================================================
   CONFIG — the only part of this file meant to be hand-edited when the
   app roster changes. Mirrors the split Nullstore (this project's design
   reference) uses: live build data comes from data/apps.json (regenerated
   by scripts/update_catalog.py), while categories/notices are presentation
   choices that don't need a pipeline run to update.
   ========================================================================== */

const CONFIG = {
  categories: {
    Google: ["youtube", "youtube-music", "gboard", "google-photos"],
    "Sosyal & İçerik": ["twitter", "twitter-x", "instagram", "reddit", "reddit-adobo", "tiktok", "tiktok-hxreborn"],
    "Gizlilik & Ağ": ["proton-vpn", "warp", "proton-pass", "brave"],
    "Üretkenlik & Araçlar": ["notesnook", "termius", "inure-github", "inure-play", "inshot", "speedtest"],
  },
  // Keep this list to things we can actually verify from Builder-Morphe's
  // own pipeline code (core/release.py::upload_microg_once), not guesses.
  notices: [
    {
      triggers: ["youtube", "youtube-music"],
      title: "MicroG gerekebilir",
      text: "Google hesabınla oturum açmak için cihazında MicroG kurulu olması gerekir. Yama akışı her başarılı çalıştırmada güncel MicroG paketini de yayına ekliyor.",
      linkLabel: "MicroG'yi indir",
      linkUrl: "https://github.com/MorpheApp/MicroG-RE/releases/latest",
      useMicrogAsset: true,
    },
  ],
  refetchIntervalMs: 5 * 60 * 1000,
  tickIntervalMs: 30 * 1000,
};

/* ==========================================================================
   State
   ========================================================================== */

const state = {
  catalog: null,
  history: null,
  category: "all",
  search: "",
  sort: "recent",
  modalAppKey: null,
};

const el = {};

/* ==========================================================================
   Boot
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
  cacheElements();
  wireStaticEvents();
  load();
  setInterval(load, CONFIG.refetchIntervalMs);
  setInterval(renderUpdatedPill, CONFIG.tickIntervalMs);
});

function cacheElements() {
  el.updatedPill = document.getElementById("updatedPill");
  el.statApps = document.getElementById("statApps");
  el.statDownloads = document.getElementById("statDownloads");
  el.statPatchSources = document.getElementById("statPatchSources");
  el.searchInput = document.getElementById("searchInput");
  el.chips = document.getElementById("categoryChips");
  el.sortSelect = document.getElementById("sortSelect");
  el.spotlightSection = document.getElementById("spotlightSection");
  el.spotlightSlot = document.getElementById("spotlightSlot");
  el.grid = document.getElementById("appGrid");
  el.resultCount = document.getElementById("resultCount");
  el.emptyState = document.getElementById("emptyState");
  el.errorState = document.getElementById("errorState");
  el.main = document.getElementById("mainContent");
  el.modal = document.getElementById("appModal");
  el.modalPanel = document.getElementById("modalPanel");
  el.toast = document.getElementById("toast");
  el.obtainiumExportLink = document.getElementById("obtainiumExportLink");
  el.obtainiumExportCount = document.getElementById("obtainiumExportCount");
}

function wireStaticEvents() {
  el.searchInput.addEventListener("input", debounce((e) => {
    state.search = e.target.value.trim().toLowerCase();
    renderGrid();
  }, 150));

  el.sortSelect.addEventListener("change", (e) => {
    state.sort = e.target.value;
    renderGrid();
  });

  el.modal.addEventListener("click", (e) => {
    if (e.target === el.modal || e.target.closest("[data-close-modal]")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !el.modal.hidden) closeModal();
  });
}

/* ==========================================================================
   Data loading
   ========================================================================== */

async function load() {
  try {
    const [catalogRes, historyRes] = await Promise.all([
      fetch("data/apps.json", { cache: "no-store" }),
      fetch("data/history.json", { cache: "no-store" }),
    ]);
    if (!catalogRes.ok) throw new Error(`data/apps.json -> HTTP ${catalogRes.status}`);
    state.catalog = await catalogRes.json();
    state.history = historyRes.ok ? await historyRes.json() : { entries: [] };

    showMain();
    renderUpdatedPill();
    renderStats();
    renderCategoryChips();
    renderSpotlight();
    renderGrid();
    renderObtainiumExport();
  } catch (err) {
    console.error("Katalog yüklenemedi:", err);
    if (!state.catalog) showError();
  }
}

function showMain() {
  el.main.hidden = false;
  el.errorState.hidden = true;
}

function showError() {
  el.main.hidden = true;
  el.errorState.hidden = false;
}

/* ==========================================================================
   Header / hero
   ========================================================================== */

function renderUpdatedPill() {
  if (!state.catalog) return;
  const { source, generatedAt } = state.catalog;
  const dot = el.updatedPill.querySelector(".dot");
  let label;
  if (!source.hasRelease) {
    label = "henüz ilk build bekleniyor";
    dot.classList.add("dot--stale");
  } else {
    label = `katalog ${relativeTimeTR(generatedAt)} tazelendi`;
    dot.classList.remove("dot--stale");
  }
  el.updatedPill.querySelector(".label").textContent = label;
}

function renderStats() {
  const { stats, patchSources } = state.catalog;
  el.statApps.textContent = `${stats.publishedApps}/${stats.totalApps}`;
  el.statDownloads.textContent = formatNumber(stats.totalDownloads);
  el.statPatchSources.textContent = formatNumber(Object.keys(patchSources).length);
}

/* ==========================================================================
   Category chips
   ========================================================================== */

function categoryOf(appKey) {
  for (const [name, keys] of Object.entries(CONFIG.categories)) {
    if (keys.includes(appKey)) return name;
  }
  return "Diğer";
}

function renderCategoryChips() {
  const names = ["all", ...Object.keys(CONFIG.categories), "Diğer"];
  const present = new Set(state.catalog.apps.map((a) => categoryOf(a.appKey)));
  el.chips.innerHTML = "";
  for (const name of names) {
    if (name !== "all" && !present.has(name)) continue;
    const btn = document.createElement("button");
    btn.className = "chip";
    btn.type = "button";
    btn.textContent = name === "all" ? "Tümü" : name;
    btn.setAttribute("aria-pressed", String(state.category === name));
    btn.addEventListener("click", () => {
      state.category = name;
      renderCategoryChips();
      renderGrid();
    });
    el.chips.appendChild(btn);
  }
}

/* ==========================================================================
   Spotlight — highlights whichever published app most recently had a
   version bump, per history.json. Every app in one Builder-Morphe release
   shares the same publishedAt timestamp (it's one shared release), so
   "most recently published" can't distinguish between apps on its own —
   "most recently *changed*" (from our own tracked history) is the
   meaningful signal instead. Hidden entirely if nothing has ever changed
   yet (a fresh catalog with no history) or nothing is published.
   ========================================================================== */

function renderSpotlight() {
  const publishedKeys = new Set(
    state.catalog.apps.filter((a) => a.status === "published" && !a.stale).map((a) => a.appKey)
  );
  const entries = (state.history?.entries || []).filter((e) => publishedKeys.has(e.appKey));

  if (!publishedKeys.size || !entries.length) {
    el.spotlightSection.hidden = true;
    return;
  }

  const top = entries[0]; // history.json is written newest-first
  const app = findApp(top.appKey);
  if (!app) {
    el.spotlightSection.hidden = true;
    return;
  }

  el.spotlightSection.hidden = false;
  el.spotlightSlot.innerHTML = "";
  el.spotlightSlot.appendChild(buildSpotlightCard(app));
}

function buildSpotlightCard(app) {
  const card = document.createElement("article");
  card.className = "spotlight-card glass glass--refractive glass--interactive";
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `${app.displayName} ayrıntılarını aç`);

  card.innerHTML = `
    <img class="spotlight-card__icon" src="${escapeAttr(app.icon)}" alt="" loading="lazy">
    <div>
      <div class="spotlight-card__name">${escapeHtml(app.displayName)}</div>
      <div class="spotlight-card__meta">
        ${badgesFor(app)}
      </div>
    </div>
    <div class="spotlight-card__actions">
      <a class="btn btn--primary" href="${escapeAttr(app.downloadUrl || "#")}" ${app.downloadUrl ? "" : "aria-disabled=\"true\" tabindex=\"-1\""}>İndir</a>
      <a class="btn btn--ghost" href="${escapeAttr(obtainiumDeepLink(app) || "#")}" target="_blank" rel="noopener noreferrer">Obtainium'a ekle</a>
    </div>
  `;

  wireCardLinks(card);
  card.addEventListener("click", () => openModal(app.appKey));
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openModal(app.appKey);
    }
  });
  return card;
}

// Shared by both card builders below: stop a link click from also
// bubbling up to the card's own "open modal" handler, and make
// aria-disabled (used for a missing download / pending app) actually
// inert instead of just visually muted.
function wireCardLinks(card) {
  card.querySelectorAll("a").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.stopPropagation();
      if (a.getAttribute("aria-disabled") === "true") e.preventDefault();
    });
  });
}

function badgesFor(app) {
  const bits = [];
  if (app.version) bits.push(`<span class="pill pill--muted mono">v${escapeHtml(app.version)}</span>`);
  for (const key of app.patchSources || []) {
    const src = state.catalog.patchSources[key];
    if (src) bits.push(`<span class="pill pill--accent">${escapeHtml(src.label)}</span>`);
  }
  bits.push(`<span class="pill pill--muted">${formatNumber(app.downloads.total)} indirme</span>`);
  if (app.stale) bits.push(`<span class="pill pill--amber">son build'de yok</span>`);
  return bits.join("");
}

/* ==========================================================================
   Grid
   ========================================================================== */

function findApp(appKey) {
  return state.catalog?.apps.find((a) => a.appKey === appKey) || null;
}

function matchesSearch(app) {
  if (!state.search) return true;
  const haystack = [app.displayName, app.appKey, app.pkg, ...(app.patchSources || [])].join(" ").toLowerCase();
  return haystack.includes(state.search);
}

function matchesCategory(app) {
  return state.category === "all" || categoryOf(app.appKey) === state.category;
}

function lastChangeAt(appKey) {
  const entry = (state.history?.entries || []).find((e) => e.appKey === appKey);
  return entry ? entry.at : "";
}

function sortApps(apps) {
  const sorted = [...apps];
  if (state.sort === "popular") {
    sorted.sort((a, b) => b.downloads.total - a.downloads.total);
  } else if (state.sort === "name") {
    sorted.sort((a, b) => a.displayName.localeCompare(b.displayName, "tr"));
  } else {
    sorted.sort((a, b) => {
      const byChange = lastChangeAt(b.appKey).localeCompare(lastChangeAt(a.appKey));
      if (byChange !== 0) return byChange;
      return b.downloads.total - a.downloads.total;
    });
  }
  return sorted;
}

function renderGrid() {
  const apps = sortApps(state.catalog.apps.filter((a) => matchesCategory(a) && matchesSearch(a)));

  el.resultCount.textContent = state.search || state.category !== "all"
    ? `${apps.length} sonuç`
    : "";

  el.grid.innerHTML = "";

  if (!apps.length) {
    el.emptyState.hidden = false;
    el.emptyState.querySelector("h2").textContent = state.catalog.stats.totalApps === 0
      ? "Henüz uygulama tanımlı değil"
      : "Aramayla eşleşen uygulama yok";
    el.emptyState.querySelector("p").textContent = state.catalog.stats.totalApps === 0
      ? "Builder-Morphe'un core/config.py dosyasında henüz hiç uygulama tanımlanmamış."
      : "Farklı bir arama terimi dene ya da kategori filtresini kaldır.";
    return;
  }
  el.emptyState.hidden = true;

  for (const app of apps) {
    el.grid.appendChild(buildCard(app));
  }
}

function buildCard(app) {
  const card = document.createElement("article");
  card.className = "card glass glass--interactive" + (app.status === "pending" ? " card--pending" : "");
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `${app.displayName} ayrıntılarını aç`);

  const statusLine = app.status === "pending"
    ? `<span class="card__version">ilk build bekleniyor</span>`
    : `<span class="card__version mono">v${escapeHtml(app.version)}${app.stale ? " · son build'de yok" : ""}</span>`;

  card.innerHTML = `
    <div class="card__top">
      <img class="card__icon" src="${escapeAttr(app.icon)}" alt="" loading="lazy">
      <div class="card__title">
        <div class="card__name">${escapeHtml(app.displayName)}</div>
        ${statusLine}
      </div>
    </div>
    <div class="card__badges">${badgesFor(app)}</div>
    <div class="card__actions">
      <a class="btn" href="${escapeAttr(app.downloadUrl || "#")}" ${app.downloadUrl ? "" : "aria-disabled=\"true\" tabindex=\"-1\""}>İndir</a>
      <a class="btn" href="${escapeAttr(obtainiumDeepLink(app) || "#")}" target="_blank" rel="noopener noreferrer" ${app.status === "pending" ? "aria-disabled=\"true\" tabindex=\"-1\"" : ""}>Obtainium</a>
    </div>
  `;

  wireCardLinks(card);
  card.addEventListener("click", () => openModal(app.appKey));
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openModal(app.appKey);
    }
  });
  return card;
}

/* ==========================================================================
   Modal
   ========================================================================== */

function openModal(appKey) {
  const app = findApp(appKey);
  if (!app) return;
  state.modalAppKey = appKey;
  el.modalPanel.innerHTML = renderModalContent(app);
  el.modalPanel.querySelector("[data-copy-regex]")?.addEventListener("click", (e) => {
    copyToClipboard(e.currentTarget.dataset.copyRegex, "Regex kopyalandı");
  });
  el.modalPanel.querySelector("[data-copy-pkg]")?.addEventListener("click", (e) => {
    copyToClipboard(e.currentTarget.dataset.copyPkg, "Paket adı kopyalandı");
  });
  el.modal.hidden = false;
  el.modalPanel.focus();
}

function closeModal() {
  el.modal.hidden = true;
  state.modalAppKey = null;
}

function renderModalContent(app) {
  const events = (state.history?.entries || []).filter((e) => e.appKey === app.appKey).slice(0, 8);
  const notice = CONFIG.notices.find((n) => n.triggers.includes(app.appKey));

  return `
    <div class="modal__head">
      <img class="modal__icon" src="${escapeAttr(app.icon)}" alt="" loading="lazy">
      <div>
        <div class="modal__name">${escapeHtml(app.displayName)}</div>
        <div class="modal__pkg mono" data-copy-pkg="${escapeAttr(app.pkg)}" title="Kopyalamak için tıkla">${escapeHtml(app.pkg)}</div>
      </div>
      <button class="btn btn--icon modal__close" data-close-modal aria-label="Kapat">${xIcon()}</button>
    </div>

    <div class="modal__stats">
      ${app.version ? `<span class="pill pill--muted mono">v${escapeHtml(app.version)}</span>` : ""}
      ${app.arch ? `<span class="pill pill--muted mono">${escapeHtml(app.arch)}</span>` : ""}
      ${app.size ? `<span class="pill pill--muted">${formatBytes(app.size)}</span>` : ""}
      <span class="pill pill--muted">${formatNumber(app.downloads.total)} toplam indirme</span>
      ${app.downloads.downloadsThisRelease != null ? `<span class="pill pill--muted">bu build: ${formatNumber(app.downloads.downloadsThisRelease)}</span>` : ""}
    </div>

    ${notice ? renderNotice(notice, app) : ""}

    ${events.length ? `
    <div class="modal__section">
      <h3>Sürüm geçmişi (bu site tarafından takip edilir)</h3>
      <ul class="version-log">
        ${events.map((e) => `
          <li>
            <time datetime="${escapeAttr(e.at)}">${formatDateTR(e.at)}</time>
            <span>${e.fromVersion ? `<span class="mono">${escapeHtml(e.fromVersion)}</span> → ` : "ilk kez yayınlandı: "}<span class="mono">${escapeHtml(e.toVersion)}</span></span>
          </li>
        `).join("")}
      </ul>
    </div>` : ""}

    ${renderChangelogSection(app)}

    <div class="modal__section">
      <h3>Obtainium ayarları</h3>
      <p style="margin-bottom: var(--sp-3);">Bu uygulamanın APK'sı, Builder-Morphe'un tek paylaşılan release'i içinde diğer uygulamalarla birlikte duruyor — bu yüzden Obtainium'un doğru dosyayı seçmesi için bir regex filtresi ve uygulama adı otomatik ayarlanır.</p>
      <div class="regex-row">
        <code>${escapeHtml(app.obtainium.additionalSettings.apkFilterRegEx)}</code>
        <button class="btn btn--icon" type="button" data-copy-regex="${escapeAttr(app.obtainium.additionalSettings.apkFilterRegEx)}" aria-label="Regex'i kopyala">${copyIcon()}</button>
      </div>
      <div style="margin-top: var(--sp-3); display:flex; gap: var(--sp-2); flex-wrap: wrap;">
        <a class="btn btn--primary" href="${escapeAttr(obtainiumDeepLink(app) || "#")}" target="_blank" rel="noopener noreferrer">Obtainium'a ekle</a>
        ${app.downloadUrl ? `<a class="btn btn--ghost" href="${escapeAttr(app.downloadUrl)}">APK'yı indir</a>` : ""}
      </div>
    </div>
  `;
}

function renderNotice(notice, app) {
  const url = notice.useMicrogAsset && state.catalog.microg?.downloadUrl ? state.catalog.microg.downloadUrl : notice.linkUrl;
  return `
    <div class="notice" style="margin-top: var(--sp-4);">
      <div>
        <strong>${escapeHtml(notice.title)}</strong>
        <div>${escapeHtml(notice.text)} <a href="${escapeAttr(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(notice.linkLabel)}</a></div>
      </div>
    </div>
  `;
}

function renderChangelogSection(app) {
  const sources = (app.patchSources || []).map((key) => state.catalog.patchSources[key]).filter(Boolean);
  if (!sources.length) return "";

  return `
    <div class="modal__section">
      <h3>Yama değişiklik günlüğü</h3>
      ${sources.map((src) => `
        <div class="changelog-source">
          <div class="changelog-source__head">
            <span class="changelog-source__label">${escapeHtml(src.label)}</span>
            <span class="changelog-source__tag mono">${escapeHtml(src.latestTag || "")}</span>
          </div>
          <div class="changelog-body">${safeMarkdownToHtml(src.changelog)}</div>
        </div>
      `).join("")}
    </div>
  `;
}

/* ==========================================================================
   Obtainium bulk export footer link
   ========================================================================== */

function renderObtainiumExport() {
  if (!el.obtainiumExportLink) return;
  fetch("data/obtainium.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : { apps: [] }))
    .then((data) => {
      const count = data.apps?.length || 0;
      el.obtainiumExportCount.textContent = count ? `(${count} uygulama)` : "(henüz yok)";
      el.obtainiumExportLink.classList.toggle("btn--disabled", !count);
      if (!count) el.obtainiumExportLink.setAttribute("aria-disabled", "true");
    })
    .catch(() => {
      el.obtainiumExportCount.textContent = "";
    });
}

/* ==========================================================================
   Safe, minimal markdown -> HTML
   Patch-source release notes are third-party text, so this escapes
   everything first and only ever re-introduces a small fixed whitelist
   of tags (h3/strong/code/a/ul/li/p) whose markup comes from this code,
   never from the source text itself. See README.md for the full
   reasoning; do not swap this for innerHTML of raw fetched text.
   ========================================================================== */

function safeMarkdownToHtml(md) {
  if (!md) return "<p>Bu sürüm için değişiklik günlüğü bulunamadı.</p>";

  let text = escapeHtml(md);

  // Links restricted to http(s) targets only.
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
  text = text.replace(/^#{1,6}\s+(.*)$/gm, "<h3>$1</h3>");

  const lines = text.split("\n");
  let html = "";
  let inList = false;
  for (const line of lines) {
    const bullet = line.match(/^\s*[*-]\s+(.*)$/);
    if (bullet) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${bullet[1]}</li>`;
      continue;
    }
    if (inList) {
      html += "</ul>";
      inList = false;
    }
    if (line.trim() === "") continue;
    if (/^<h3>/.test(line)) {
      html += line;
    } else {
      html += `<p>${line}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html || "<p>Bu sürüm için değişiklik günlüğü bulunamadı.</p>";
}

/* ==========================================================================
   Small utilities
   ========================================================================== */

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttr(str) {
  return escapeHtml(str ?? "");
}

function formatNumber(n) {
  return Number(n || 0).toLocaleString("tr-TR");
}

function formatBytes(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let val = bytes;
  let i = 0;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(val >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatDateTR(iso) {
  try {
    return new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function relativeTimeTR(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 45) return "az önce";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} dakika önce`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} saat önce`;
  const days = Math.floor(diffSec / 86400);
  if (days < 30) return `${days} gün önce`;
  return formatDateTR(iso) + " tarihinde";
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function copyToClipboard(text, message) {
  const done = () => showToast(message);
  const fail = () => showToast("Kopyalanamadı");
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(done, fail);
    return;
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    done();
  } catch {
    fail();
  }
}

let toastTimer;
function showToast(message) {
  el.toast.textContent = message;
  el.toast.classList.add("toast--show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove("toast--show"), 2200);
}

function xIcon() {
  return `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
}
function copyIcon() {
  return `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
}
