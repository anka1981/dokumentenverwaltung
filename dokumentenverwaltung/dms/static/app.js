"use strict";

// ------------------------------------------------------------------ Helfer

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const main = $("#main");

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Diagnose ins Add-on-Protokoll (Einstellungen → Add-ons → Dokumentenverwaltung → Protokoll)
function clientLog(msg) {
  try {
    const body = JSON.stringify({ msg: String(msg) });
    if (!(navigator.sendBeacon && navigator.sendBeacon("api/clientlog", new Blob([body], { type: "application/json" })))) {
      fetch("api/clientlog", { method: "POST", body, headers: { "Content-Type": "application/json" } }).catch(() => {});
    }
  } catch { /* Diagnose darf nie stören */ }
}
window.addEventListener("error", e => clientLog(`JS-Fehler: ${e.message} (${e.filename}:${e.lineno})`));
window.addEventListener("unhandledrejection", e => clientLog(`JS-Fehler (async): ${e.reason?.message || e.reason}`));

async function api(path, opts = {}) {
  const { timeout = 30000, ...rest } = opts;
  const init = { ...rest, headers: { ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch("api" + path, { ...init, signal: ctrl.signal });
  } catch (e) {
    const msg = e.name === "AbortError" ? `Keine Antwort vom Server (${path})` : `Verbindung fehlgeschlagen (${path})`;
    clientLog(msg);
    throw new Error(msg);
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Fehler ${res.status}`);
    err.data = data;
    throw err;
  }
  return data;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 6000 : 3000);
}

function busy(text, progress) {
  $("#busy").hidden = text === false;
  if (text !== false) {
    $("#busy-text").textContent = text;
    const p = $("#busy-progress");
    p.hidden = progress === undefined;
    if (progress !== undefined) p.value = progress;
  }
}

const fmtDate = iso => (iso ? new Date(iso + "T00:00:00").toLocaleDateString("de-DE") : "");
const fmtAmount = a => (a == null ? "" : a.toLocaleString("de-DE", { style: "currency", currency: "EUR" }));
const pct = s => Math.round(s * 100) + " %";
const fmtBytes = b => b >= 1e9 ? (b / 1e9).toFixed(2).replace(".", ",") + " GB"
  : b >= 1e6 ? (b / 1e6).toFixed(1).replace(".", ",") + " MB" : Math.round(b / 1e3) + " KB";

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// Ordnerbaum als flache Liste mit Einrückung (für <select>)
let folderCache = null;
async function folders(reload = false) {
  if (!folderCache || reload) folderCache = await api("/folders");
  return folderCache;
}
function flatten(tree, depth = 0, out = []) {
  for (const f of tree) {
    out.push({ ...f, depth });
    flatten(f.children, depth + 1, out);
  }
  return out;
}
function folderOptions(tree, selected, placeholder = "– Ordner wählen –") {
  return `<option value="">${esc(placeholder)}</option>` + flatten(tree).map(f =>
    `<option value="${f.id}" ${f.id === selected ? "selected" : ""}>${esc(f.path)}</option>`
  ).join("");
}

async function refreshCount() {
  try {
    const s = await api("/status");
    const b = $("#inbox-count");
    b.textContent = s.eingang;
    b.hidden = !s.eingang;
    const drive = await api("/drive");
    $("#btn-drive").hidden = !drive.connected;
  } catch { /* offline */ }
}

// ------------------------------------------------------------------ Router

const views = { eingang: viewInbox, archiv: viewArchive, ordner: viewFolders, doc: viewDocument, einstellungen: viewSettings };

async function route() {
  const [name, arg] = (location.hash.slice(1).split("?")[0] || "eingang").split("/");
  $$(".topbar nav a").forEach(a => a.classList.toggle("active", a.dataset.view === name));
  $(".fab-group").hidden = name === "doc" || name === "einstellungen"; // Knöpfe würden Formulare verdecken
  const view = views[name] || viewInbox;
  main.innerHTML = `<div class="empty"><div class="spinner" style="margin:auto"></div></div>`;
  try {
    await view(arg);
  } catch (e) {
    clientLog(`Ansicht ${name}: ${e.message}`);
    main.innerHTML = `<div class="card warning">${esc(e.message)}</div>`;
  }
  refreshCount();
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------ Dokumentliste

function docCard(d) {
  const ext = (d.original_name.split(".").pop() || "").slice(0, 4);
  const thumb = d.has_preview
    ? `<div class="thumb" style="background-image:url('api/documents/${d.id}/preview')"></div>`
    : `<div class="thumb">${esc(ext)}</div>`;
  const top = d.suggestion && d.suggestion[0];
  let where = "";
  if (d.status === "abgelegt") where = `📁 ${esc(d.folder_path || "")}`;
  else if (top) where = `Vorschlag: ${esc(top.path)} <span class="suggest-pill ${top.score < 0.5 ? "low" : ""}">${pct(top.score)}</span>`;
  const snippet = d.snippet
    ? `<div class="snippet">${esc(d.snippet).replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>")}</div>` : "";
  const meta = [fmtDate(d.doc_date), d.correspondent, fmtAmount(d.amount)].filter(Boolean).map(esc).join(" · ");
  return `<a class="card doc-item" href="#doc/${d.id}">
    ${thumb}
    <div class="grow">
      <h3>${esc(d.title)}</h3>
      <div class="meta">${meta || esc(d.original_name)}</div>
      <div class="meta">${where}</div>
      ${snippet}
      <div class="chips" style="margin-top:6px">${d.tags.slice(0, 6).map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>
    </div>
  </a>`;
}

async function viewInbox() {
  const docs = await api("/documents?status=eingang");
  main.innerHTML = `
    <div class="row between wrap" style="margin-bottom:12px">
      <h2 style="margin:0">Eingang</h2>
    </div>
    <p class="muted small" style="margin-top:0">Neue Dokumente prüfen und die vorgeschlagene Ablage bestätigen oder ändern.</p>
    ${docs.length ? `<div class="doc-list">${docs.map(docCard).join("")}</div>` : `
      <div class="card empty">
        <p><strong>Der Eingang ist leer.</strong></p>
        <p>Papierdokumente mit der <b>Google-Drive-App</b> in den Ordner <b>Dokumente-Eingang</b> scannen
        (dort <b>＋ → Scannen</b>) – sie erscheinen automatisch hier, sofort mit <b>⟳ Drive</b>.
        Mit <b>Datei</b> lassen sich Word-, PDF- und andere Dateien hochladen; am Computer auch
        einfach in dieses Fenster ziehen.</p>
      </div>`}`;
}

async function runDriveImport() {
  try {
    const before = await api("/drive/run", { method: "POST" });
    toast(before.started ? "Abruf aus Google Drive läuft im Hintergrund…" : "Ein Abruf läuft bereits…");
    const lastRun = before.last_run;
    // Auf das Ende warten (Texterkennung auf dem Raspberry Pi kann Minuten dauern)
    for (let i = 0; i < 180; i++) {
      await new Promise(r => setTimeout(r, 5000));
      const st = await api("/drive");
      if (!st.running && st.last_run !== lastRun) {
        const r = st.last_result;
        const parts = [`${r.imported} neu`];
        if (r.duplicates) parts.push(`${r.duplicates} schon vorhanden`);
        toast(`Google Drive: ${parts.join(", ")}` + (r.errors.length ? ` – ${r.errors[0]}` : ""), r.errors.length > 0);
        const view = (location.hash.slice(1).split("?")[0] || "eingang").split("/")[0];
        if (view === "eingang" || view === "einstellungen") route();
        return;
      }
    }
  } catch (e) { toast(e.message, true); }
}

async function viewArchive() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const state = { q: params.get("q") || "", folder: params.get("folder") ? +params.get("folder") : null, tag: params.get("tag") || "" };
  const [tree, tags] = await Promise.all([folders(true), api("/tags")]);

  function treeHtml(nodes) {
    return `<ul class="tree">${nodes.map(n => `<li>
      <a href="#" data-folder="${n.id}" class="${state.folder === n.id ? "active" : ""}"><span>${esc(n.name)}</span><span class="count">${n.total || ""}</span></a>
      ${n.children.length ? treeHtml(n.children) : ""}</li>`).join("")}</ul>`;
  }

  main.innerHTML = `
    <div class="archive">
      <aside class="card tree-card">
        <h3>Ablage</h3>
        <ul class="tree"><li><a href="#" data-folder="" class="${state.folder ? "" : "active"}"><span>Alle Dokumente</span></a></li></ul>
        ${treeHtml(tree)}
      </aside>
      <section class="stack">
        <div class="card stack">
          <input type="search" id="q" placeholder="Volltextsuche: Wörter, Absender, Schlagwörter…" value="${esc(state.q)}" autocomplete="off">
          <select class="folder-select" id="folder-select">${folderOptions(tree, state.folder, "Alle Ordner")}</select>
          <div class="chips" id="tag-chips">${tags.slice(0, 30).map(t =>
            `<a href="#" class="chip ${state.tag === t.name ? "active" : ""}" data-tag="${esc(t.name)}">${esc(t.name)} <span class="muted">${t.count}</span></a>`).join("")}</div>
        </div>
        <div id="results" class="doc-list"></div>
      </section>
    </div>`;

  async function load() {
    const qs = new URLSearchParams();
    if (state.q) qs.set("q", state.q);
    if (state.folder) qs.set("folder_id", state.folder);
    if (state.tag) qs.set("tag", state.tag);
    history.replaceState(null, "", "#archiv" + (qs.toString() ? "?" + qs.toString().replace("folder_id", "folder") : ""));
    const docs = await api("/documents?" + qs);
    $("#results").innerHTML = docs.length ? docs.map(docCard).join("")
      : `<div class="card empty">Keine Dokumente gefunden.</div>`;
  }

  $("#q").addEventListener("input", debounce(e => { state.q = e.target.value.trim(); load(); }, 250));
  $("#folder-select").addEventListener("change", e => { state.folder = e.target.value ? +e.target.value : null; load(); });
  $$(".tree a").forEach(a => a.addEventListener("click", e => {
    e.preventDefault();
    state.folder = a.dataset.folder ? +a.dataset.folder : null;
    $$(".tree a").forEach(x => x.classList.toggle("active", x === a));
    load();
  }));
  $$("#tag-chips .chip").forEach(c => c.addEventListener("click", e => {
    e.preventDefault();
    state.tag = state.tag === c.dataset.tag ? "" : c.dataset.tag;
    $$("#tag-chips .chip").forEach(x => x.classList.toggle("active", x.dataset.tag === state.tag));
    load();
  }));
  await load();
}

// --------------------------------------------------------- Prüfen & Ablegen

function tagInput(container, initial, allTags) {
  let tags = [...initial];
  const listId = "tag-options";
  function render() {
    container.innerHTML = tags.map((t, i) =>
      `<span class="chip">${esc(t)}<button type="button" data-i="${i}" aria-label="${esc(t)} entfernen">×</button></span>`).join("")
      + `<input type="text" list="${listId}" placeholder="Schlagwort hinzufügen…">
         <datalist id="${listId}">${allTags.map(t => `<option value="${esc(t.name)}">`).join("")}</datalist>`;
    $$("button", container).forEach(b => b.addEventListener("click", () => { tags.splice(+b.dataset.i, 1); render(); }));
    const inp = $("input", container);
    const add = () => {
      inp.value.split(/[,;]/).map(s => s.trim()).filter(Boolean).forEach(v => {
        if (!tags.some(t => t.toLowerCase() === v.toLowerCase())) tags.push(v);
      });
      inp.value = "";
      render();
      $("input", container).focus();
    };
    inp.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); if (inp.value.trim()) add(); }
      else if (e.key === "Backspace" && !inp.value && tags.length) { tags.pop(); render(); $("input", container).focus(); }
    });
    inp.addEventListener("change", () => { if (allTags.some(t => t.name === inp.value)) add(); });
    inp.addEventListener("blur", () => { if (inp.value.trim()) add(); });
  }
  render();
  return () => {
    const pending = $("input", container).value.trim();
    return pending ? [...tags, pending] : tags;
  };
}

async function viewDocument(id) {
  const [d, tree, allTags] = await Promise.all([api("/documents/" + id), folders(true), api("/tags")]);
  const inbox = d.status === "eingang";
  const top = d.suggestion[0];
  const selected = inbox ? (top ? top.folder_id : null) : d.folder_id;

  const preview = d.has_preview
    ? `<a href="api/documents/${d.id}/content" target="_blank" rel="noopener"><img src="api/documents/${d.id}/preview?${Date.now()}" alt="Vorschau"></a>`
    : `<div class="card no-preview muted">Keine Vorschau für ${esc(d.original_name.split(".").pop().toUpperCase())}-Dateien.<br>Der erkannte Text steht unten.</div>`;

  const alternatives = d.suggestion.slice(1).filter(s => s.score >= 0.15);
  let suggestionHtml = "";
  if (inbox) {
    suggestionHtml = top ? `
      <div class="card suggestion-box ${top.score < 0.5 ? "low" : ""} stack">
        <div class="row between wrap">
          <span class="muted small">Vorgeschlagene Ablage</span>
          <span class="suggest-pill ${top.score < 0.5 ? "low" : ""}">Sicherheit ${pct(top.score)}</span>
        </div>
        <div class="path">📁 ${esc(top.path)}</div>
        <div class="small muted">${top.reasons.map(esc).join(" · ")}</div>
        ${alternatives.length ? `<div class="alt-list"><span class="small muted">Alternativen:</span>${alternatives.map(s =>
          `<button type="button" class="chip" data-pick="${s.folder_id}" title="${esc(s.reasons.join(" · "))}">${esc(s.path)} (${pct(s.score)})</button>`).join("")}</div>` : ""}
      </div>` : `<div class="card warning">Kein Vorschlag möglich – bitte einen Ordner wählen.</div>`;
  }

  main.innerHTML = `
    <p><a href="${inbox ? "#eingang" : "#archiv"}">← zurück</a></p>
    <div class="review">
      <div class="preview stack">
        ${preview}
        <div class="row wrap">
          <a class="btn small" href="api/documents/${d.id}/content" target="_blank" rel="noopener">Original öffnen</a>
          <a class="btn small" href="api/documents/${d.id}/content?download=1">Herunterladen</a>
        </div>
      </div>
      <form class="stack" id="doc-form">
        <h2>${inbox ? "Dokument prüfen" : "Dokument"}</h2>
        ${d.warnings.map(w => `<div class="warning">${esc(w)}</div>`).join("")}
        ${suggestionHtml}
        <div class="card stack">
          <div class="row" style="align-items:flex-end">
            <label class="grow">Ordner
              <select id="f-folder" required>${folderOptions(tree, selected)}</select>
            </label>
            <button type="button" class="btn" id="new-folder" title="Neuen Ordner anlegen">+ Ordner</button>
          </div>
          <label>Titel <input type="text" id="f-title" value="${esc(d.title)}" required></label>
          <div class="grid2">
            <label>Datum <input type="date" id="f-date" value="${esc(d.doc_date || "")}"></label>
            <label>Betrag (€) <input type="text" inputmode="decimal" id="f-amount" value="${d.amount == null ? "" : String(d.amount).replace(".", ",")}"></label>
          </div>
          <div class="grid2">
            <label>Absender <input type="text" id="f-corr" value="${esc(d.correspondent || "")}"></label>
            <label>Dokumentart <input type="text" id="f-type" value="${esc(d.doc_type || "")}"></label>
          </div>
          <label>Schlagwörter <div class="tag-input" id="f-tags"></div></label>
          <label>Notizen <textarea id="f-notes">${esc(d.notes || "")}</textarea></label>
          ${d.iban ? `<div class="small muted">IBAN im Dokument: ${esc(d.iban)}</div>` : ""}
          <div class="row wrap end">
            <button type="button" class="btn ghost danger" id="delete">Löschen</button>
            ${inbox ? "" : `<button type="button" class="btn" id="save">Speichern</button>`}
            <button type="submit" class="btn primary">${inbox ? "✓ Bestätigen &amp; ablegen" : "Ablage ändern"}</button>
          </div>
        </div>
        <details class="card text-box">
          <summary>Erkannter Text (${esc({ text: "Textebene", ocr: "Texterkennung", mixed: "Text + OCR", none: "–" }[d.extract_method] || d.extract_method)}, ${d.pages} Seite${d.pages === 1 ? "" : "n"})</summary>
          <pre>${esc(d.text || "(kein Text erkannt)")}</pre>
        </details>
        <div class="small muted">
          Datei: ${esc(d.original_name)} · eingegangen ${esc(d.created_at)}${d.filed_at ? " · abgelegt " + esc(d.filed_at) : ""}
          ${d.status === "abgelegt" ? `<br>Ablageort: <code>${esc(d.file_path)}</code>` : ""}
        </div>
      </form>
    </div>`;

  const getTags = tagInput($("#f-tags"), d.tags, allTags);
  const folderSel = $("#f-folder");
  $$("[data-pick]").forEach(b => b.addEventListener("click", () => { folderSel.value = b.dataset.pick; folderSel.focus(); }));

  const fields = () => ({
    title: $("#f-title").value,
    doc_date: $("#f-date").value || null,
    amount: $("#f-amount").value.trim() || null,
    correspondent: $("#f-corr").value,
    doc_type: $("#f-type").value,
    notes: $("#f-notes").value,
    tags: getTags(),
  });

  $("#new-folder").addEventListener("click", async () => {
    const parentId = folderSel.value ? +folderSel.value : null;
    const parentName = parentId ? flatten(tree).find(f => f.id === parentId).path : null;
    const name = prompt(parentName ? `Neuer Unterordner in „${parentName}“:\n(leer lassen und Abbrechen, um oben einen Ordner zu wählen)` : "Name des neuen Ordners:");
    if (!name) return;
    try {
      const f = await api("/folders", { method: "POST", json: { name, parent_id: parentId } });
      const t = await folders(true);
      folderSel.innerHTML = folderOptions(t, f.id);
      toast(`Ordner „${f.path}“ angelegt`);
    } catch (e) { toast(e.message, true); }
  });

  $("#doc-form").addEventListener("submit", async e => {
    e.preventDefault();
    if (!folderSel.value) { toast("Bitte einen Ordner wählen", true); return; }
    try {
      const res = await api(`/documents/${d.id}/file`, { method: "POST", json: { folder_id: +folderSel.value, ...fields() } });
      toast(`Abgelegt in ${res.folder_path}`);
      if (inbox) {
        const next = await api("/documents?status=eingang");
        location.hash = next.length ? "#doc/" + next[0].id : "#eingang";
      } else route();
    } catch (err) { toast(err.message, true); }
  });

  $("#save")?.addEventListener("click", async () => {
    try {
      await api(`/documents/${d.id}`, { method: "PATCH", json: fields() });
      toast("Gespeichert");
      route();
    } catch (err) { toast(err.message, true); }
  });

  $("#delete").addEventListener("click", async () => {
    if (!confirm(`„${d.title}“ endgültig löschen?`)) return;
    await api("/documents/" + d.id, { method: "DELETE" });
    toast("Gelöscht");
    location.hash = inbox ? "#eingang" : "#archiv";
  });
}

// ------------------------------------------------------- Ordnerverwaltung

async function viewFolders() {
  const tree = await folders(true);

  function node(f) {
    return `<div class="folder-edit" data-id="${f.id}">
      <div class="folder-head">
        <input type="text" class="f-name" value="${esc(f.name)}" aria-label="Ordnername">
        <span class="muted small">${f.total} Dok.</span>
        <button type="button" class="btn small f-add" title="Unterordner anlegen">+ Unterordner</button>
        <button type="button" class="btn small ghost danger f-del" title="Ordner löschen">Löschen</button>
      </div>
      <label style="margin-top:6px">Erkennungsbegriffe (kommagetrennt, auch Wortteile wie „versicherung“)
        <textarea class="f-kw" rows="2">${esc(f.keywords)}</textarea>
      </label>
      <div class="row end"><button type="button" class="btn small primary f-save" hidden>Speichern</button></div>
      ${f.children.map(node).join("")}
    </div>`;
  }

  main.innerHTML = `
    <div class="stack">
      <div class="card">
        <h2>Ablagestruktur</h2>
        <p class="muted small">Die Begriffe bestimmen, welcher Ordner für neue Dokumente vorgeschlagen wird.
        Zusätzlich lernt das System aus jeder Bestätigung und Korrektur – je mehr abgelegt ist, desto besser die Vorschläge.
        Umbenennen verschiebt die Dateien im Ablageverzeichnis mit.</p>
        <div class="row">
          <input type="text" id="root-name" placeholder="Neuer Hauptordner…">
          <button class="btn" id="root-add">Anlegen</button>
        </div>
      </div>
      <div class="card">${tree.map(node).join("") || '<p class="muted">Noch keine Ordner.</p>'}</div>
    </div>`;

  $("#root-add").addEventListener("click", async () => {
    const name = $("#root-name").value.trim();
    if (!name) return;
    try { await api("/folders", { method: "POST", json: { name } }); route(); } catch (e) { toast(e.message, true); }
  });

  $$(".folder-edit").forEach(el => {
    const id = +el.dataset.id;
    const head = el.querySelector(":scope > .folder-head");
    const nameIn = $(".f-name", head);
    const kwIn = el.querySelector(":scope > label .f-kw");
    const save = el.querySelector(":scope > .row .f-save");
    const dirty = () => (save.hidden = false);
    nameIn.addEventListener("input", dirty);
    kwIn.addEventListener("input", dirty);
    save.addEventListener("click", async () => {
      try {
        await api("/folders/" + id, { method: "PATCH", json: { name: nameIn.value, keywords: kwIn.value } });
        toast("Ordner gespeichert");
        save.hidden = true;
        folderCache = null;
      } catch (e) { toast(e.message, true); }
    });
    $(".f-add", head).addEventListener("click", async () => {
      const name = prompt(`Unterordner in „${nameIn.value}“:`);
      if (!name) return;
      try { await api("/folders", { method: "POST", json: { name, parent_id: id } }); route(); } catch (e) { toast(e.message, true); }
    });
    $(".f-del", head).addEventListener("click", async () => {
      if (!confirm(`Ordner „${nameIn.value}“ löschen?`)) return;
      try { await api("/folders/" + id, { method: "DELETE" }); route(); } catch (e) { toast(e.message, true); }
    });
  });
}

// ------------------------------------------------------------ Einstellungen

async function viewSettings() {
  const [st, sys] = await Promise.all([api("/drive"), api("/status")]);
  const last = st.last_result;
  const lastText = st.last_run
    ? `Letzter Abruf: ${new Date(st.last_run).toLocaleString("de-DE")} – ${last.imported} neu` +
      (last.duplicates ? `, ${last.duplicates} schon vorhanden` : "") +
      (last.errors.length ? `<br><span style="color:var(--danger)">${last.errors.map(esc).join("<br>")}</span>` : "")
    : "Noch nicht abgerufen.";

  main.innerHTML = `
    <div class="stack">
      <div class="card stack">
        <h2>Import aus Google Drive</h2>
        <p class="muted small" style="margin:0">Mit der <b>Google-Drive-App</b> (＋ → Scannen) direkt in den Ordner
        <b>${esc(st.folder)}</b> scannen. Neue Dateien erscheinen dann automatisch im Eingang und werden in Drive
        nach <b>${esc(st.folder)}/importiert</b> verschoben.</p>
        <div class="row wrap">
          <span class="suggest-pill ${st.connected ? "" : "low"}">${st.connected ? "✓ Mit Google verbunden" : "Nicht verbunden"}</span>
          ${st.connected ? `<button class="btn small" id="d-run">Jetzt abrufen</button>
                            <button class="btn small ghost danger" id="d-disconnect">Trennen</button>` : ""}
        </div>
        ${st.connected ? `<div class="small muted">${lastText}</div>` : ""}
      </div>

      <div class="card stack">
        <h3>${st.connected ? "Einstellungen" : "1. Zugangsdaten aus Google Cloud"}</h3>
        ${st.connected ? "" : `<p class="muted small" style="margin:0">In der Google Cloud Console unter
          <b>APIs und Dienste → Anmeldedaten → Anmeldedaten erstellen → OAuth-Client-ID</b> einen Client vom Typ
          <b>Desktop-App</b> anlegen und Client-ID und Clientschlüssel hier eintragen.</p>`}
        <label>Client-ID <input type="text" id="d-id" value="${esc(st.client_id)}" autocomplete="off"></label>
        <label>Clientschlüssel <input type="text" id="d-secret" placeholder="${st.has_secret ? "gespeichert – nur zum Ändern ausfüllen" : ""}" autocomplete="off"></label>
        <div class="grid2">
          <label>Drive-Ordner <input type="text" id="d-folder" value="${esc(st.folder)}"></label>
          <label>Abruf alle … Minuten <input type="number" id="d-interval" min="1" max="1440" value="${st.interval}"></label>
        </div>
        <label class="row" style="color:var(--text)"><input type="checkbox" id="d-enabled" ${st.enabled ? "checked" : ""}> Automatisch abrufen</label>
        <div class="row end"><button class="btn" id="d-save">Speichern</button></div>
      </div>

      ${st.connected ? "" : `
      <div class="card stack">
        <h3>2. Mit Google verbinden</h3>
        <p class="muted small" style="margin:0">Am besten am PC. Nach „Mit Google verbinden“ in dem neuen Fenster das Konto wählen.
        Bei „Google hat diese App nicht überprüft“ auf <b>Erweitert → Weiter zu …</b> tippen und den Zugriff erlauben.
        Danach zeigt der Browser <b>„Seite nicht erreichbar“</b> – das ist richtig so.
        <b>Die komplette Adresse aus der Adressleiste</b> (beginnt mit <code>http://127.0.0.1:8765/?</code>) kopieren und unten einfügen.</p>
        <div class="row wrap"><button class="btn primary" id="d-auth" ${st.client_id && st.has_secret ? "" : "disabled"}>Mit Google verbinden</button></div>
        <label>Kopierte Adresse <input type="text" id="d-url" placeholder="http://127.0.0.1:8765/?state=…&code=…" autocomplete="off"></label>
        <div class="row end"><button class="btn primary" id="d-connect">Verbindung herstellen</button></div>
      </div>`}
      <div class="card">
        <h3>Speicherplatz</h3>
        <p class="small" style="margin:0">${fmtBytes(sys.bytes_stored)} belegt
          ${sys.bytes_original > sys.bytes_stored ? `– ${fmtBytes(sys.bytes_original - sys.bytes_stored)}
          (${Math.round((1 - sys.bytes_stored / sys.bytes_original) * 100)} %) durch verlustfreie Kompression gespart` : ""}.
          ${sys.optimize_pending ? `<br><span class="muted">${sys.optimize_pending} Dokument(e) werden noch im Hintergrund optimiert.</span>` : ""}</p>
        <p class="small muted" style="margin:6px 0 0">Die Dateien bleiben normale PDF-, Bild- und Office-Dateien;
          Inhalt und Bildqualität ändern sich nicht.</p>
      </div>
      <p class="small muted" style="text-align:center">Dokumentenverwaltung ${esc(sys.version)} ·
        Texterkennung ${sys.ocr ? "aktiv" : "nicht verfügbar"} · Verbindung ${esc(location.protocol.replace(":", ""))}</p>
    </div>`;

  $("#d-save").addEventListener("click", async () => {
    try {
      await api("/drive", { method: "PUT", json: {
        client_id: $("#d-id").value, client_secret: $("#d-secret").value || null,
        folder: $("#d-folder").value, interval: $("#d-interval").value, enabled: $("#d-enabled").checked,
      } });
      toast("Gespeichert");
      route();
    } catch (e) { toast(e.message, true); }
  });
  $("#d-auth")?.addEventListener("click", async () => {
    const win = window.open("", "_blank"); // vor dem await öffnen, sonst blockiert der Popup-Blocker
    try {
      const { url } = await api("/drive/auth", { method: "POST" });
      if (win) win.location = url; else location.href = url;
    } catch (e) { win?.close(); toast(e.message, true); }
  });
  $("#d-connect")?.addEventListener("click", async () => {
    try {
      await api("/drive/connect", { method: "POST", json: { url: $("#d-url").value } });
      toast("Mit Google Drive verbunden");
      route();
    } catch (e) { toast(e.message, true); }
  });
  $("#d-run")?.addEventListener("click", async () => { await runDriveImport(); });
  $("#d-disconnect")?.addEventListener("click", async () => {
    if (!confirm("Verbindung zu Google Drive trennen?")) return;
    await api("/drive/disconnect", { method: "POST" });
    route();
  });
}

// ------------------------------------------------------------------ Upload

function upload(formData, label) {
  // XHR statt fetch, damit der Upload-Fortschritt sichtbar ist
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "api/documents");
    xhr.upload.onprogress = e => {
      if (!e.lengthComputable) return;
      const p = Math.round((e.loaded / e.total) * 100);
      busy(p < 100 ? `${label} wird hochgeladen… ${p} %` : "Text wird erkannt und verschlagwortet…", p);
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* leer */ }
      xhr.status < 300 ? resolve(data) : reject(new Error(data.error || `Fehler ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Verbindung fehlgeschlagen"));
    busy(`${label} wird hochgeladen…`, 0);
    xhr.send(formData);
  });
}

async function handleResults(results) {
  const ok = results.filter(r => r.document).map(r => r.document);
  const failed = results.filter(r => r.error);
  failed.forEach(r => toast(`${r.filename || "Datei"}: ${r.error}`, true));
  folderCache = null;
  if (ok.length === 1) location.hash = "#doc/" + ok[0].id;
  else if (ok.length) { toast(`${ok.length} Dokumente im Eingang`); location.hash = "#eingang"; route(); }
  else if (failed.length === 1 && failed[0].document_id) location.hash = "#doc/" + failed[0].document_id;
}

async function uploadFiles(files) {
  if (!files.length) return;
  const fd = new FormData();
  [...files].forEach(f => fd.append("files", f, f.name));
  try {
    await handleResults(await upload(fd, files.length === 1 ? files[0].name : `${files.length} Dateien`));
  } catch (e) { toast(e.message, true); }
  finally { busy(false); }
}

$("#btn-file").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", e => { uploadFiles(e.target.files); e.target.value = ""; });

// Drag & Drop auf dem Computer
let dragDepth = 0;
window.addEventListener("dragenter", e => { if (e.dataTransfer?.types.includes("Files")) { dragDepth++; $("#drop-hint").hidden = false; } });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop-hint").hidden = true; } });
window.addEventListener("dragover", e => e.preventDefault());
window.addEventListener("drop", e => {
  e.preventDefault();
  dragDepth = 0;
  $("#drop-hint").hidden = true;
  if (e.dataTransfer?.files.length) uploadFiles(e.dataTransfer.files);
});

$("#btn-drive").addEventListener("click", runDriveImport);

// ------------------------------------------------------------------ Start

route();
