"use strict";
/* Drawing2CAD Ergebnisanzeige: Teileliste, Zeichnung, 3D-Ansicht, Prüftabellen und das laufende Laufprotokoll.
   Einfaches JavaScript ohne Build-Schritt; einzige Bibliothek ist das mitgelieferte model-viewer. */

// ---------------------------------------------------------------------------
// Konstanten
// ---------------------------------------------------------------------------
// CadQuery ist Z-up, glTF ist Y-up: diese Kamerapositionen wurden am Demoteil kalibriert.
const VIEWS = { front: "0deg 90deg auto", top: "0deg 0deg auto", side: "90deg 90deg auto", iso: "45deg 60deg auto" };
const VIEW_BUTTONS = [["front", "Vorne"], ["top", "Oben"], ["side", "Seite"], ["iso", "Iso"]];
const GLOW = [0.5, 0.12, 0]; // Emissionsfaktor der orangefarbenen Flächen, während eine Prüfzeile überfahren wird
const DIMMED = 0.35; // Farbanteil, den die anderen Materialien solange behalten
// Ränder des Blattrahmens als Anteile der dargestellten Seite, an der Demo-Zeichnung kalibriert.
const FRAME = { l: 0.048, t: 0.034, r: 0.047, b: 0.034 };
const MAX_UPLOAD = 20 * 1024 * 1024;
const STAGES = [["read", "LESEN", "A"], ["describe", "BESCHREIBEN", "B"], ["build", "KONSTRUIEREN", "C"], ["check", "PRÜFEN", "D"], ["export", "EXPORTIEREN", "E"]];
const STAGE_OF = {
  render_drawing: "read", read_drawing_text: "read", measure_drawing: "read",
  submit_spec: "describe", check_spec: "describe",
  extrude: "build", revolve: "build", add_holes: "build", edge_finish: "build", run_script: "build",
  edit_op: "build", delete_op: "build", move_op: "build",
  export_step: "export",
};
const NEUTRAL_TOOLS = new Set(["get_state"]); // wird mitten im Aufbau gerufen, nur im Protokoll sichtbar
const PHASE_STAGE = { read: "read", build: "build" };
const TABS = [["review", "Prüfliste"], ["validation", "Prüfungen"], ["traceability", "Rückverfolgung"], ["benchmark", "Referenz"], ["files", "Dateien"]];
const FILE_ORDER = ["STEP", "MID STEP", "GLB", "MD", "JSON"];
// Anzeige der Prüfcodes aus review.json; der Serverwert bleibt, unbekannte Codes erscheinen unverändert.
const ISSUE_LABEL = {
  missing: "fehlt", ambiguous: "mehrdeutig", low_confidence: "unsicher", conflict: "Widerspruch", convention: "Konvention",
};

// ---------------------------------------------------------------------------
// Hilfsfunktionen
// ---------------------------------------------------------------------------
const $ = (selector, root = document) => root.querySelector(selector);
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const enc = encodeURIComponent;

/** Erzeugt ein Element. Text kommt nur als Textknoten hinein, nie als Markup. */
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// Zeichnungssymbole als inline SVG: der lateinische Schnitt der Schrift hat kein Dreieck, Häkchen oder Pfeile.
const ICONS = {
  triangle: '<path d="M8 2.2 14.6 13.8H1.4Z" fill="none" stroke="currentColor" stroke-width="1.6"/>',
  check: '<path d="M2.5 8.6 6.3 12.4 13.6 3.8" fill="none" stroke="currentColor" stroke-width="2"/>',
  cross: '<path d="M3 3 13 13M13 3 3 13" fill="none" stroke="currentColor" stroke-width="2"/>',
  plus: '<path d="M8 2.5v11M2.5 8h11" fill="none" stroke="currentColor" stroke-width="1.8"/>',
  minus: '<path d="M2.5 8h11" fill="none" stroke="currentColor" stroke-width="1.8"/>',
  reset: '<path d="M2.5 2.5h4M2.5 2.5v4M13.5 13.5h-4M13.5 13.5v-4M13.5 2.5h-4M13.5 2.5v4M2.5 13.5h4M2.5 13.5v-4" fill="none" stroke="currentColor" stroke-width="1.6"/>',
  back: '<path d="M14 8H3M7 4 3 8l4 4" fill="none" stroke="currentColor" stroke-width="1.6"/>',
};
const template = document.createElement("template");
function icon(name, size = 14) {
  template.innerHTML = `<svg class="icon" viewBox="0 0 16 16" width="${size}" height="${size}" aria-hidden="true" focusable="false">${ICONS[name]}</svg>`;
  return template.content.firstElementChild;
}

/** Ersetzt den Inhalt eines Elements; leere Werte (null aus einer Bedingung) werden übersprungen, nicht als "null" ausgegeben. */
const fill = (el, ...nodes) => el.replaceChildren(...nodes.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false));

const sr = (text) => h("span", { class: "sr-only" }, text);
const num = (value, digits = 4) =>
  typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: digits, useGrouping: false }).format(value)
    : "-";
const basename = (path) => String(path ?? "").split("/").pop();
const announce = (text) => { $("#announce").textContent = text; };

function clock(ms, tenths = true) {
  const total = Math.max(0, ms) / 1000;
  const minutes = Math.floor(total / 60);
  const seconds = total - minutes * 60;
  const tail = tenths ? seconds.toFixed(1).padStart(4, "0") : String(Math.floor(seconds)).padStart(2, "0");
  return `${String(minutes).padStart(2, "0")}:${tail}`;
}

async function errorMessage(response) {
  try {
    const body = await response.json();
    if (body && body.error) return String(body.error);
  } catch { /* kein JSON */ }
  return `${response.status} ${response.statusText}`.trim();
}
const friendly = (e) => (e instanceof TypeError ? "Der Server ist nicht erreichbar." : e.message);
async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

/** Ein Kasten mit 45-Grad-Schnittschraffur als Rand; das Wort sitzt auf weißem Feld, nie auf der Schraffur. */
function hatchBox(word, text, action) {
  return h("div", { class: "hatch" },
    h("div", { class: "hatch-inner" },
      word ? h("p", { class: "hatch-word" }, word) : null,
      text ? h("p", { class: "hatch-text" }, text) : null,
      action));
}
const plainBox = (text) => h("p", { class: "empty" }, text);

// ---------------------------------------------------------------------------
// Zustand
// ---------------------------------------------------------------------------
const state = {
  parts: [], loaded: false, loadError: null,
  name: null, detail: null, ops: null, marks: null,
  tab: "review", run: null,
};
let openToken = 0;
let panelToken = 0;

// ---------------------------------------------------------------------------
// Teileliste
// ---------------------------------------------------------------------------
function partRow(part, index, focusName) {
  const exported = part.all_ok === true;
  const count = part.review_count ?? 0;
  const tags = [
    (part.issues ?? []).includes("conflict") ? h("span", { class: "tag", title: "Die Prüfliste enthält einen Widerspruch" }, "MASSSTAB") : null,
    exported ? null : h("span", { class: "tag tag-outline" }, "NICHT EXPORTIERT"),
  ].filter(Boolean);
  return h("li", {
    class: "part", role: "option", "data-name": part.name, title: part.name,
    "aria-selected": String(part.name === state.name), tabindex: part.name === focusName ? "0" : "-1",
  },
    h("span", { class: "pos mono" }, String(index + 1).padStart(2, "0")),
    h("span", { class: "name" }, part.title || part.name),
    count ? h("span", { class: "assumed", title: `${count} angenommen` }, icon("triangle", 12), String(count), sr(" angenommen")) : null,
    exported ? h("span", { class: "ok" }, icon("check", 14), sr("Exportiert und geprüft")) : null,
    tags.length ? h("span", { class: "tags" }, tags) : null);
}

function renderParts() {
  const list = $("#parts");
  const box = $("#parts-state");
  const hadFocus = list.contains(document.activeElement);
  box.hidden = true;
  $("#parts-count").textContent = state.loaded && !state.loadError ? String(state.parts.length) : "";
  if (state.loadError) {
    list.replaceChildren();
    box.hidden = false;
    box.replaceChildren(hatchBox("", "Die Teile konnten nicht geladen werden.", h("button", { type: "button", class: "btn btn-secondary", onclick: () => reload() }, "Erneut versuchen")));
    return;
  }
  if (!state.loaded) {
    list.replaceChildren(...Array.from({ length: 6 }, () => h("li", { class: "part skeleton", "aria-hidden": "true" })));
    return;
  }
  const focusName = state.parts.some((p) => p.name === state.name) ? state.name : state.parts[0]?.name;
  list.replaceChildren(...state.parts.map((part, i) => partRow(part, i, focusName)));
  if (!state.parts.length) {
    box.hidden = false;
    box.replaceChildren(plainBox("Noch keine Teile."));
  }
  if (hadFocus) $("li[tabindex='0']", list)?.focus();
}

function markActiveRow() {
  const rows = [...$("#parts").children];
  for (const row of rows) {
    const active = row.dataset.name === state.name;
    row.setAttribute("aria-selected", String(active));
    if (active) {
      rows.forEach((r) => r.setAttribute("tabindex", r === row ? "0" : "-1"));
      row.scrollIntoView({ block: "nearest" });
    }
  }
}

function initPartsList() {
  const list = $("#parts");
  list.addEventListener("click", (e) => {
    const row = e.target.closest("li.part[data-name]");
    if (row) go(row.dataset.name);
  });
  list.addEventListener("keydown", (e) => {
    const rows = [...list.querySelectorAll("li.part[data-name]")];
    const at = rows.indexOf(document.activeElement);
    if (at < 0) return;
    let next = at;
    if (e.key === "ArrowDown") next = Math.min(at + 1, rows.length - 1);
    else if (e.key === "ArrowUp") next = Math.max(at - 1, 0);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = rows.length - 1;
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(rows[at].dataset.name); return; }
    else return;
    e.preventDefault();
    rows.forEach((r, i) => r.setAttribute("tabindex", i === next ? "0" : "-1"));
    rows[next].focus();
  });
}

// ---------------------------------------------------------------------------
// Schriftfeld
// ---------------------------------------------------------------------------
const printedScale = (entry, scale) =>
  [...`${entry.assumption} ${entry.reason}`.matchAll(/\d+:\d+/g)].map((m) => m[0]).find((s) => s !== scale);

function scaleConflict(detail) {
  const rows = assumptionsOf(detail);
  const at = rows.findIndex((a) => a.issue === "conflict" && /scale/i.test(`${a.assumption} ${a.reason}`));
  return at < 0 ? null : { entry: rows[at], number: at + 1 };
}

function restartDraw() {
  const block = $("#titleblock");
  block.classList.remove("draw");
  void block.offsetWidth; // die Linien-Animation neu starten
  block.classList.add("draw");
}

function fitTitle() {
  const title = $("#tb-title");
  const outline = title.firstElementChild;
  if (!outline) return;
  let size = 48;
  title.style.fontSize = "";
  while (size > 32 && outline.scrollWidth > outline.clientWidth) title.style.fontSize = `${(size -= 2)}px`; // unter 32 px wirkt die Kontur ausgefranst
}

function setTitle(text, ok) {
  const title = $("#tb-title");
  title.classList.toggle("is-ok", ok);
  title.title = text;
  title.replaceChildren(h("span", { class: "t-outline" }, text), h("span", { class: "t-fill", "aria-hidden": "true" }, text));
  fitTitle();
}

function renderTitleBlock(detail) {
  const spec = detail.spec ?? {};
  const exported = detail.all_ok === true;
  const title = spec.title || detail.title || detail.name;
  setTitle(title, exported);
  document.title = `${title} - Drawing2CAD`;
  $("#tb-drawing").textContent = spec.drawing ? basename(spec.drawing) : "-";
  $("#tb-drawing").title = spec.drawing ?? "";
  $("#tb-page").textContent = spec.drawing ? `Blatt ${spec.page ?? 1}` : "";
  const scale = $("#tb-scale");
  scale.replaceChildren(spec.scale ?? "-");
  const conflict = scaleConflict(detail);
  if (conflict) {
    const other = printedScale(conflict.entry, spec.scale);
    if (other) scale.prepend(h("s", { class: "struck", title: "Maßstab aus der Zeichnung" }, other), " ");
    scale.append(" ", h("a", {
      href: `#${enc(detail.name)}/review`, class: "delta-link", title: `Prüfpunkt ${conflict.number}: ${conflict.entry.assumption}`,
      onclick: (e) => { e.preventDefault(); setTab("review"); flashRows([conflict.entry.id]); },
    }, icon("triangle", 14), String(conflict.number), sr(` Prüfpunkt ${conflict.number}, Maßstabswiderspruch`)));
  }
  $("#tb-tolerance").textContent = spec.general_tolerance || "nicht angegeben";
  const status = $("#tb-status");
  if (!exported) {
    status.replaceChildren(h("span", { class: "tag tag-outline tag-big" }, "NICHT EXPORTIERT"));
  } else {
    const rows = detail.review?.validation ?? [];
    const passed = rows.filter((r) => r.ok).length;
    const assumed = detail.review?.assumptions?.length ?? 0;
    status.replaceChildren(
      h("span", { class: "assumed-line" }, `${assumed} ANGENOMMEN`),
      h("span", { class: "ok-line" }, `${passed}/${rows.length} OK`, icon("check", 16)));
  }
  restartDraw();
}

function renderRunTitleBlock(run) {
  setTitle(run.part, false);
  document.title = `${run.part} - Drawing2CAD`;
  $("#tb-drawing").textContent = run.fileName;
  $("#tb-drawing").title = run.fileName;
  $("#tb-page").textContent = "";
  $("#tb-scale").textContent = "-";
  $("#tb-tolerance").textContent = "-";
  $("#tb-status").replaceChildren(h("span", { class: "tag tag-outline tag-big" }, run.done ? (run.result?.ok ? "FERTIG" : "GESTOPPT") : "LÄUFT"));
  restartDraw();
}

function renderEmptyTitleBlock() {
  setTitle("Kein Teil", false);
  $("#tb-drawing").textContent = "-";
  $("#tb-page").textContent = "";
  $("#tb-scale").textContent = "-";
  $("#tb-tolerance").textContent = "-";
  $("#tb-status").replaceChildren();
  document.title = "Drawing2CAD";
  restartDraw();
}

// ---------------------------------------------------------------------------
// Zeichnung mit Rasterlineal, Zoom und Zellen-Hervorhebung
// ---------------------------------------------------------------------------
const drawing = { k: 1, x: 0, y: 0, cols: 8, iw: 1414, ih: 1000 };

function applyZoom() {
  const inner = $(".paper-inner");
  if (!inner) return;
  const paper = inner.parentElement;
  const { width, height } = paper.getBoundingClientRect();
  drawing.x = Math.min(0, Math.max(width * (1 - drawing.k), drawing.x));
  drawing.y = Math.min(0, Math.max(height * (1 - drawing.k), drawing.y));
  inner.style.transform = `translate(${drawing.x}px, ${drawing.y}px) scale(${drawing.k})`;
  paper.classList.toggle("is-zoomed", drawing.k > 1);
}

function zoomAt(factor, px, py) {
  const paper = $(".paper");
  if (!paper) return;
  const rect = paper.getBoundingClientRect();
  const x = px ?? rect.width / 2, y = py ?? rect.height / 2;
  const k = Math.min(8, Math.max(1, drawing.k * factor));
  drawing.x = x - (x - drawing.x) * (k / drawing.k);
  drawing.y = y - (y - drawing.y) * (k / drawing.k);
  drawing.k = k;
  applyZoom();
}

function fitPaper() {
  const stage = $(".sheet-area"), frame = $(".frame");
  if (!stage || !frame) return;
  const ruler = 32; // zwei Leisten von 16 px rund um das Blatt
  const aw = stage.clientWidth - ruler, ah = stage.clientHeight - ruler;
  const scale = Math.max(0, Math.min(aw / drawing.iw, ah / drawing.ih));
  frame.style.setProperty("--pw", `${Math.floor(drawing.iw * scale)}px`);
  frame.style.setProperty("--ph", `${Math.floor(drawing.ih * scale)}px`);
  applyZoom();
}

function buildRulers(frame) {
  const letters = "ABCDEF".split("");
  const numbers = Array.from({ length: drawing.cols }, (_, i) => String(i + 1));
  const make = (cls, labels) => h("div", { class: `ruler ${cls}`, "aria-hidden": "true" }, labels.map((l) => h("span", {}, l)));
  frame.style.setProperty("--cols", String(drawing.cols));
  for (const old of frame.querySelectorAll(".ruler")) old.remove();
  frame.prepend(make("ruler-top", numbers), make("ruler-bottom", numbers), make("ruler-left", letters), make("ruler-right", letters));
}

function cellRect(cell) {
  const row = "ABCDEF".indexOf(String(cell)[0]);
  const col = Number(String(cell).slice(1)) - 1;
  if (row < 0 || !(col >= 0 && col < drawing.cols)) return null;
  const w = 1 - FRAME.l - FRAME.r, hgt = 1 - FRAME.t - FRAME.b;
  return { left: FRAME.l + (col * w) / drawing.cols, top: FRAME.t + (row * hgt) / 6, width: w / drawing.cols, height: hgt / 6 };
}

function highlightCells(cells) {
  const inner = $(".paper-inner");
  if (!inner) return;
  for (const box of inner.querySelectorAll(".cellbox")) box.remove();
  for (const cell of cells) {
    const r = cellRect(cell);
    if (!r) continue;
    inner.append(h("div", {
      class: "cellbox", "aria-hidden": "true",
      style: `left:${r.left * 100}%;top:${r.top * 100}%;width:${r.width * 100}%;height:${r.height * 100}%`,
    }));
  }
}

function renderDrawing(detail) {
  const area = $("#drawing");
  area.replaceChildren();
  Object.assign(drawing, { k: 1, x: 0, y: 0, cols: 8, iw: 1414, ih: 1000 });
  const spec = detail.spec;
  if (!spec || !spec.drawing) {
    area.append(hatchBox("", "Für dieses Teil gibt es noch keine Spezifikation und daher keine Zeichnung."));
    return;
  }
  const title = spec.title || detail.name, page = spec.page ?? 1;
  const img = h("img", { class: "paper-img", alt: `Zeichnung von ${title}, Blatt ${page}`, draggable: "false" });
  const paper = h("div", {
    class: "paper is-loading", tabindex: "0", role: "group",
    "aria-label": "Zeichnung. Die Tasten Plus, Minus und Null zoomen.",
  }, h("div", { class: "paper-inner" }, img));
  const zoomButton = (name, label, handler) => h("button", { type: "button", class: "icon-btn", "aria-label": label, title: label, onclick: handler }, icon(name, 16));
  const frame = h("div", { class: "frame" }, paper);
  area.append(h("div", { class: "sheet-area" }, frame),
    h("div", { class: "zoom", role: "group", "aria-label": "Zoom" },
      zoomButton("plus", "Vergrößern", () => zoomAt(1.4)),
      zoomButton("minus", "Verkleinern", () => zoomAt(1 / 1.4)),
      zoomButton("reset", "Zoom zurücksetzen", () => { drawing.k = 1; drawing.x = drawing.y = 0; applyZoom(); })));
  buildRulers(frame);
  fitPaper();

  img.addEventListener("load", () => {
    drawing.iw = img.naturalWidth || drawing.iw;
    drawing.ih = img.naturalHeight || drawing.ih;
    drawing.cols = Math.max(drawing.iw, drawing.ih) <= 1400 ? 4 : 8; // A4 hat 4 Spalten, A3 und größer 8
    paper.classList.remove("is-loading");
    buildRulers(frame);
    fitPaper();
  });
  img.addEventListener("error", async () => {
    let message = "Die Zeichnung konnte nicht dargestellt werden.";
    try { message = await errorMessage(await fetch(img.src)); } catch { /* Hinweistext bleibt stehen */ }
    area.replaceChildren(hatchBox("", message));
  });
  img.src = `/api/parts/${enc(detail.name)}/drawing.png`;

  paper.addEventListener("keydown", (e) => {
    if (e.key === "+" || e.key === "=") zoomAt(1.4);
    else if (e.key === "-" || e.key === "_") zoomAt(1 / 1.4);
    else if (e.key === "0") { drawing.k = 1; drawing.x = drawing.y = 0; applyZoom(); }
    else return;
    e.preventDefault();
  });
  paper.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = paper.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });
  let drag = null;
  paper.addEventListener("pointerdown", (e) => {
    if (drawing.k <= 1 || e.button !== 0) return;
    drag = { x: e.clientX - drawing.x, y: e.clientY - drawing.y };
    paper.setPointerCapture(e.pointerId);
    paper.classList.add("is-panning");
  });
  paper.addEventListener("pointermove", (e) => {
    if (!drag) return;
    drawing.x = e.clientX - drag.x;
    drawing.y = e.clientY - drag.y;
    applyZoom();
  });
  const endDrag = () => { drag = null; paper.classList.remove("is-panning"); };
  paper.addEventListener("pointerup", endDrag);
  paper.addEventListener("pointercancel", endDrag);
}

// ---------------------------------------------------------------------------
// 3D-Ansicht: model-viewer, die Verbindung der orangen Flächen zu den Prüfzeilen
// ---------------------------------------------------------------------------
const viewer = $("#viewer");
const view = { materials: [], base: new Map(), glowing: false, down: null };

const toSrgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055) * 255;
// Der Export schreibt Orange als [1, 0.214, 0], genau 127,5 im Grünkanal: Vergleich mit Toleranz.
const sameColor = (factor, hex) =>
  [1, 3, 5].every((i, k) => Math.abs(toSrgb(factor[k]) - parseInt(hex.slice(i, i + 2), 16)) <= 2);
const isOrange = (material) =>
  Object.keys(state.marks ?? {}).some((hex) => sameColor(material.pbrMetallicRoughness.baseColorFactor, hex));
const marksActive = () => Boolean(state.marks) && view.materials.some(isOrange);

function pressedView(key) {
  for (const button of $("#view-buttons").children) button.setAttribute("aria-pressed", String(button.dataset.view === key));
}

function setView(key) {
  if (!VIEWS[key]) return;
  viewer.cameraOrbit = VIEWS[key];
  if (reducedMotion()) viewer.jumpCameraToGoal();
  pressedView(key);
}

function removeHotspot() {
  for (const hotspot of viewer.querySelectorAll("[slot='hotspot-mark']")) hotspot.remove();
}

function setGlow(on) {
  if (on === view.glowing || !marksActive()) return;
  view.glowing = on;
  for (const material of view.materials) {
    const base = view.base.get(material);
    if (isOrange(material)) {
      if (!reducedMotion()) material.setEmissiveFactor(on ? GLOW : [0, 0, 0]);
    } else {
      material.pbrMetallicRoughness.setBaseColorFactor(on ? [base[0] * DIMMED, base[1] * DIMMED, base[2] * DIMMED, base[3]] : base);
    }
  }
}

function markedIds() {
  return [...new Set(Object.values(state.marks ?? {}).flat())];
}

function onViewerClick(event) {
  if (!marksActive()) return;
  const x = event.clientX, y = event.clientY; // dieses model-viewer liest Viewport-Koordinaten und zieht sein eigenes Feld ab
  const material = viewer.materialFromPoint(x, y);
  if (!material || !isOrange(material)) return; // Hintergrund und andere Flächen tun nichts
  const ids = markedIds();
  setTab("review");
  flashRows(ids);
  const hit = viewer.positionAndNormalFromPoint(x, y);
  removeHotspot(); // es gibt immer nur einen
  if (hit) {
    viewer.append(h("button", {
      type: "button", slot: "hotspot-mark", class: "hotspot", tabindex: "-1",
      "data-position": hit.position.toString(), "data-normal": hit.normal.toString(),
    }, ids.join(", ")));
  }
}

function focusSources(sources) {
  /* sources: [{view, cell}]; die Kamera dreht zur ersten Ansicht, jede Zelle bekommt einen Rahmen auf der Zeichnung. */
  const views = new Map((state.detail?.spec?.views ?? []).map((v) => [v.id, v.kind]));
  const first = sources.find((s) => VIEWS[views.get(s.view)]);
  if (first && !viewer.hidden) {
    viewer.cameraOrbit = VIEWS[views.get(first.view)];
    if (reducedMotion()) viewer.jumpCameraToGoal();
    pressedView(views.get(first.view));
  }
  highlightCells(sources.map((s) => s.cell).filter(Boolean));
}

function parseSources(list) {
  return (list ?? []).map((s) => {
    const [viewId, cell] = String(s).split("/");
    return { view: viewId, cell: cell && cell !== "-" ? cell : null };
  });
}

function linkRow(row, sources) {
  const on = () => { setGlow(true); focusSources(sources); };
  const off = () => { setGlow(false); highlightCells([]); };
  row.addEventListener("mouseenter", on);
  row.addEventListener("mouseleave", off);
  row.addEventListener("focus", on);
  row.addEventListener("blur", off);
}

function initViewer() {
  const hint = $("#view-hint");
  const buttons = $("#view-buttons");
  buttons.replaceChildren(...VIEW_BUTTONS.map(([key, label]) =>
    h("button", { type: "button", class: "ghost", "data-view": key, "aria-pressed": "false", onclick: () => setView(key) }, label)));
  viewer.addEventListener("load", () => {
    hint.hidden = true;
    viewer.classList.remove("is-pending");
    view.materials = viewer.model?.materials ?? [];
    view.base = new Map(view.materials.map((m) => [m, [...m.pbrMetallicRoughness.baseColorFactor]]));
    view.glowing = false;
    viewer.cameraOrbit = VIEWS.front; // das Modell erhebt sich aus der Frontansicht in die Isometrie
    viewer.jumpCameraToGoal();
    requestAnimationFrame(() => setView("iso"));
  });
  viewer.addEventListener("error", () => reset3D("Das 3D-Modell konnte nicht geladen werden."));
  viewer.addEventListener("camera-change", (e) => { if (e.detail?.source === "user-interaction") pressedView(null); });
  viewer.addEventListener("pointerdown", (e) => { view.down = { x: e.clientX, y: e.clientY }; });
  viewer.addEventListener("pointerup", (e) => {
    const down = view.down;
    view.down = null;
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5) onViewerClick(e); // ein Klick, kein Schwenken
  });
}

/** Das 3D-Fenster ohne Modell: der Punkteboden und ein Satz (die Meldung kann den Grund nennen). */
function reset3D(message = "Das 3D-Modell erscheint nach dem Export, mit Farben.") {
  removeHotspot();
  view.materials = [];
  view.glowing = false;
  state.marks = null;
  viewer.hidden = true;
  viewer.classList.remove("is-pending");
  viewer.removeAttribute("src");
  $("#view-buttons").hidden = true;
  $("#step-link").hidden = true;
  const hint = $("#view-hint");
  hint.hidden = false;
  hint.textContent = message;
}

/** Die nominale STEP-Datei eines exportierten Teils ist einen Klick entfernt, egal welcher Tab offen ist. */
function showStepLink(detail) {
  const step = (detail.files ?? []).find((f) => /\.(step|stp)$/i.test(f) && !/_mid\.(step|stp)$/i.test(f));
  const link = $("#step-link");
  link.hidden = !step;
  if (!step) return;
  link.href = `/files/${enc(detail.name)}/${enc(step)}`;
  link.setAttribute("download", step);
}

async function setup3D(detail, token) {
  const glb = `${detail.name}.glb`;
  const hint = $("#view-hint");
  if (!(detail.files ?? []).includes(glb)) { reset3D(); return; }
  removeHotspot();
  view.materials = [];
  view.glowing = false;
  state.marks = null;
  viewer.hidden = false;
  $("#view-buttons").hidden = false;
  const assumed = detail.review?.assumptions?.length ?? 0;
  viewer.alt = `3D-Modell von ${detail.spec?.title || detail.name}, ${assumed} ${assumed === 1 ? "Annahme" : "Annahmen"} orange markiert`;
  viewer.classList.add("is-pending"); // das bisherige Modell darf unter der Lademitteilung nicht sichtbar sein
  hint.hidden = false;
  hint.textContent = "3D-Modell wird geladen …";
  const url = `/files/${enc(detail.name)}/${enc(glb)}`;
  if (viewer.getAttribute("src") === url) { // dieselbe Datei erneut feuert kein load: erst leeren
    viewer.removeAttribute("src");
    await new Promise(requestAnimationFrame);
    if (token !== openToken) return;
  }
  viewer.src = url;
  if ((detail.files ?? []).includes("marks.json")) {
    try {
      const marks = await getJson(`/files/${enc(detail.name)}/marks.json`);
      if (token === openToken && marks && Object.keys(marks).length) state.marks = marks;
    } catch { /* ohne Markierungen zeigt die 3D-Ansicht nur das Modell */ }
  }
}

// ---------------------------------------------------------------------------
// Tabs und Bereiche
// ---------------------------------------------------------------------------
function assumptionsOf(detail) {
  if (detail?.review?.assumptions) return detail.review.assumptions;
  // Nicht exportiert: noch keine review.json, aber die Spezifikation enthält die Annahmen bereits.
  const dims = new Map((detail?.spec?.dimensions ?? []).map((d) => [d.id, d]));
  return (detail?.spec?.review ?? []).map((r) => ({
    id: r.id, issue: r.issue, assumption: r.assumption, reason: r.reason, marked_faces: null,
    refs: r.refs.map((id) => ({ id, text: dims.get(id)?.text ?? "" })),
    sources: r.refs.map((id) => dims.get(id)?.source).filter(Boolean).map((s) => `${s.view}/${s.cell ?? "-"}`),
  }));
}

function tabList() {
  const detail = state.detail;
  const tabs = [];
  if (state.run) tabs.push(["run", "Lauf", ""]);
  const rows = detail?.review?.validation ?? [];
  const counts = {
    review: detail ? String(assumptionsOf(detail).length) : "",
    validation: rows.length ? `${rows.filter((r) => r.ok).length}/${rows.length}` : "",
  };
  for (const [key, label] of TABS) tabs.push([key, label, counts[key] ?? ""]);
  return tabs;
}

function renderTabs() {
  const bar = $("#tabs");
  bar.replaceChildren(...tabList().map(([key, label, count]) => h("button", {
    type: "button", role: "tab", id: `tab-${key}`, "data-tab": key, "aria-controls": "panel",
    "aria-selected": String(key === state.tab), tabindex: key === state.tab ? "0" : "-1",
  }, label, count ? h("span", { class: "tab-count" }, count) : null)));
}

function setTab(tab) {
  state.tab = tab;
  if (state.name) history.replaceState(null, "", `#${enc(state.name)}/${tab}`);
  renderTabs();
  renderPanel();
}

function initTabs() {
  const bar = $("#tabs");
  bar.addEventListener("click", (e) => {
    const tab = e.target.closest("[role='tab']");
    if (tab) setTab(tab.dataset.tab);
  });
  bar.addEventListener("keydown", (e) => {
    const tabs = [...bar.querySelectorAll("[role='tab']")];
    const at = tabs.indexOf(document.activeElement);
    if (at < 0) return;
    let next = at;
    if (e.key === "ArrowRight") next = (at + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (at - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    else return;
    e.preventDefault();
    setTab(tabs[next].dataset.tab);
    $(`#tab-${tabs[next].dataset.tab}`).focus();
  });
}

function flashRows(ids) {
  const panel = $("#panel");
  const rows = ids.map((id) => panel.querySelector(`tr[data-id="${CSS.escape(id)}"]`)).filter(Boolean);
  rows.forEach((row, i) => {
    if (i === 0) row.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
    row.classList.remove("flash");
    void row.offsetWidth; // die Animation neu starten
    row.classList.add("flash");
    setTimeout(() => row.classList.remove("flash"), 1300);
  });
}

function table(headers, rows, cls = "") {
  return h("table", { class: `grid ${cls}` },
    h("thead", {}, h("tr", {}, headers.map((t) => h("th", { scope: "col" }, t)))),
    h("tbody", {}, rows));
}

function dimensionCell(refs, dims) {
  return refs.map((ref) => {
    if (ref.text) return h("span", { class: "dim" }, ref.text);
    const dim = dims.get(ref.id);
    // Kein gedruckter Text: der Wert war angenommen, gezeichnet als Hilfsbemaßung in Klammern.
    return dim ? h("span", { class: "dim dim-assumed", title: `Angenommener Wert von ${ref.id}` }, `(${num(dim.value)})`) : h("span", { class: "mono" }, ref.id);
  }).flatMap((node, i) => (i ? [" ", node] : [node]));
}

function renderReview(detail) {
  const rows = assumptionsOf(detail);
  const panel = $("#panel");
  const dims = new Map((detail.spec?.dimensions ?? []).map((d) => [d.id, d]));
  const parts = [];
  if (!rows.length) {
    parts.push(plainBox("Keine Annahmen: Jeder Wert stammt aus der Zeichnung."));
  } else {
    const body = rows.map((a, i) => {
      const row = h("tr", { class: "assumed-row", tabindex: "0", "data-id": a.id },
        h("td", { class: "col-delta", "data-label": "Pos." }, h("span", { class: "delta" }, icon("triangle", 12), String(i + 1))),
        h("td", { class: "mono", "data-label": "ID" }, a.id),
        h("td", { "data-label": "Maß" }, a.refs.length ? dimensionCell(a.refs, dims) : "-"),
        h("td", { "data-label": "Befund" }, h("span", { class: "tag" }, ISSUE_LABEL[a.issue] ?? a.issue)),
        h("td", { "data-label": "Annahme" }, a.assumption),
        h("td", { "data-label": "Begründung" }, a.reason),
        h("td", { class: "mono", "data-label": "Quelle" }, (a.sources ?? []).length ? a.sources.join(", ") : "-"),
        h("td", { class: "mono", "data-label": "Flächen" }, a.marked_faces == null ? "-" : String(a.marked_faces)));
      linkRow(row, parseSources(a.sources));
      return row;
    });
    parts.push(table(["Pos.", "ID", "Maß", "Befund", "Annahme", "Begründung", "Quelle", "Flächen"], body, "review"));
  }
  const notes = detail.review?.notes ?? detail.spec?.notes ?? [];
  if (notes.length) {
    parts.push(h("div", { class: "notes" },
      h("h3", {}, "Zeichnungshinweise"),
      h("ul", {}, notes.map((n) => h("li", {}, h("span", { class: "mono" }, n.id), " ", n.text)))));
  }
  fill(panel, ...parts);
}

function renderValidation(detail) {
  const panel = $("#panel");
  const rows = detail.review?.validation;
  if (!rows) {
    fill(panel, plainBox("Noch keine Prüfungen. Der letzte Export bestand nicht, oder das Teil wurde nie exportiert."));
    return;
  }
  const passed = rows.filter((r) => r.ok).length;
  const status = (ok) => ok
    ? h("span", { class: "ok" }, icon("check", 14), " ", sr("bestanden"))
    : h("span", { class: "fail" }, icon("cross", 14), " FEHLGESCHLAGEN");
  fill(panel,
    h("div", { class: "kpi" }, h("span", { class: "kpi-number" }, `${passed}/${rows.length}`), h("span", { class: "label" }, "bestandene Prüfungen")),
    table(["Prüfung", "Soll", "Ist", "Ergebnis"], rows.map((r) => h("tr", {},
      h("td", { class: "mono", "data-label": "Prüfung" }, r.check),
      h("td", { class: "mono", "data-label": "Soll" }, r.expected),
      h("td", { class: "mono", "data-label": "Ist" }, r.actual),
      h("td", { "data-label": "Ergebnis" }, status(r.ok)))), "validation"));
}

async function renderTraceability(detail) {
  const panel = $("#panel");
  const review = detail.review;
  if (!review) {
    fill(panel, plainBox("Noch keine Rückverfolgung. Sie entsteht mit dem Export."));
    return;
  }
  const token = ++panelToken;
  if (!state.ops) {
    try { state.ops = await getJson(`/files/${enc(detail.name)}/ops.json`); } catch { state.ops = []; }
    if (token !== panelToken || state.tab !== "traceability") return;
  }
  const dims = new Map((detail.spec?.dimensions ?? []).map((d) => [d.id, d]));
  const opName = (i) => state.ops[i]?.op ?? "op";
  const line = (t, linked) => {
    const row = h("li", { class: `trace mono${linked ? "" : " unreferenced"}`, tabindex: "0" },
      `Op. ${t.op_index + 1} `, h("span", { class: "opname" }, opName(t.op_index)), ` · ${t.path} = ${num(t.value)}`,
      linked ? [h("span", { class: "back" }, icon("back", 14), sr(" aus")), ` ${t.dimension} `,
        t.text ? h("span", { class: "dim" }, `"${t.text}"`) : h("span", { class: "dim dim-assumed" }, "(angenommen)")] : null);
    if (linked) {
      const source = dims.get(t.dimension)?.source;
      linkRow(row, source ? [{ view: source.view, cell: source.cell }] : []);
    }
    return row;
  };
  const traced = review.traceability ?? [], loose = review.unreferenced ?? [];
  fill(panel,
    traced.length ? h("ul", { class: "trace-list" }, traced.map((t) => line(t, true))) : plainBox("Kein Wert geht auf ein Maß zurück."),
    h("div", { class: "loose" },
      h("h3", {}, "Werte ohne Maßbezug"),
      loose.length ? h("ul", { class: "trace-list" }, loose.map((t) => line(t, false))) : h("p", { class: "muted" }, "Keine.")));
}

function matchWord(row) {
  const raw = row.match ?? row.MATCH;
  if (raw === "MATCH") return h("span", { class: "match match-yes" }, "TREFFER");
  if (raw === "DIFF") return h("span", { class: "match match-diff" }, "ABWEICHUNG");
  return h("span", { class: "match match-invalid" }, "Referenz ungültig");
}

// Anzeige für die Benchmark-Marks; die Werte (MATCH, DIFF, …) bleiben Serverdaten.
const KIND_LABEL = { yes: "Treffer", diff: "Abweichung", invalid: "Referenz ungültig", none: "keine Referenz" };

function benchmarkTally() {
  const rows = state.parts.filter((p) => p.eval).map((p) => p.eval);
  const invalid = (r) => (r.match ?? r.MATCH) === "reference invalid" || r.ref_valid === false;
  const valid = rows.filter((r) => !invalid(r));
  return { matched: valid.filter((r) => (r.match ?? r.MATCH) === "MATCH").length, total: valid.length, invalid: rows.length - valid.length };
}

function ruler() {
  const tally = benchmarkTally();
  if (!tally.total && !tally.invalid) return null;
  const kind = (p) => {
    const raw = p.eval?.match ?? p.eval?.MATCH;
    if (!p.eval) return "none";
    return raw === "MATCH" ? "yes" : raw === "DIFF" ? "diff" : "invalid";
  };
  return h("div", { class: "ruler-bar" },
    h("p", { class: "kpi-line" }, h("span", { class: "kpi-number" }, `${tally.matched}/${tally.total}`), h("span", { class: "label" }, "Treffer"),
      tally.invalid ? h("span", { class: "muted" }, `${tally.invalid} mit ungültiger Referenz, nicht gezählt`) : null),
    h("div", { class: "ticks", role: "group", "aria-label": "Teile auf dem Referenzlineal" },
      state.parts.map((p) => h("button", {
        type: "button", class: `tick tick-${kind(p)}${p.name === state.name ? " is-current" : ""}`, title: `${p.title || p.name}: ${KIND_LABEL[kind(p)]}`,
        "aria-label": `${p.title || p.name}, ${KIND_LABEL[kind(p)]}`, onclick: () => go(p.name, "benchmark"),
      }))));
}

function renderBenchmark(detail) {
  const panel = $("#panel");
  const row = detail.eval;
  const bar = ruler();
  if (!row) {
    fill(panel, bar, plainBox("Keine Referenz für dieses Teil."));
    return;
  }
  const sorted = (list) => (Array.isArray(list) ? list : []);
  const own = sorted(row.bbox_sorted), ref = sorted(row.ref_bbox_sorted);
  const unit = (value, suffix) => (typeof value === "number" ? `${num(value, 3)} ${suffix}` : "-");
  fill(panel, bar,
    h("div", { class: "bench" },
      h("div", { class: "bench-head" }, matchWord(row),
        h("div", { class: "kpi" }, h("span", { class: "kpi-number" }, unit(row.dv_pct, "%")), h("span", { class: "label" }, "Volumenabweichung")),
        h("div", { class: "kpi" }, h("span", { class: "kpi-number" }, unit(row.dbbox_max_mm, "mm")), h("span", { class: "label" }, "größte Abweichung des Hüllquaders"))),
      h("div", { class: "bench-box" },
        h("p", { class: "label" }, "Hüllquader, sortiert (eigen / Referenz)"),
        [0, 1, 2].map((i) => h("p", { class: "mono" }, `${num(own[i], 3)}  /  ${num(ref[i], 3)}`)),
        row.missing_d?.length ? h("p", { class: "mono" }, `fehlende Durchmesser: ${row.missing_d.join(", ")}`) : null,
        row.extra_d?.length ? h("p", { class: "mono" }, `zusätzliche Durchmesser: ${row.extra_d.join(", ")}`) : null)));
}

function fileKind(name) {
  if (/_mid\.(step|stp)$/i.test(name)) return "MID STEP";
  if (/\.(step|stp)$/i.test(name)) return "STEP";
  return (name.split(".").pop() || "FILE").toUpperCase();
}

function renderFiles(detail) {
  const panel = $("#panel");
  const files = [...(detail.files ?? [])].sort((a, b) => {
    const ka = FILE_ORDER.indexOf(fileKind(a)), kb = FILE_ORDER.indexOf(fileKind(b));
    return (ka < 0 ? 99 : ka) - (kb < 0 ? 99 : kb) || a.localeCompare(b);
  });
  const hasStep = files.some((f) => fileKind(f) === "STEP");
  const rows = files.map((f) => h("li", { class: "file" },
    h("span", { class: "kind" }, fileKind(f)),
    h("span", { class: "mono filename" }, f),
    h("a", { class: "btn btn-link", href: `/files/${enc(detail.name)}/${enc(f)}`, download: f }, "Herunterladen", sr(` ${f}`))));
  const blocked = hasStep ? null : hatchBox("GESTOPPT", "Keine STEP-Datei: Der Export läuft nur, wenn jede Prüfung besteht.");
  fill(panel, blocked, files.length ? h("ul", { class: "files" }, rows) : plainBox("Keine Dateien."));
}

function renderPanel() {
  const panel = $("#panel");
  panel.setAttribute("aria-labelledby", `tab-${state.tab}`);
  const detail = state.detail;
  if (state.tab === "run" && state.run) { renderRun(); return; }
  if (!detail) {
    fill(panel, plainBox(state.run ? "Ein Teil auswählen, um die Details zu sehen." : "Ein Teil auswählen."));
    return;
  }
  panelToken++;
  ({ review: renderReview, validation: renderValidation, traceability: renderTraceability, benchmark: renderBenchmark, files: renderFiles }[state.tab] ?? renderReview)(detail);
}

// ---------------------------------------------------------------------------
// Live-Lauf: Phasen als Maßkette, Protokoll als Änderungstabelle
// ---------------------------------------------------------------------------
const toolStage = (run, name) => {
  if (NEUTRAL_TOOLS.has(name)) return null;
  return STAGE_OF[name] ?? run.current ?? "read";
};

function newRun(id, part, fileName) {
  return {
    id, part, fileName, t0: performance.now(), done: false, lost: false, result: null, current: null, reloaded: false,
    stages: Object.fromEntries(STAGES.map(([key]) => [key, { state: "pending", segs: [] }])),
    open: new Map(), log: [], es: null, timer: null, stick: true,
  };
}

function activate(run, key) {
  if (!key) return;
  const order = STAGES.map(([k]) => k);
  run.current = key;
  for (const [k, stage] of Object.entries(run.stages)) {
    // PRÜFEN wird allein von export_step gelenkt: die Prüfungen laufen innerhalb des Exports
    if (k !== "check" && order.indexOf(k) < order.indexOf(key) && stage.state !== "failed") stage.state = "done";
  }
  if (run.stages[key].state === "pending") run.stages[key].state = "active";
}

function brief(value, max = 90) {
  const text = typeof value === "string" ? value : JSON.stringify(value) ?? "";
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > max ? `${line.slice(0, max - 1)}...` : line;
}

function handleEvent(run, event, now = performance.now()) {
  const at = now - run.t0;
  const type = event && typeof event === "object" ? event.type : undefined;
  const name = String(event?.name ?? "");
  if (type === "phase") {
    activate(run, PHASE_STAGE[event.phase]);
    run.log.push({ at, kind: "PHASE", label: String(event.phase ?? ""), summary: "" });
  } else if (type === "tool_call") {
    const key = toolStage(run, name);
    activate(run, key);
    if (name === "export_step") run.stages.check.state = "active";
    const seg = { name, stage: key, start: now, dur: null, ok: null };
    if (key) run.stages[key].segs.push(seg);
    if (!run.open.has(name)) run.open.set(name, []);
    run.open.get(name).push(seg);
    run.log.push({ at, kind: "CALL", label: name, summary: brief(event.args ?? {}), detail: event.args === undefined ? "" : JSON.stringify(event.args, null, 2) });
  } else if (type === "tool_result") {
    const seg = run.open.get(name)?.shift();
    const ok = event.ok !== false;
    if (seg) { seg.dur = now - seg.start; seg.ok = ok; }
    if (name === "export_step") run.stages.check.state = ok ? "done" : "failed";
    const text = event.text === undefined ? "" : String(event.text);
    run.log.push({ at, kind: ok ? "OK" : "FAIL", label: name, summary: brief(text), detail: text, dur: seg?.dur ?? null });
  } else if (type === "message") {
    const text = String(event.text ?? "");
    run.log.push({ at, kind: "MSG", label: "", summary: brief(text, 140), detail: text.length > 140 ? text : "" });
  } else if (type === "done") {
    finishRun(run, event, now);
    return;
  } else {
    run.log.push({ at, kind: "EVENT", label: "", summary: brief(event ?? "", 160) });
  }
  refreshRun(run);
}

function finishRun(run, event, now = performance.now()) {
  if (run.done) return;
  run.done = true;
  run.es?.close(); // der Server beendet den Stream nach done; sonst würde das EventSource neu verbinden und alles wiederholen
  clearInterval(run.timer);
  const ok = event.ok === true;
  run.result = { ok, reason: event.reason ? String(event.reason) : "" };
  for (const seg of [...run.open.values()].flat()) { seg.dur = now - seg.start; seg.ok = false; }
  run.open.clear();
  if (!ok && run.current) run.stages[run.current].state = "failed";
  if (ok) for (const stage of Object.values(run.stages)) if (stage.state === "active") stage.state = "done";
  run.log.push({ at: now - run.t0, kind: "DONE", label: ok ? "exportiert" : "gestoppt", summary: run.result.reason, detail: "" });
  refreshRun(run);
  if (state.run === run && !state.name) renderRunTitleBlock(run);
  afterRun(run);
}

function connectionLost(run) {
  if (run.done) return;
  run.es?.close();
  clearInterval(run.timer);
  run.lost = true;
  const now = performance.now();
  for (const seg of [...run.open.values()].flat()) { seg.dur = now - seg.start; seg.ok = false; }
  run.open.clear();
  run.log.push({ at: now - run.t0, kind: "LOST", label: "", summary: `Verbindung verloren bei ${clock(now - run.t0, false)}` });
  refreshRun(run);
}

async function afterRun(run) {
  if (run.reloaded) return;
  run.reloaded = true; // genau ein Neuladen der Teileliste pro Lauf
  await loadParts();
  // ein gestoppter Lauf behält sein Protokoll im Blick, neben dem Teil, das er hinterlässt
  if (state.parts.some((p) => p.name === run.part)) go(run.part, run.result?.ok ? "review" : "run");
}

function segSeconds(seg, now) {
  return Math.max(0.3, ((seg.dur ?? now - seg.start) || 0) / 1000);
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** Eine Bemaßungslinie; der Pfeilspitzen-Marker steht einmal in index.html. Offene Segmente haben nur einen Startpfeil. */
function dimLine(closed) {
  const svg = document.createElementNS(SVG_NS, "svg");
  const line = document.createElementNS(SVG_NS, "line");
  for (const [key, value] of Object.entries({ class: "dimline", height: "12", "aria-hidden": "true", focusable: "false" })) svg.setAttribute(key, value);
  for (const [key, value] of Object.entries({ x1: "1", x2: "100%", y1: "6", y2: "6", stroke: "currentColor", "stroke-width": "1.5", "marker-start": "url(#dim-arrow)" })) line.setAttribute(key, value);
  if (closed) line.setAttribute("marker-end", "url(#dim-arrow)");
  svg.append(line);
  return svg;
}

function segView(seg, now, still) {
  const kind = seg.dur === null ? "open" : seg.ok === false ? "bad" : "closed";
  const grow = still && seg.dur === null ? 1 : segSeconds(seg, now);
  return h("span", { class: `seg seg-${kind}`, style: `flex-grow:${grow.toFixed(2)}`, title: seg.name },
    h("span", { class: "seg-body" },
      h("em", { class: "seg-name" }, seg.name),
      dimLine(kind !== "open"),
      h("b", { class: "seg-time" }, seg.dur === null ? "" : `${(seg.dur / 1000).toFixed(1)} s`)));
}

function chainView(run, now) {
  const still = reducedMotion();
  const items = STAGES.map(([key, label, letter]) => {
    const stage = run.stages[key];
    const weight = Math.max(1, stage.segs.reduce((sum, s) => sum + segSeconds(s, now), 0));
    return h("li", { class: `stage is-${stage.state}`, style: `flex-grow:${weight.toFixed(2)}` },
      h("span", { class: "stage-head" },
        h("span", { class: "marker" }, icon("triangle", 12), letter),
        h("span", { class: "stage-name" }, label),
        stage.state === "failed" ? sr(" gestoppt") : null),
      h("div", { class: "segs" }, stage.segs.map((seg) => segView(seg, now, still))));
  });
  if (run.done) {
    const ok = run.result.ok;
    items.push(h("li", { class: `result ${ok ? "is-yes" : "is-no"}` },
      ok ? h("p", { class: "result-word" }, "EXPORTIERT")
        : hatchBox("GESTOPPT", run.result.reason || "Der Lauf endete ohne Export.")));
  }
  return h("ol", { class: "chain", "aria-label": "Laufphasen" }, items);
}

function logRow(entry) {
  const detail = entry.detail ? h("details", {}, h("summary", {}, entry.kind === "CALL" ? "Argumente" : "Text"), h("pre", {}, entry.detail)) : null;
  return h("tr", { class: `log-${entry.kind.toLowerCase()}` },
    h("td", { class: "mono t" }, clock(entry.at)),
    h("td", { class: "mono kind" }, entry.kind === "FAIL" ? h("span", { class: "fail" }, icon("cross", 12), " FEHLGESCHLAGEN") : entry.kind),
    h("td", { class: "mono tool" }, entry.label),
    h("td", { class: "mono what" }, entry.dur != null ? `${(entry.dur / 1000).toFixed(1)} s  ` : "", entry.summary, detail));
}

function renderRun() {
  const run = state.run;
  const panel = $("#panel");
  fill(panel,
    h("div", { class: "run" },
      h("div", { id: "chain-host" }, chainView(run, performance.now())),
      h("div", { class: "log", id: "log", role: "log", "aria-live": "polite", "aria-label": "Laufprotokoll" },
        h("table", { class: "grid log-table" }, h("tbody", {}, run.log.map(logRow))))));
  const log = $("#log");
  log.addEventListener("scroll", () => { run.stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 8; });
  log.scrollTop = log.scrollHeight;
}

function refreshRun(run) {
  if (state.run !== run || state.tab !== "run") return;
  const host = $("#chain-host"), log = $("#log");
  if (!host || !log) { renderRun(); return; }
  host.replaceChildren(chainView(run, performance.now()));
  const body = log.querySelector("tbody");
  for (const entry of run.log.slice(body.children.length)) body.append(logRow(entry));
  if (run.stick) log.scrollTop = log.scrollHeight;
}

function startRun(id, part, fileName) {
  const run = newRun(id, part, fileName);
  state.run = run;
  state.name = null;
  state.detail = null;
  state.tab = "run";
  history.replaceState(null, "", location.pathname + location.search);
  markActiveRow();
  renderRunTitleBlock(run);
  renderRunStage(run);
  renderTabs();
  renderPanel();
  run.es = new EventSource(`/api/runs/${enc(id)}/events`);
  run.es.onmessage = (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { event = { raw: message.data }; }
    handleEvent(run, event);
  };
  run.es.onerror = () => connectionLost(run); // der Browser würde neu verbinden und alle Ereignisse wiederholen: hier lieber stoppen
  if (!reducedMotion()) run.timer = setInterval(() => refreshRun(run), 250);
}

function renderRunStage(run) {
  $("#drawing").replaceChildren(hatchBox("", `Die Zeichnung von ${run.part} erscheint hier, sobald der Agent die Spezifikation eingereicht hat.`));
  reset3D();
}

// ---------------------------------------------------------------------------
// Hochladen
// ---------------------------------------------------------------------------
function fieldError(id, message) {
  const el = $(`#${id}-error`);
  el.textContent = message ?? "";
  $(`#${id}`).setAttribute("aria-invalid", message ? "true" : "false");
}
function clearErrors() {
  for (const id of ["file", "part", "model"]) fieldError(id, "");
  const notice = $("#run-notice");
  notice.textContent = "";
  notice.classList.remove("is-shown");
}

function showNotice(text) {
  const notice = $("#run-notice");
  notice.textContent = text;
  notice.classList.add("is-shown");
}

function showServerError(status, message) {
  if (status === 503) { showNotice(`${message} Aufgenommene Teile bleiben aufrufbar.`); return; }
  if (status === 0) { showNotice(message); return; } // gar keine Antwort

  const target = /part name/i.test(message) ? "part" : /\bmodel\b/i.test(message) ? "model" : "file";
  fieldError(target, message);
  $(`#${target}`).focus();
}

function initUpload() {
  const form = $("#upload"), file = $("#file"), part = $("#part");
  file.addEventListener("change", () => {
    fieldError("file", "");
    const chosen = file.files[0];
    if (chosen && !part.value) part.value = chosen.name.replace(/\.[^.]+$/, "").replace(/[^\w-]+/g, "_").replace(/^_+|_+$/g, "");
  });
  for (const type of ["dragenter", "dragover"]) form.addEventListener(type, (e) => { e.preventDefault(); form.classList.add("is-over"); });
  for (const type of ["dragleave", "drop"]) form.addEventListener(type, (e) => { e.preventDefault(); form.classList.remove("is-over"); });
  form.addEventListener("drop", (e) => {
    if (!e.dataTransfer?.files.length) return;
    file.files = e.dataTransfer.files;
    file.dispatchEvent(new Event("change"));
  });
  $("#new-run").addEventListener("click", () => { file.focus(); file.scrollIntoView({ block: "nearest" }); });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearErrors();
    const chosen = file.files[0];
    const name = part.value.trim();
    if (!chosen) { fieldError("file", "Bitte eine Zeichnungsdatei wählen (PDF, PNG oder JPG)."); file.focus(); return; }
    if (chosen.size > MAX_UPLOAD) { fieldError("file", "Die Datei ist größer als 20 MB."); file.focus(); return; }
    if (!/^[\w-]+$/.test(name)) { fieldError("part", "Nur Buchstaben, Ziffern, '_' und '-' verwenden."); part.focus(); return; }
    if (state.run && !state.run.done && !state.run.lost) { showNotice("Ein Lauf läuft noch."); return; }
    const body = new FormData();
    body.append("pdf", chosen);
    body.append("part", name);
    if ($("#model").value.trim()) body.append("model", $("#model").value.trim());
    const button = $("#start");
    button.disabled = true;
    try {
      const response = await fetch("/api/runs", { method: "POST", body });
      if (!response.ok) { showServerError(response.status, await errorMessage(response)); return; }
      const { run_id: id } = await response.json();
      file.value = "";
      part.value = "";
      startRun(id, name, chosen.name);
    } catch (error) {
      showServerError(0, friendly(error));
    } finally {
      button.disabled = false;
    }
  });
}

// ---------------------------------------------------------------------------
// Teil öffnen, Routing, Start
// ---------------------------------------------------------------------------
function go(name, tab) {
  const hash = `#${enc(name)}${tab ? `/${tab}` : ""}`;
  if (location.hash === hash) route(); else location.hash = hash;
}

async function openPart(name, tab) {
  const token = ++openToken;
  state.name = name;
  state.tab = tab && (tab === "run" ? state.run : TABS.some(([k]) => k === tab)) ? tab : "review";
  markActiveRow();
  $("#main").classList.add("is-loading");
  let detail;
  try {
    detail = await getJson(`/api/parts/${enc(name)}`);
  } catch (error) {
    if (token !== openToken) return;
    $("#main").classList.remove("is-loading");
    state.detail = null;
    renderEmptyTitleBlock();
    reset3D();
    $("#drawing").replaceChildren(hatchBox("", `${name} konnte nicht geladen werden: ${friendly(error)}`, h("button", { type: "button", class: "btn btn-secondary", onclick: () => openPart(name, tab) }, "Erneut versuchen")));
    renderTabs();
    renderPanel();
    return;
  }
  if (token !== openToken) return;
  $("#main").classList.remove("is-loading");
  state.detail = detail;
  state.ops = null;
  renderTitleBlock(detail);
  renderDrawing(detail);
  renderTabs();
  renderPanel();
  announce(`Geöffnet: ${detail.spec?.title || name}`);
  setup3D(detail, token);
  showStepLink(detail); // after setup3D, which clears the link when it finds no model
}

function route() {
  const [rawName, rawTab] = location.hash.slice(1).split("/");
  let name = rawName ?? "";
  try { name = decodeURIComponent(name); } catch { /* fehlerhaften Hash so lassen, wie er ist */ }
  if (name) { openPart(name, rawTab); return; }
  if (state.parts.length && !state.run) { go(state.parts[0].name); return; }
  if (!state.parts.length && !state.run) showEmpty();
}

function showEmpty() {
  state.name = null;
  state.detail = null;
  renderEmptyTitleBlock();
  $("#drawing").replaceChildren(h("p", { class: "empty empty-sheet" }, "Noch keine Teile. Zeichnung links unten ablegen und STEP erstellen."));
  reset3D();
  renderTabs();
  $("#panel").replaceChildren();
  if (state.loaded && !state.loadError) $("#file").focus();
}

async function loadParts() {
  if (!state.loaded) document.body.classList.add("loading");
  state.loadError = null;
  try {
    state.parts = await getJson("/api/parts");
    state.loaded = true;
  } catch (error) {
    state.loadError = friendly(error);
  }
  document.body.classList.remove("loading");
  renderParts();
  if (state.detail) renderTabs();
}

async function reload() {
  await loadParts();
  route();
}

window.addEventListener("hashchange", route);
new ResizeObserver(() => fitPaper()).observe($("#drawing"));
new ResizeObserver(() => fitTitle()).observe($(".cell-title"));
document.fonts?.ready.then(fitTitle);
initPartsList();
initTabs();
initViewer();
/** Die Modellauswahl des Servers (Claude, ein konfigurierter Endpunkt, die lokalen Ollama-Modelle), gruppiert. */
async function loadModels() {
  const select = $("#model");
  try {
    const { models } = await getJson("/api/models");
    const groups = new Map();
    for (const m of models) {
      if (!groups.has(m.group)) {
        const group = document.createElement("optgroup");
        group.label = m.group;
        groups.set(m.group, group);
        select.append(group);
      }
      const option = new Option(m.label, m.value);
      option.disabled = Boolean(m.disabled);
      groups.get(m.group).append(option);
    }
    const first = models.find((m) => !m.disabled);
    if (first) select.value = first.value;
  } catch { /* ohne die Liste wählt der Agent selbst sein Modell */ }
}

initUpload();
loadModels();
renderParts();
reload();
