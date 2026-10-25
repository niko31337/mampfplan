// Mampfplan – Client-Logik. Kein Inline-JS wegen der Content-Security-Policy.
// Enthält: kleine Helfer, Drag & Drop, Offline-Warteschlange mit Synchronisation, Display-Sperre, Service Worker.
"use strict";

/* ------------------------------------------------------------------ Hilfsfunktionen */

const base = () => document.body.dataset.base || "";
const csrfToken = () => {
  try {
    return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || "";
  } catch {
    return "";
  }
};

function showToast(text) {
  const slot = document.getElementById("toast");
  if (!slot) return;
  slot.replaceChildren();
  const div = document.createElement("div");
  div.className = "toast";
  div.textContent = text;
  slot.appendChild(div);
}

const listEl = () => document.getElementById("shopping-list");

/* ------------------------------------------------------------------ Klick-Helfer */

document.addEventListener("click", (event) => {
  const allBtn = event.target.closest("[data-check-all]");
  if (allBtn) {
    const boxes = document.querySelectorAll(`${allBtn.dataset.checkAll} input[type=checkbox]`);
    const everythingChecked = [...boxes].every((box) => box.checked);
    boxes.forEach((box) => (box.checked = !everythingChecked));
    allBtn.textContent = everythingChecked ? "Alle wählen" : "Keine wählen";
  }
  const copyBtn = event.target.closest("[data-copy]");
  if (copyBtn) {
    const text = document.querySelector(copyBtn.dataset.copy)?.textContent.trim();
    if (text && navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => {
        const old = copyBtn.textContent;
        copyBtn.textContent = "Kopiert ✓";
        setTimeout(() => (copyBtn.textContent = old), 1500);
      });
    }
  }
  if (event.target.closest("[data-reload]")) location.reload();
});

// Sicherheitsabfrage für Formulare mit data-confirm
document.addEventListener(
  "submit",
  (event) => {
    const message = event.target.dataset?.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopPropagation();
    }
  },
  true
);

// Formulare mit data-reset-on-success nach erfolgreichem HTMX-Request leeren
document.addEventListener("htmx:afterRequest", (event) => {
  const el = event.detail.elt;
  if (el?.matches?.("[data-reset-on-success]") && event.detail.successful) el.reset();
});

/* ------------------------------------------------------------------ Offline-Warteschlange */
// Abhaken und Umsortieren funktionieren auch ohne Verbindung: Die Aktion wird lokal gemerkt
// (localStorage) und später der Reihe nach an den Server geschickt. Die Server-Endpunkte sind
// idempotent (Abhaken setzt einen Zustand, Sortieren setzt eine Reihenfolge) – doppeltes Senden schadet nicht.

const QUEUE_KEY = "mampfplan.queue";
const QUEUEABLE = /\/shopping\/(?:\d+\/toggle|reorder|categories\/order)$/;
const TOGGLE = /\/shopping\/(\d+)\/toggle$/;

function loadQueue() {
  try {
    return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveQueue(queue) {
  try {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
  } catch {
    /* Speicher voll/gesperrt: dann geht die Aktion eben verloren */
  }
}

function queueKey(entry) {
  const category = entry.pairs.find(([k]) => k === "category")?.[1] || "";
  return `${entry.path}|${category}`;
}

function enqueue(entry) {
  const key = queueKey(entry);
  // Gleiche Aktion (z. B. mehrmals dasselbe Häkchen) → nur der letzte Stand zählt.
  const queue = loadQueue().filter((e) => queueKey(e) !== key);
  queue.push(entry);
  saveQueue(queue);
}

function requestPairs(config) {
  const data = config.formData ?? config.parameters;
  if (!data) return [];
  if (typeof data.entries === "function" && !(data instanceof Map)) return [...data.entries()].map(([k, v]) => [k, String(v)]);
  return Object.entries(data).map(([k, v]) => [k, String(v)]);
}

function onNetworkFailure(event) {
  const config = event.detail.requestConfig;
  const path = config?.path || event.detail.pathInfo?.requestPath || "";
  if (config?.verb === "post" && QUEUEABLE.test(path)) {
    enqueue({ path, pairs: requestPairs(config) });
    updateBanner();
    scheduleRetry();
    showToast("📴 Gemerkt – wird übertragen, sobald du wieder Verbindung hast.");
  } else {
    showToast("Keine Verbindung zum Server.");
  }
}
document.addEventListener("htmx:sendError", onNetworkFailure);
document.addEventListener("htmx:timeout", onNetworkFailure);

document.addEventListener("htmx:responseError", (event) => {
  showToast(event.detail.xhr.status === 403 ? "Sitzung abgelaufen – bitte Seite neu laden." : "Das hat leider nicht geklappt.");
});

let flushing = false;
let retryTimer = null;

async function flushQueue() {
  if (flushing || !loadQueue().length) return;
  flushing = true;
  updateBanner();
  let sent = 0;
  try {
    for (let queue = loadQueue(); queue.length; queue = loadQueue()) {
      const entry = queue[0];
      let response;
      try {
        response = await fetch(entry.path, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "X-CSRF-Token": csrfToken(),
            "Content-Type": "application/x-www-form-urlencoded",
            "HX-Request": "true",
          },
          body: new URLSearchParams(entry.pairs),
          signal: AbortSignal.timeout(8000),
        });
      } catch {
        break; // weiterhin offline
      }
      if (response.status >= 500) break; // später erneut versuchen
      // Erfolg oder dauerhafter Fehler (z. B. Artikel inzwischen gelöscht): Eintrag verwerfen
      saveQueue(loadQueue().filter((e) => queueKey(e) !== queueKey(entry)));
      sent += 1;
    }
  } finally {
    flushing = false;
    updateBanner();
  }
  if (loadQueue().length) {
    scheduleRetry();
  } else if (sent) {
    showToast("✓ Änderungen synchronisiert");
    refreshShoppingList();
  }
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(flushQueue, 10000);
}

function refreshShoppingList() {
  if (listEl() && window.htmx) {
    htmx.ajax("GET", `${base()}/shopping/list`, { target: "#shopping-list", swap: "outerHTML" });
  }
}

window.addEventListener("online", () => {
  updateBanner();
  flushQueue();
});
window.addEventListener("offline", updateBanner);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    flushQueue();
    syncWakeLock();
  }
});

function updateBanner() {
  const banner = document.getElementById("sync-banner");
  if (!banner) return;
  const pending = loadQueue().length;
  let text = "";
  const noun = `${pending} Änderung${pending > 1 ? "en" : ""}`;
  if (pending && flushing) text = `🔄 ${noun} werden übertragen …`;
  else if (pending) text = `📴 Keine Verbindung – ${noun} warten auf Übertragung`;
  else if (!navigator.onLine) text = "📴 Offline – Abhaken und Ansehen funktioniert weiterhin";
  banner.textContent = text;
  banner.hidden = !text;
}

/* ------------------------------------------------------------------ Optimistische Anzeige der Einkaufsliste */
// Beim Abhaken wird der Artikel sofort verschoben – der Server-Antwort (oder dem Offline-Puffer) muss man nicht abwarten.

// Kürzel rechts neben dem Namen: offen = wer eingetragen hat, gekauft = wer in den Wagen gelegt hat.
function setBadge(row, text, color, title) {
  let badge = row.querySelector(".who");
  if (!text) {
    badge?.remove();
    return;
  }
  if (!badge) {
    badge = document.createElement("span");
    row.querySelector(".shop-name").after(badge);
  }
  badge.className = `who who-${color}`;
  badge.textContent = text;
  badge.title = title;
  badge.setAttribute("aria-label", title);
}

function setItemState(id, checked) {
  const list = listEl();
  const row = list?.querySelector(`.shop-item[data-id="${CSS.escape(String(id))}"]`);
  if (!row || row.classList.contains("is-done") === checked) return;
  const check = row.querySelector(".check");
  const group = row.closest(".shop-group");
  if (checked) {
    const me = list.dataset;
    setBadge(row, me.myBadge, me.myColor, `in den Wagen gelegt von ${me.myName}`);
  } else {
    setBadge(row, row.dataset.addedBadge, row.dataset.addedColor, `eingetragen von ${row.dataset.addedBy}`);
  }

  if (checked) {
    let done = list.querySelector(".shop-done");
    if (!done) {
      done = document.createElement("section");
      done.className = "shop-group shop-done";
      const heading = document.createElement("h2");
      heading.textContent = "Gekauft";
      done.appendChild(heading);
      list.appendChild(done);
      list.querySelector("#shop-empty")?.remove();
    }
    row.classList.add("is-done");
    row.querySelector(".item-handle")?.remove();
    row.querySelector(".delete-btn")?.remove();
    check.classList.add("is-on");
    check.textContent = "✓";
    check.setAttribute("hx-vals", '{"state": "0"}');
    done.querySelector("h2").after(row);
    if (group && !group.classList.contains("shop-done") && !group.querySelector(".shop-item")) group.remove();
  } else {
    const container = ensureGroup(list, row.dataset.category);
    row.classList.remove("is-done");
    check.classList.remove("is-on");
    check.textContent = "";
    check.setAttribute("hx-vals", '{"state": "1"}');
    if (!row.querySelector(".item-handle")) {
      const handle = document.createElement("span");
      handle.className = "drag-handle item-handle";
      handle.textContent = "⠿";
      row.prepend(handle);
    }
    container.appendChild(row);
    if (!list.querySelector(".shop-done .shop-item")) list.querySelector(".shop-done")?.remove();
  }
  initSortables();
}

function ensureGroup(list, category) {
  const groups = list.querySelector("#shop-groups");
  let section = [...groups.querySelectorAll(".shop-group")].find((g) => g.dataset.category === category);
  if (!section) {
    section = document.createElement("section");
    section.className = "shop-group";
    section.dataset.category = category;
    const heading = document.createElement("h2");
    const name = document.createElement("span");
    name.textContent = category;
    const handle = document.createElement("span");
    handle.className = "drag-handle cat-handle";
    handle.textContent = "⠿";
    heading.append(name, handle);
    const items = document.createElement("div");
    items.className = "shop-items";
    items.dataset.category = category;
    items.dataset.reorderUrl = `${base()}/shopping/reorder`;
    section.append(heading, items);
    groups.appendChild(section);
  }
  list.querySelector("#shop-empty")?.remove();
  return section.querySelector(".shop-items");
}

function applyReorder(category, ids) {
  const container = [...document.querySelectorAll("#shopping-list .shop-items")].find((c) => c.dataset.category === category);
  if (!container) return;
  ids.forEach((id) => {
    const row = container.querySelector(`.shop-item[data-id="${CSS.escape(String(id))}"]`);
    if (row) container.appendChild(row);
  });
}

// Wartende Aktionen auf die (evtl. veraltete) Anzeige anwenden, z. B. nach dem Laden aus dem Offline-Cache.
function applyPending() {
  if (!listEl()) return;
  for (const entry of loadQueue()) {
    const toggle = TOGGLE.exec(entry.path);
    if (toggle) {
      const state = entry.pairs.find(([k]) => k === "state")?.[1];
      if (state === "0" || state === "1") setItemState(toggle[1], state === "1");
    } else if (entry.path.endsWith("/shopping/reorder")) {
      const category = entry.pairs.find(([k]) => k === "category")?.[1];
      applyReorder(category, entry.pairs.filter(([k]) => k === "ids").map(([, v]) => v));
    } else if (entry.path.endsWith("/categories/order")) {
      const groups = document.getElementById("shop-groups");
      entry.pairs
        .filter(([k]) => k === "categories")
        .forEach(([, name]) => {
          const section = [...groups.children].find((g) => g.dataset.category === name);
          if (section) groups.appendChild(section);
        });
    }
  }
}

document.addEventListener("htmx:beforeRequest", (event) => {
  const config = event.detail.requestConfig;
  const match = TOGGLE.exec(config?.path || "");
  if (match && config.verb === "post") {
    const state = requestPairs(config).find(([k]) => k === "state")?.[1];
    if (state === "0" || state === "1") setItemState(match[1], state === "1");
  }
});

/* ------------------------------------------------------------------ Drag & Drop (SortableJS) */

function postOrder(url, values) {
  if (!window.htmx || !listEl()) return;
  htmx.ajax("POST", url, { source: listEl(), target: "#shopping-list", swap: "outerHTML", values });
}

function initSortables() {
  if (!window.Sortable) return;
  // forceFallback: eigener Zeiger-Modus statt natives HTML5-Drag&Drop – gleiches Verhalten für Maus und Touch.
  const common = {
    animation: 150,
    ghostClass: "sortable-ghost",
    chosenClass: "sortable-chosen",
    forceFallback: true,
    fallbackTolerance: 3,
    fallbackOnBody: true,
  };

  document.querySelectorAll("#shopping-list .shop-items").forEach((container) => {
    if (Sortable.get(container)) return;
    Sortable.create(container, {
      ...common,
      handle: ".item-handle",
      draggable: ".shop-item:not(.is-done)",
      onEnd: () => {
        const ids = [...container.querySelectorAll(".shop-item")].map((row) => row.dataset.id);
        postOrder(container.dataset.reorderUrl, { category: container.dataset.category, ids });
      },
    });
  });

  const groups = document.getElementById("shop-groups");
  if (groups && !Sortable.get(groups)) {
    Sortable.create(groups, {
      ...common,
      handle: ".cat-handle",
      draggable: ".shop-group",
      onEnd: () => {
        const categories = [...groups.querySelectorAll(".shop-group")].map((g) => g.dataset.category);
        postOrder(groups.dataset.orderUrl, { categories });
      },
    });
  }
}

/* ------------------------------------------------------------------ Display an (Wake Lock) */

let wakeLock = null;

async function syncWakeLock() {
  const wanted = document.visibilityState === "visible" && document.querySelector("[data-wake-lock]");
  const status = document.querySelector("[data-wake-status]");
  if (wanted && !wakeLock && "wakeLock" in navigator) {
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => {
        wakeLock = null;
        updateWakeStatus();
      });
    } catch {
      wakeLock = null; // z. B. Energiesparmodus
    }
  } else if (!wanted && wakeLock) {
    await wakeLock.release().catch(() => {});
    wakeLock = null;
  }
  updateWakeStatus();
  if (status && wanted && !("wakeLock" in navigator)) {
    status.hidden = false;
    status.textContent = "Display-Sperre nicht verfügbar (benötigt HTTPS)";
  }
}

function updateWakeStatus() {
  const status = document.querySelector("[data-wake-status]");
  if (!status || !("wakeLock" in navigator)) return;
  status.hidden = !wakeLock;
  status.textContent = wakeLock ? "🔆 Display bleibt an, solange diese Seite offen ist" : "";
}

/* ------------------------------------------------------------------ Service Worker & Vorladen */

function registerServiceWorker() {
  if ("serviceWorker" in navigator && document.body.dataset.base) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }
}

// Die Einkaufsliste im Hintergrund frisch halten, damit sie auch ohne Netz aktuell vorliegt.
let prefetchTimer = null;
function prefetchShoppingList(force = false) {
  if (!base() || !navigator.onLine || !navigator.serviceWorker?.controller) return;
  const last = Number(sessionStorage.getItem("mampfplan.prefetch") || 0);
  if (!force && Date.now() - last < 60000) return;
  sessionStorage.setItem("mampfplan.prefetch", String(Date.now()));
  fetch(`${base()}/shopping`, { headers: { "X-Mampf-Prefetch": "1" }, credentials: "same-origin" }).catch(() => {});
}

document.addEventListener("htmx:afterRequest", (event) => {
  const config = event.detail.requestConfig;
  if (event.detail.successful && config?.verb === "post" && (config.path || "").includes("/shopping/")) {
    clearTimeout(prefetchTimer);
    prefetchTimer = setTimeout(() => prefetchShoppingList(true), 1500);
  }
});

/* ------------------------------------------------------------------ Start */

function refreshUi() {
  initSortables();
  applyPending();
  updateBanner();
  syncWakeLock();
}

document.addEventListener("htmx:load", refreshUi);
document.addEventListener("htmx:historyRestore", refreshUi);
document.addEventListener("DOMContentLoaded", () => {
  registerServiceWorker();
  refreshUi();
  flushQueue();
  prefetchShoppingList();
});
