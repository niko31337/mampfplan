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

// Übersetzungen: base.html legt sie als JSON-Datenblock ab (kein Inline-Skript wegen der CSP).
const TEXTS = (() => {
  try {
    return JSON.parse(document.getElementById("i18n")?.textContent || "{}");
  } catch {
    return {};
  }
})();
function t(msgid, vars = {}) {
  return (TEXTS[msgid] || msgid).replace(/%\((\w+)\)s/g, (_, key) => String(vars[key] ?? ""));
}

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
    allBtn.textContent = everythingChecked ? t("Alle wählen") : t("Keine wählen");
  }
  const copyBtn = event.target.closest("[data-copy]");
  if (copyBtn) {
    const text = document.querySelector(copyBtn.dataset.copy)?.textContent.trim();
    if (text && navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => {
        const old = copyBtn.textContent;
        copyBtn.textContent = t("Kopiert ✓");
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

// Formulare mit data-reset-on-success nach erfolgreichem HTMX-Request leeren.
// Die gewählte Einkaufsliste (Radio-Buttons) bleibt stehen – wer mehrere Artikel für denselben Laden einträgt,
// muss nicht jedes Mal neu wählen.
document.addEventListener("htmx:afterRequest", (event) => {
  const el = event.detail.elt;
  if (el?.matches?.("[data-reset-on-success]") && event.detail.successful) {
    const chosen = [...el.querySelectorAll("input[type=radio]:checked")];
    el.reset();
    chosen.forEach((radio) => (radio.checked = true));
  }
});

// Aufklappmenüs (Artikel auf andere Liste, Rezept auf anderen Tag …) schließen, sobald man daneben tippt
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.move-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
  // nach Auswahl eines Menüeintrags schließen
  const choice = event.target.closest(".move-menu .menu-item");
  if (choice) choice.closest("details").open = false;
});

/* ------------------------------------------------------------------ Einkaufslisten ein-/ausklappen */
// Welche Listen (Läden) eingeklappt sind, merkt sich das Gerät in einem Cookie – der Server rendert sie
// dann gleich eingeklappt (kein Aufblitzen nach dem Neuladen der Liste).

document.addEventListener(
  "toggle",
  (event) => {
    const details = event.target;
    if (!details.matches?.("details.shop-list")) return;
    const list = details.closest("#shopping-list");
    const cookie = list?.dataset.closedCookie;
    if (!cookie) return;
    const closed = [...list.querySelectorAll("details.shop-list:not([open])")].map((d) => d.dataset.shop);
    const path = base() || "/";
    document.cookie = `${cookie}=${closed.join(".")}; path=${path}; max-age=31536000; samesite=lax`;
  },
  true
);

/* ------------------------------------------------------------------ Offline-Warteschlange */
// Abhaken und Umsortieren funktionieren auch ohne Verbindung: Die Aktion wird lokal gemerkt
// (localStorage) und später der Reihe nach an den Server geschickt. Die Server-Endpunkte sind
// idempotent (Abhaken setzt einen Zustand, Sortieren setzt eine Reihenfolge) – doppeltes Senden schadet nicht.

const QUEUE_KEY = "mampfplan.queue";
const QUEUEABLE = /\/shopping\/(?:\d+\/toggle|\d+\/move|reorder|categories\/order)$/;
const TOGGLE = /\/shopping\/(\d+)\/toggle$/;
const MOVE = /\/shopping\/(\d+)\/move$/;

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

const pairValue = (pairs, key) => pairs.find(([k]) => k === key)?.[1];

function queueKey(entry) {
  const category = pairValue(entry.pairs, "category") || "";
  const shop = pairValue(entry.pairs, "shop_id") || "";
  return `${entry.path}|${shop}|${category}`;
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
    connectionTrouble = true;
    updateBanner();
    scheduleRetry();
    showToast(t("📴 Gemerkt – wird übertragen, sobald du wieder Verbindung hast."));
  } else {
    showToast(t("Keine Verbindung zum Server."));
  }
}
document.addEventListener("htmx:sendError", onNetworkFailure);
document.addEventListener("htmx:timeout", onNetworkFailure);

document.addEventListener("htmx:responseError", (event) => {
  const xhr = event.detail.xhr;
  // Der Server kann eine eigene Meldung mitschicken (z. B. „Bitte wähle eine Einkaufsliste.“)
  const message = xhr.getResponseHeader("X-Mampf-Message");
  if (message) {
    showToast(decodeURIComponent(message));
    const picker = document.querySelector(".shop-picker");
    picker?.classList.add("needs-choice");
    setTimeout(() => picker?.classList.remove("needs-choice"), 2500);
    return;
  }
  showToast(xhr.status === 403 ? t("Sitzung abgelaufen – bitte Seite neu laden.") : t("Das hat leider nicht geklappt."));
});

let flushing = false;
let retryTimer = null;
// true, solange Übertragungen gescheitert sind – erst dann zeigt das Banner etwas an (sonst würde jede
// normale Sortier-Aktion kurz „wird übertragen“ aufblitzen lassen).
let connectionTrouble = false;

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
        connectionTrouble = true;
        break; // weiterhin offline
      }
      if (response.status >= 500) {
        connectionTrouble = true;
        break; // später erneut versuchen
      }
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
    if (connectionTrouble) showToast(t("✓ Änderungen synchronisiert"));
    connectionTrouble = false;
    updateBanner();
    if (listEl()) refreshShoppingList(); // aktueller Stand vom Server, inkl. Rückgängig-Button
    else refreshUndoButton();
  }
}

// Seiten ohne Einkaufsliste (z. B. Ladenreihenfolge): nur den Rückgängig-Button auf den neuen Stand bringen
function refreshUndoButton() {
  if (window.htmx && document.getElementById("undo-slot")) {
    htmx.ajax("GET", location.pathname + location.search, { target: "#undo-slot", select: "#undo-slot", swap: "outerHTML" });
  }
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(flushQueue, 10000);
}

// Während ein Artikel oder eine Kategorie gezogen wird, darf die Liste nicht ausgetauscht werden –
// sonst verschwindet der laufende Ziehvorgang samt eingeblendeten Ablageflächen. Danach wird nachgeholt.
let dragActive = false;
let refreshAfterDrag = false;

function setDragActive(active) {
  dragActive = active;
  if (!active && refreshAfterDrag) {
    refreshAfterDrag = false;
    refreshShoppingList();
  }
}

document.addEventListener("htmx:beforeSwap", (event) => {
  if (dragActive && event.detail.target?.id === "shopping-list") {
    event.detail.shouldSwap = false;
    refreshAfterDrag = true;
  }
});

function refreshShoppingList() {
  if (dragActive) {
    refreshAfterDrag = true;
    return;
  }
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
  const many = pending > 1;
  const trouble = connectionTrouble || !navigator.onLine;
  if (pending && trouble && flushing) {
    text = t(many ? "🔄 %(num)s Änderungen werden übertragen …" : "🔄 %(num)s Änderung wird übertragen …", { num: pending });
  } else if (pending && trouble) {
    text = t(many ? "📴 Keine Verbindung – %(num)s Änderungen warten auf Übertragung" : "📴 Keine Verbindung – %(num)s Änderung wartet auf Übertragung", { num: pending });
  } else if (!navigator.onLine) text = t("📴 Offline – Abhaken und Ansehen funktioniert weiterhin");
  banner.textContent = text;
  banner.hidden = !text;
}

/* ------------------------------------------------------------------ Optimistische Anzeige der Einkaufsliste */
// Beim Abhaken wird der Artikel sofort verschoben – der Server-Antwort (oder dem Offline-Puffer) muss man nicht abwarten.

// Leere Kategorie: markieren und per hidden-Attribut ausblenden – außer während ein Artikel gezogen wird,
// dann dient sie als Ablagefläche.
function markEmpty(section, empty) {
  if (!section) return;
  section.classList.toggle("is-empty", empty);
  section.hidden = empty && !listEl()?.classList.contains("is-dragging-item");
}

function showDropTargets(show) {
  listEl()?.classList.toggle("is-dragging-item", show);
  document.querySelectorAll("#shopping-list .shop-groups .shop-group.is-empty").forEach((group) => (group.hidden = !show));
}

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
    setBadge(row, me.myBadge, me.myColor, t("in den Wagen gelegt von %(name)s", { name: me.myName }));
  } else {
    setBadge(row, row.dataset.addedBadge, row.dataset.addedColor, t("eingetragen von %(name)s", { name: row.dataset.addedBy }));
  }

  if (checked) {
    let done = list.querySelector(".shop-done");
    if (!done) {
      done = document.createElement("section");
      done.className = "shop-group shop-done";
      const heading = document.createElement("h2");
      heading.textContent = t("Gekauft");
      done.appendChild(heading);
      list.appendChild(done);
    }
    row.classList.add("is-done");
    row.querySelector(".item-handle")?.remove();
    row.querySelector(".delete-btn")?.remove();
    row.querySelector(".move-menu")?.remove();
    check.classList.add("is-on");
    check.textContent = "✓";
    check.setAttribute("hx-vals", '{"state": "0"}');
    done.querySelector("h2").after(row);
    if (group && !group.classList.contains("shop-done") && !group.querySelector(".shop-item")) {
      markEmpty(group, true); // bleibt als (versteckte) Ablagefläche fürs Ziehen erhalten
    }
  } else {
    const container = ensureGroup(list, row.dataset.shop, row.dataset.category);
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

function shopGroups(list, shop) {
  const all = [...list.querySelectorAll(".shop-groups")];
  return all.find((g) => g.dataset.shop === String(shop)) || all[0];
}

function ensureGroup(list, shop, category) {
  const groups = shopGroups(list, shop);
  shop = groups.dataset.shop;
  let section = [...groups.querySelectorAll(".shop-group")].find((g) => g.dataset.category === category);
  if (!section) {
    section = document.createElement("section");
    section.className = "shop-group";
    section.dataset.category = category;
    const heading = document.createElement("h2");
    const name = document.createElement("span");
    name.textContent = t(category);
    const handle = document.createElement("span");
    handle.className = "drag-handle cat-handle";
    handle.textContent = "⠿";
    heading.append(name, handle);
    const items = document.createElement("div");
    items.className = "shop-items";
    items.dataset.category = category;
    items.dataset.shop = shop;
    items.dataset.reorderUrl = `${base()}/shopping/reorder`;
    items.dataset.moveUrl = `${base()}/shopping/__ID__/move`;
    section.append(heading, items);
    groups.appendChild(section);
  }
  markEmpty(section, false);
  groups.closest(".shop-list")?.querySelector(".shop-empty")?.remove();
  return section.querySelector(".shop-items");
}

function applyReorder(shop, category, ids) {
  const containers = [...document.querySelectorAll("#shopping-list .shop-items")];
  const container =
    containers.find((c) => c.dataset.category === category && (!shop || c.dataset.shop === String(shop))) ||
    containers.find((c) => c.dataset.category === category);
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
    const move = MOVE.exec(entry.path);
    const shop = pairValue(entry.pairs, "shop_id");
    if (move) {
      const category = pairValue(entry.pairs, "category");
      const row = listEl().querySelector(`.shop-item[data-id="${CSS.escape(move[1])}"]`);
      if (category && row && !row.classList.contains("is-done")) {
        const source = row.closest(".shop-group");
        const target = shop || row.dataset.shop;
        ensureGroup(listEl(), target, category).appendChild(row);
        row.dataset.category = category;
        row.dataset.shop = target;
        if (source && !source.querySelector(".shop-item")) markEmpty(source, true);
        applyReorder(target, category, entry.pairs.filter(([k]) => k === "ids").map(([, v]) => v));
      }
    } else if (toggle) {
      const state = entry.pairs.find(([k]) => k === "state")?.[1];
      if (state === "0" || state === "1") setItemState(toggle[1], state === "1");
    } else if (entry.path.endsWith("/shopping/reorder")) {
      const category = pairValue(entry.pairs, "category");
      applyReorder(shop, category, entry.pairs.filter(([k]) => k === "ids").map(([, v]) => v));
    } else if (entry.path.endsWith("/categories/order")) {
      const groups = shopGroups(listEl(), shop);
      if (!groups) continue;
      const value = (key) => entry.pairs.find(([k]) => k === key)?.[1];
      const section = (name) => [...groups.children].find((g) => g.dataset.category === name);
      const moved = section(value("category"));
      if (moved) {
        const before = section(value("before"));
        const after = section(value("after"));
        if (before) groups.insertBefore(moved, before);
        else if (after) after.after(moved);
      } else {
        entry.pairs
          .filter(([k]) => k === "categories")
          .forEach(([, name]) => {
            const s = section(name);
            if (s) groups.appendChild(s);
          });
      }
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

// Sortieren/Verschieben: Die Seite zeigt nach dem Ablegen schon den neuen Stand. Die Änderung geht über
// dieselbe Warteschlange wie Offline-Aktionen – der Reihe nach, ohne dass bei schnellem Ziehen über eine
// langsame Leitung etwas verloren geht. Danach wird die Liste einmal frisch geladen (nie während des Ziehens).
function postOrder(url, values) {
  const pairs = [];
  for (const [key, value] of Object.entries(values)) {
    (Array.isArray(value) ? value : [value]).forEach((v) => pairs.push([key, String(v)]));
  }
  enqueue({ path: url, pairs });
  flushQueue();
}

function visibleSibling(element, direction) {
  let sibling = direction === "next" ? element.nextElementSibling : element.previousElementSibling;
  while (sibling && (sibling.hidden || sibling.classList.contains("is-empty"))) {
    sibling = direction === "next" ? sibling.nextElementSibling : sibling.previousElementSibling;
  }
  return sibling;
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
    // Automatisch scrollen, sobald der Finger in die Nähe des Rands kommt. Der Bereich ist bewusst groß:
    // Unten liegen die Navigationsleiste der App und die Gestenleiste des Handys.
    scroll: true,
    scrollSensitivity: 120,
    scrollSpeed: 18,
    bubbleScroll: true,
  };

  document.querySelectorAll("#shopping-list .shop-items").forEach((container) => {
    if (Sortable.get(container)) return;
    Sortable.create(container, {
      ...common,
      group: "shop-items", // gemeinsame Gruppe: Artikel dürfen in andere Kategorien gezogen werden
      handle: ".item-handle",
      draggable: ".shop-item:not(.is-done)",
      // Beim Ziehen eines Artikels auch leere Kategorien als Ablagefläche zeigen
      onStart: () => {
        setDragActive(true);
        showDropTargets(true);
      },
      onEnd: (event) => {
        showDropTargets(false);
        setDragActive(false);
        const target = event.to;
        const ids = [...target.querySelectorAll(".shop-item")].map((row) => row.dataset.id);
        const shop = target.dataset.shop;
        if (event.from === target) {
          if (event.oldIndex !== event.newIndex) {
            postOrder(target.dataset.reorderUrl, { shop_id: shop, category: target.dataset.category, ids });
          }
          return;
        }
        // auch in eine andere Liste (Laden) möglich
        const row = event.item;
        row.dataset.category = target.dataset.category;
        row.dataset.shop = shop;
        markEmpty(target.closest(".shop-group"), false);
        target.closest(".shop-list")?.querySelector(".shop-empty")?.remove();
        if (!event.from.querySelector(".shop-item")) markEmpty(event.from.closest(".shop-group"), true);
        postOrder(target.dataset.moveUrl.replace("__ID__", row.dataset.id), {
          shop_id: shop,
          category: target.dataset.category,
          ids,
        });
      },
    });
  });

  // Kategorien in einer Liste: vor die nächste (bzw. hinter die vorige) sichtbare Kategorie einfügen.
  // Der Server merkt sich das dauerhaft – auch relativ zu gerade leeren, ausgeblendeten Kategorien.
  document.querySelectorAll("#shopping-list .shop-groups").forEach((groups) => initCategorySortable(groups, common));

  // Seite „Ladenreihenfolge“: alle Kategorien einer Liste, die vollständige Reihenfolge wird übernommen
  const storeOrder = document.getElementById("category-order");
  if (storeOrder && !Sortable.get(storeOrder)) {
    Sortable.create(storeOrder, {
      ...common,
      handle: ".cat-handle",
      onStart: () => setDragActive(true),
      onEnd: (event) => {
        setDragActive(false);
        if (event.oldIndex === event.newIndex) return;
        const categories = [...storeOrder.children].map((row) => row.dataset.category);
        postOrder(storeOrder.dataset.orderUrl, { shop_id: storeOrder.dataset.shop, categories });
      },
    });
  }

  // Einstellungen: Reihenfolge der Einkaufslisten
  const shopOrder = document.getElementById("shop-order");
  if (shopOrder && !Sortable.get(shopOrder)) {
    Sortable.create(shopOrder, {
      ...common,
      handle: ".shop-handle",
      onEnd: (event) => {
        if (event.oldIndex === event.newIndex) return;
        const rows = [...shopOrder.children];
        rows.forEach((row, index) => {
          const [up, down] = row.querySelectorAll(".no-js-order button");
          if (up) up.disabled = index === 0;
          if (down) down.disabled = index === rows.length - 1;
        });
        postOrder(shopOrder.dataset.orderUrl, { ids: rows.map((row) => row.dataset.id) });
      },
    });
  }

  // Wochenplan: Einträge auf einen anderen Tag ziehen (dort kommen sie zusätzlich dazu)
  document.querySelectorAll("#week .day-entries").forEach((container) => {
    if (Sortable.get(container)) return;
    Sortable.create(container, {
      ...common,
      group: "plan-entries",
      handle: ".entry-handle",
      draggable: ".entry",
      onStart: () => document.getElementById("week")?.classList.add("is-dragging-entry"),
      onEnd: (event) => {
        document.getElementById("week")?.classList.remove("is-dragging-entry");
        if (event.from === event.to && event.oldIndex === event.newIndex) return;
        const week = document.getElementById("week");
        const ids = [...event.to.querySelectorAll(".entry")].map((entry) => entry.dataset.id);
        moveEntryToDay(week.dataset.moveUrl.replace("__ID__", event.item.dataset.id), event.to.dataset.day, ids);
      },
    });
  });
}

// Eintrag verschieben und danach die Woche vom Server neu anzeigen (inkl. Rückgängig-Button und Hinweis)
async function moveEntryToDay(url, day, ids) {
  const body = new URLSearchParams([["day", day], ...ids.map((id) => ["ids", id])]);
  try {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken(), "Content-Type": "application/x-www-form-urlencoded", "HX-Request": "true" },
      body,
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error(String(response.status));
    const html = await response.text();
    if (window.htmx?.swap) htmx.swap("#week", html, { swapStyle: "outerHTML" });
    else location.reload();
  } catch {
    showToast(t("Das hat leider nicht geklappt."));
    location.reload();
  }
}

function initCategorySortable(groups, common) {
  if (Sortable.get(groups)) return;
  Sortable.create(groups, {
    ...common,
    handle: ".cat-handle",
    draggable: ".shop-group:not(.is-empty)",
    onStart: () => setDragActive(true),
    onEnd: (event) => {
      setDragActive(false);
      if (event.oldIndex === event.newIndex) return;
      const moved = event.item;
      const values = { category: moved.dataset.category };
      const next = visibleSibling(moved, "next");
      const previous = visibleSibling(moved, "previous");
      if (next) values.before = next.dataset.category;
      else if (previous) values.after = previous.dataset.category;
      else return;
      values.shop_id = groups.dataset.shop;
      postOrder(groups.dataset.orderUrl, values);
    },
  });
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
    status.textContent = t("Display-Sperre nicht verfügbar (benötigt HTTPS)");
  }
}

function updateWakeStatus() {
  const status = document.querySelector("[data-wake-status]");
  if (!status || !("wakeLock" in navigator)) return;
  status.hidden = !wakeLock;
  status.textContent = wakeLock ? t("🔆 Display bleibt an, solange diese Seite offen ist") : "";
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
