"use strict";
/* Drawing2CAD Ergebnisanzeige: Teileliste, Zeichnung, 3D-Ansicht, Prüftabellen und das laufende Laufprotokoll.
   Einfaches JavaScript ohne Build-Schritt; einzige Bibliothek ist das mitgelieferte model-viewer. */

// ---------------------------------------------------------------------------
// Konstanten
// ---------------------------------------------------------------------------
// CadQuery ist Z-up, glTF ist Y-up: diese Kamerapositionen wurden am Demoteil kalibriert.
const VIEWS = { front: "0deg 90deg auto", top: "0deg 0deg auto", side: "90deg 90deg auto", iso: "45deg 60deg auto" };
const VIEW_BUTTONS = [["front", "Front"], ["top", "Top"], ["side", "Side"], ["iso", "Iso"]];
const GLOW = [0.5, 0.12, 0]; // Emissionsfaktor der orangefarbenen Flächen, während eine Prüfzeile überfahren wird
const DIMMED = 0.35; // Farbanteil, den die anderen Materialien solange behalten
// Ränder des Blattrahmens als Anteile der dargestellten Seite, an der Demo-Zeichnung kalibriert.
const FRAME = { l: 0.048, t: 0.034, r: 0.047, b: 0.034 };
const MAX_UPLOAD = 20 * 1024 * 1024;
const STAGES = [["read", "READ", "A"], ["describe", "DESCRIBE", "B"], ["build", "BUILD", "C"], ["check", "CHECK", "D"], ["export", "EXPORT", "E"]];
const STAGE_OF = {
  render_drawing: "read", read_drawing_text: "read", measure_drawing: "read",
  submit_spec: "describe", check_spec: "describe",
  extrude: "build", revolve: "build", add_holes: "build", edge_finish: "build", run_script: "build",
  edit_op: "build", delete_op: "build", move_op: "build",
  export_step: "export",
};
const NEUTRAL_TOOLS = new Set(["get_state"]); // wird mitten im Aufbau gerufen, nur im Protokoll sichtbar
const PHASE_STAGE = { read: "read", build: "build" };
const TABS = [
  ["review", "Review list"], ["validation", "Checks"], ["tolerances", "Tolerances"],
  ["traceability", "Traceability"], ["benchmark", "Reference"], ["files", "Files"],
];
const FILE_ORDER = ["STEP", "MID STEP", "GLB", "MD", "JSON"];
// Anzeige der Prüfcodes aus review.json; der Serverwert bleibt, unbekannte Codes erscheinen unverändert.
const ISSUE_LABEL = {
  missing: "missing", ambiguous: "ambiguous", low_confidence: "low confidence", conflict: "conflict", convention: "convention",
};

const CHANGE_AREA = {
  dimension: "Dimension", tolerance: "Tolerance", general_tolerance: "General tolerance", geometric: "Geometric tolerance", hole: "Hole", bbox: "Bounding box",
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
const friendly = (e) => (e instanceof TypeError ? "The server is not reachable." : e.message);
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
  // all_ok: null ohne Export, false nach einem Export mit einer nicht bestandenen Prüfung, true exportiert und geprüft
  const exported = part.all_ok != null, checked = part.all_ok === true;
  const count = part.review_count ?? 0;
  const tags = [
    part.scale_conflict ? h("span", { class: "tag", title: "The review list contains a conflict with the scale" }, "SCALE") : null,
    exported ? null : h("span", { class: "tag tag-outline" }, "NOT EXPORTED"),
    exported && !checked ? h("span", { class: "tag tag-outline", title: "Exported, but not every check passed" }, "CHECK FAILED") : null,
  ].filter(Boolean);
  return h("li", {
    class: "part", role: "option", "data-name": part.name, title: part.name,
    "aria-selected": String(part.name === state.name), tabindex: part.name === focusName ? "0" : "-1",
  },
    h("span", { class: "pos mono" }, String(index + 1).padStart(2, "0")),
    h("span", { class: "name" }, part.title || part.name),
    count ? h("span", { class: "assumed", title: `${count} assumed` }, icon("triangle", 12), String(count), sr(" assumed")) : null,
    checked ? h("span", { class: "ok" }, icon("check", 14), sr("Exported and checked")) : null,
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
    box.replaceChildren(hatchBox("", "The parts could not be loaded.", h("button", { type: "button", class: "btn btn-secondary", onclick: () => reload() }, "Try again")));
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
    box.replaceChildren(plainBox("No parts yet."));
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
// Ein Widerspruch zum Maßstab: issue "conflict" und das Wort Maßstab oder scale (wie _is_scale_conflict im Server)
const SCALE_WORDS = /ma(?:ß|ss)stab|scale/i;
const printedScale = (entry, scale) =>
  [...`${entry.assumption} ${entry.reason}`.matchAll(/\d+:\d+/g)].map((m) => m[0]).find((s) => s !== scale);

function scaleConflict(detail) {
  const rows = assumptionsOf(detail);
  const at = rows.findIndex((a) => a.issue === "conflict" && SCALE_WORDS.test(`${a.assumption} ${a.reason}`));
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
  const exported = detail.all_ok != null, checked = detail.all_ok === true;
  const title = spec.title || detail.title || detail.name;
  setTitle(title, checked);
  document.title = `${title} - Drawing2CAD`;
  $("#tb-drawing").textContent = spec.drawing ? basename(spec.drawing) : "-";
  $("#tb-drawing").title = spec.drawing ?? "";
  $("#tb-page").textContent = spec.drawing ? `Sheet ${spec.page ?? 1}` : "";
  const scale = $("#tb-scale");
  scale.replaceChildren(spec.scale ?? "-");
  const conflict = scaleConflict(detail);
  if (conflict) {
    const other = printedScale(conflict.entry, spec.scale);
    if (other) scale.prepend(h("s", { class: "struck", title: "Scale printed on the drawing" }, other), " ");
    scale.append(" ", h("a", {
      href: `#${enc(detail.name)}/review`, class: "delta-link", title: `Review item ${conflict.number}: ${conflict.entry.assumption}`,
      onclick: (e) => { e.preventDefault(); setTab("review"); flashRows([conflict.entry.id]); },
    }, icon("triangle", 14), String(conflict.number), sr(` review item ${conflict.number}, scale conflict`)));
  }
  $("#tb-tolerance").textContent = spec.general_tolerance || "not specified";
  const status = $("#tb-status");
  if (!exported) {
    status.replaceChildren(h("span", { class: "tag tag-outline tag-big" }, "NOT EXPORTED"));
  } else {
    const rows = detail.review?.validation ?? [];
    const passed = rows.filter((r) => r.ok).length;
    const assumed = detail.review?.assumptions?.length ?? 0;
    status.replaceChildren(
      h("span", { class: "assumed-line" }, `${assumed} ASSUMED`),
      checked
        ? h("span", { class: "ok-line" }, `${passed}/${rows.length} OK`, icon("check", 16))
        : h("span", { class: "fail" }, `${passed}/${rows.length} OK`, icon("cross", 14)));
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
  $("#tb-status").replaceChildren(h("span", { class: "tag tag-outline tag-big" }, run.done ? (run.result?.ok ? "DONE" : "STOPPED") : "RUNNING"));
  restartDraw();
}

function renderEmptyTitleBlock() {
  setTitle("No part", false);
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
    area.append(hatchBox("", "There is no specification for this part yet, and therefore no drawing."));
    return;
  }
  const title = spec.title || detail.name, page = spec.page ?? 1;
  const img = h("img", { class: "paper-img", alt: `Drawing of ${title}, sheet ${page}`, draggable: "false" });
  const paper = h("div", {
    class: "paper is-loading", tabindex: "0", role: "group",
    "aria-label": "Drawing. The keys plus, minus and zero zoom.",
  }, h("div", { class: "paper-inner" }, img));
  const zoomButton = (name, label, handler) => h("button", { type: "button", class: "icon-btn", "aria-label": label, title: label, onclick: handler }, icon(name, 16));
  const frame = h("div", { class: "frame" }, paper);
  area.append(h("div", { class: "sheet-area" }, frame),
    h("div", { class: "zoom", role: "group", "aria-label": "Zoom" },
      zoomButton("plus", "Zoom in", () => zoomAt(1.4)),
      zoomButton("minus", "Zoom out", () => zoomAt(1 / 1.4)),
      zoomButton("reset", "Reset zoom", () => { drawing.k = 1; drawing.x = drawing.y = 0; applyZoom(); })));
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
    let message = "The drawing could not be displayed.";
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
  viewer.addEventListener("error", () => reset3D("The 3D model could not be loaded."));
  viewer.addEventListener("camera-change", (e) => { if (e.detail?.source === "user-interaction") pressedView(null); });
  viewer.addEventListener("pointerdown", (e) => { view.down = { x: e.clientX, y: e.clientY }; });
  viewer.addEventListener("pointerup", (e) => {
    const down = view.down;
    view.down = null;
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5) onViewerClick(e); // ein Klick, kein Schwenken
  });
}

/** Das 3D-Fenster ohne Modell: der Punkteboden und ein Satz (die Meldung kann den Grund nennen). */
function reset3D(message = "The 3D model appears after the export, with colours.") {
  removeHotspot();
  view.materials = [];
  view.glowing = false;
  state.marks = null;
  viewer.hidden = true;
  viewer.classList.remove("is-pending");
  viewer.src = null; // model-viewer spiegelt src nicht ins Attribut: die Eigenschaft setzen, nicht das Attribut
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
  viewer.alt = `3D model of ${detail.spec?.title || detail.name}, ${assumed} ${assumed === 1 ? "assumption" : "assumptions"} marked orange`;
  viewer.classList.add("is-pending"); // das bisherige Modell darf unter der Lademitteilung nicht sichtbar sein
  hint.hidden = false;
  hint.textContent = "Loading 3D model …";
  const url = `/files/${enc(detail.name)}/${enc(glb)}`;
  if (viewer.src === url) { // dieselbe Datei erneut feuert kein load: erst leeren
    viewer.src = null;
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
  if (state.run) tabs.push(["run", "Run", ""]);
  if (detail?.runs?.length) tabs.push(["runs", "Runs", String(detail.runs.length)]);
  const rows = detail?.review?.validation ?? [];
  const tol = detail?.review?.tolerances ?? [];
  const tolChecked = tol.filter((t) => t.in_tolerance_nominal === true || t.in_tolerance_nominal === false);
  const counts = {
    review: detail ? String(assumptionsOf(detail).length) : "",
    validation: rows.length ? `${rows.filter((r) => r.ok).length}/${rows.length}` : "",
    // ohne gebundene Werte ist nichts geprüft: dann steht dort die Zahl der tolerierten Maße
    tolerances: tol.length ? (tolChecked.length ? `${tolChecked.filter((t) => t.in_tolerance_nominal === true).length}/${tolChecked.length}` : String(tol.length)) : "",
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
    return dim ? h("span", { class: "dim dim-assumed", title: `Assumed value of ${ref.id}` }, `(${num(dim.value)})`) : h("span", { class: "mono" }, ref.id);
  }).flatMap((node, i) => (i ? [" ", node] : [node]));
}

function renderReview(detail) {
  const rows = assumptionsOf(detail);
  const panel = $("#panel");
  const dims = new Map((detail.spec?.dimensions ?? []).map((d) => [d.id, d]));
  const parts = [];
  if (!rows.length) {
    parts.push(plainBox("No assumptions: every value comes from the drawing."));
  } else {
    const body = rows.map((a, i) => {
      const row = h("tr", { class: "assumed-row", tabindex: "0", "data-id": a.id },
        h("td", { class: "col-delta", "data-label": "No." }, h("span", { class: "delta" }, icon("triangle", 12), String(i + 1))),
        h("td", { class: "mono", "data-label": "ID" }, a.id),
        h("td", { "data-label": "Dimension" }, a.refs.length ? dimensionCell(a.refs, dims) : "-"),
        h("td", { "data-label": "Finding" }, h("span", { class: "tag" }, ISSUE_LABEL[a.issue] ?? a.issue)),
        h("td", { "data-label": "Assumption" }, a.assumption),
        h("td", { "data-label": "Reason" }, a.reason),
        h("td", { class: "mono", "data-label": "Source" }, (a.sources ?? []).length ? a.sources.join(", ") : "-"),
        h("td", { class: "mono", "data-label": "Faces" }, a.marked_faces == null ? "-" : String(a.marked_faces)));
      linkRow(row, parseSources(a.sources));
      return row;
    });
    parts.push(table(["No.", "ID", "Dimension", "Finding", "Assumption", "Reason", "Source", "Faces"], body, "review"));
  }
  const changes = detail.review?.changes ?? [];
  if (changes.length) {
    parts.unshift(h("div", { class: "notes" }, h("h3", {}, "Changes from the previous version")),
      table(["Feature", "Before", "After", "Change"], changes.map((c) => h("tr", {},
        h("td", { "data-label": "Feature" }, `${CHANGE_AREA[c.area] ?? c.area} ${c.feature}`.trim()),
        h("td", { class: "mono", "data-label": "Before" }, c.before),
        h("td", { class: "mono", "data-label": "After" }, c.after),
        h("td", { "data-label": "Change" }, h("span", { class: "tag" }, c.change)))), "changes"));
  }
  const notes = detail.review?.notes ?? detail.spec?.notes ?? [];
  if (notes.length) {
    parts.push(h("div", { class: "notes" },
      h("h3", {}, "Drawing notes"),
      h("ul", {}, notes.map((n) => h("li", {}, h("span", { class: "mono" }, n.id), " ", n.text)))));
  }
  fill(panel, ...parts);
}

function renderValidation(detail) {
  const panel = $("#panel");
  const rows = detail.review?.validation;
  if (!rows) {
    fill(panel, plainBox("No checks yet. The last export did not pass, or the part was never exported."));
    return;
  }
  const passed = rows.filter((r) => r.ok).length;
  const status = (ok) => ok
    ? h("span", { class: "ok" }, icon("check", 14), " ", sr("passed"))
    : h("span", { class: "fail" }, icon("cross", 14), " FAILED");
  fill(panel,
    h("div", { class: "kpi" }, h("span", { class: "kpi-number" }, `${passed}/${rows.length}`), h("span", { class: "label" }, "checks passed")),
    table(["Check", "Expected", "Actual", "Result"], rows.map((r) => h("tr", {},
      h("td", { class: "mono", "data-label": "Check" }, r.check),
      h("td", { class: "mono", "data-label": "Expected" }, r.expected),
      h("td", { class: "mono", "data-label": "Actual" }, r.actual),
      h("td", { "data-label": "Result" }, status(r.ok)))), "validation"));
}

// ---------------------------------------------------------------------------
// Toleranzen: Grenzen, Toleranzmitte und Einhaltung je toleriertem Maß
// ---------------------------------------------------------------------------
const TOL_SOURCE = { explicit: "on dimension", fit: "fit", general: "general tolerance" };
const GD_KIND = {
  flatness: "Flatness", straightness: "Straightness", roundness: "Roundness", cylindricity: "Cylindricity",
  parallelism: "Parallelism", perpendicularity: "Perpendicularity", angularity: "Angularity",
  concentricity: "Concentricity", symmetry: "Symmetry", circular_runout: "Circular runout", total_runout: "Total runout",
  position: "Position", line_profile: "Line profile", surface_profile: "Surface profile",
};

/** Ein Abmaßpaar in einer Zeile: symmetrisch als ±, sonst oberes und unteres Abmaß. */
function deviation(lower, upper) {
  const signed = (value) => (typeof value === "number" && value > 0 ? `+${num(value)}` : num(value));
  return lower === -upper ? `±${num(upper)}` : `${signed(upper)} / ${signed(lower)}`;
}

/** Das Urteil zu einem tolerierten Maß; null heißt nicht geprüft, weil kein Wert an das Maß gebunden ist. */
function toleranceVerdict(ok, geometry = false) {
  if (ok === true) {
    return h("span", { class: "ok", title: geometry ? "from the measured body" : null }, icon("check", 14), " ", sr("within tolerance"));
  }
  if (ok === false) return h("span", { class: "fail" }, icon("cross", 12), " VIOLATED");
  return h("span", { class: "muted" }, "not checked");
}

/** Wie viele Zeilen eines Urteilsfelds sind geprüft, wie viele davon eingehalten: Grundlage der Kennzahlen. */
function toleranceTally(rows, field) {
  const checked = rows.filter((t) => t[field] === true || t[field] === false);
  return { ok: checked.filter((t) => t[field] === true).length, checked: checked.length, total: rows.length };
}

/** Die Form- und Lagetoleranzen der Prüfliste; ohne Rahmen steht nur die Art in der Zeichnung. */
function geometricTable(rows) {
  return [
    h("h3", {}, "Geometric tolerances"),
    table(["ID", "Type", "Value", "Datums", "Frame", "PMI in STEP"], rows.map((t) => h("tr", {},
      h("td", { class: "mono", "data-label": "ID" }, t.id),
      h("td", { "data-label": "Type" }, GD_KIND[t.kind] ?? t.kind),
      h("td", { class: "mono", "data-label": "Value" }, num(t.value)),
      h("td", { class: "mono", "data-label": "Datums" }, (t.datums ?? []).join(", ") || "-"),
      h("td", { class: "mono", "data-label": "Frame" }, t.text),
      h("td", { "data-label": "PMI in STEP" }, t.in_step ? "yes" : "no"))), "geometric"),
  ];
}

function renderTolerances(detail) {
  const panel = $("#panel");
  const rows = detail.review?.tolerances;
  if (!detail.review || rows == null) {
    fill(panel, plainBox("No tolerance table yet. It is created with the export."));
    return;
  }
  if (!rows.length) {
    fill(panel, plainBox("No toleranced dimensions: no dimension carries limit deviations, a fit or a general tolerance."));
    return;
  }
  const dims = new Map((detail.spec?.dimensions ?? []).map((d) => [d.id, d]));
  const nominal = toleranceTally(rows, "in_tolerance_nominal");
  const mid = toleranceTally(rows, "in_tolerance_mid");
  const geo = detail.review.geometric_tolerances ?? [];
  const midFile = (detail.files ?? []).find((f) => /_mid\.(step|stp)$/i.test(f));
  const body = rows.map((t, i) => {
    const row = h("tr", { tabindex: "0", "data-id": t.dim_id },
      h("td", { class: "mono", "data-label": "No." }, String(i + 1).padStart(2, "0")),
      h("td", { class: "mono", "data-label": "ID" }, t.dim_id),
      h("td", { "data-label": "Dimension" }, t.text ? h("span", { class: "dim" }, t.text) : "-"),
      h("td", { class: "mono", "data-label": "Nominal" }, num(t.nominal)),
      h("td", { class: "mono", "data-label": "Deviations" }, deviation(t.lower, t.upper)),
      h("td", { class: "mono", "data-label": "Midpoint" }, num(t.mid)),
      h("td", { class: "mono", "data-label": "Source" }, TOL_SOURCE[t.source] ?? t.source),
      h("td", { "data-label": "Nominal result" }, toleranceVerdict(t.in_tolerance_nominal, t.geometry === true)),
      h("td", { "data-label": "Midpoint result" }, toleranceVerdict(t.in_tolerance_mid)));
    const source = dims.get(t.dim_id)?.source;
    linkRow(row, source ? [{ view: source.view, cell: source.cell }] : []);
    return row;
  });
  fill(panel,
    h("div", { class: "kpi" },
      h("span", { class: "kpi-number" }, `${nominal.ok}/${nominal.checked}`), h("span", { class: "label" }, "nominal within tolerance")),
    h("div", { class: "kpi" },
      h("span", { class: "kpi-number" }, `${mid.ok}/${mid.checked}`), h("span", { class: "label" }, "tolerance midpoint within tolerance")),
    table(["No.", "ID", "Dimension", "Nominal", "Deviations", "Midpoint", "Source", "Nominal result", "Midpoint result"], body, "tolerances"),
    h("p", { class: "muted notes" }, `${nominal.checked} of ${nominal.total} toleranced dimensions are checked; not checked means no value is bound to the dimension. The source is the specification on the dimension, a fit per ISO 286 or the general tolerance per ISO 2768.`),
    geo.length ? h("div", { class: "notes" }, geometricTable(geo)) : null,
    midFile ? h("p", { class: "muted notes" }, "Model at the tolerance midpoint: ",
      h("a", { href: `/files/${enc(detail.name)}/${enc(midFile)}`, download: midFile }, midFile)) : null);
}

async function renderTraceability(detail) {
  const panel = $("#panel");
  const review = detail.review;
  if (!review) {
    fill(panel, plainBox("No traceability yet. It is created with the export."));
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
      `Op ${t.op_index + 1} `, h("span", { class: "opname" }, opName(t.op_index)), ` · ${t.path} = ${num(t.value)}`,
      linked ? [h("span", { class: "back" }, icon("back", 14), sr(" from")), ` ${t.dimension} `,
        t.text ? h("span", { class: "dim" }, `"${t.text}"`) : h("span", { class: "dim dim-assumed" }, "(assumed)")] : null);
    if (linked) {
      const source = dims.get(t.dimension)?.source;
      linkRow(row, source ? [{ view: source.view, cell: source.cell }] : []);
    }
    return row;
  };
  const traced = review.traceability ?? [], loose = review.unreferenced ?? [];
  fill(panel,
    traced.length ? h("ul", { class: "trace-list" }, traced.map((t) => line(t, true))) : plainBox("No value traces back to a dimension."),
    h("div", { class: "loose" },
      h("h3", {}, "Values without a dimension"),
      loose.length ? h("ul", { class: "trace-list" }, loose.map((t) => line(t, false))) : h("p", { class: "muted" }, "None.")));
}

function matchWord(row) {
  const raw = row.match ?? row.MATCH;
  if (raw === "MATCH") return h("span", { class: "match match-yes" }, "MATCH");
  if (raw === "DIFF") return h("span", { class: "match match-diff" }, "DEVIATION");
  return h("span", { class: "match match-invalid" }, "Reference invalid");
}

// Anzeige für die Benchmark-Marks; die Werte (MATCH, DIFF, …) bleiben Serverdaten.
const KIND_LABEL = { yes: "Match", diff: "Deviation", invalid: "Reference invalid", none: "no reference" };

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
    h("p", { class: "kpi-line" }, h("span", { class: "kpi-number" }, `${tally.matched}/${tally.total}`), h("span", { class: "label" }, "Matches"),
      tally.invalid ? h("span", { class: "muted" }, `${tally.invalid} with invalid reference, not counted`) : null),
    h("div", { class: "ticks", role: "group", "aria-label": "Parts on the reference ruler" },
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
    fill(panel, bar, plainBox("No reference for this part."));
    return;
  }
  const sorted = (list) => (Array.isArray(list) ? list : []);
  const own = sorted(row.bbox_sorted), ref = sorted(row.ref_bbox_sorted);
  const unit = (value, suffix) => (typeof value === "number" ? `${num(value, 3)} ${suffix}` : "-");
  fill(panel, bar,
    h("div", { class: "bench" },
      h("div", { class: "bench-head" }, matchWord(row),
        h("div", { class: "kpi" }, h("span", { class: "kpi-number" }, unit(row.dv_pct, "%")), h("span", { class: "label" }, "volume deviation")),
        h("div", { class: "kpi" }, h("span", { class: "kpi-number" }, unit(row.dbbox_max_mm, "mm")), h("span", { class: "label" }, "largest bounding box deviation"))),
      h("div", { class: "bench-box" },
        h("p", { class: "label" }, "Bounding box, sorted (own / reference)"),
        [0, 1, 2].map((i) => h("p", { class: "mono" }, `${num(own[i], 3)}  /  ${num(ref[i], 3)}`)),
        row.missing_d?.length ? h("p", { class: "mono" }, `missing diameters: ${row.missing_d.join(", ")}`) : null,
        row.extra_d?.length ? h("p", { class: "mono" }, `extra diameters: ${row.extra_d.join(", ")}`) : null)));
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
    h("a", { class: "btn btn-link", href: `/files/${enc(detail.name)}/${enc(f)}`, download: f }, "Download", sr(` ${f}`))));
  const blocked = hasStep ? null : hatchBox("STOPPED", "No STEP file: the export only runs if every check passes.");
  const versions = detail.versions ?? [];
  fill(panel, blocked, files.length ? h("ul", { class: "files" }, rows) : plainBox("No files."),
    h("p", { class: "muted versions", title: versions.join(", ") }, `earlier versions: ${versions.length}`));
}

// Stored runs: the web app writes every run as a trace under results/<part>/runs/
function localTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("en-GB", { dateStyle: "short", timeStyle: "medium" });
}

function runOutcome(run) {
  if (run.running) return h("span", { class: "muted" }, "running");
  if (run.ok === true) return h("span", { class: "ok" }, icon("check", 14), sr("ok"));
  if (run.ok === false) return h("span", { class: "fail" }, icon("cross", 12), " ", brief(run.reason || "stopped", 70));
  return h("span", { class: "muted" }, "aborted");
}

function renderRuns(detail) {
  const panel = $("#panel");
  const runs = detail?.runs ?? [];
  if (!runs.length) {
    fill(panel, plainBox("No runs stored."));
    return;
  }
  const rows = runs.map((run) => {
    const open = () => openStoredRun(detail.name, run);
    return h("tr", {
      class: "run-row", tabindex: "0", onclick: open,
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } },
    },
      h("td", { class: "mono", "data-label": "Started" }, run.started ? localTime(run.started) : "-"),
      h("td", { "data-label": "Model" }, run.model || "Default"),
      h("td", { "data-label": "Result" }, runOutcome(run)),
      h("td", { class: "mono", "data-label": "Events" }, String(run.events)));
  });
  fill(panel, table(["Started", "Model", "Result", "Events"], rows, "runs"),
    h("p", { class: "muted" }, "Click or Enter opens the run and replays its events."));
}

function renderPanel() {
  const panel = $("#panel");
  panel.setAttribute("aria-labelledby", `tab-${state.tab}`);
  const detail = state.detail;
  if (state.tab === "run" && state.run) { renderRun(); return; }
  if (!detail) {
    fill(panel, plainBox(state.run ? "Select a part to see the details." : "Select a part."));
    return;
  }
  panelToken++;
  ({ review: renderReview, validation: renderValidation, tolerances: renderTolerances, traceability: renderTraceability, benchmark: renderBenchmark, files: renderFiles, runs: renderRuns }[state.tab] ?? renderReview)(detail);
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

const LIMIT_LABELS = { budget: "Cost limit reached", time: "Time limit reached" }; // Feld `limit` des done-Ereignisses

function finishRun(run, event, now = performance.now()) {
  if (run.done) return;
  run.done = true;
  run.es?.close(); // der Server beendet den Stream nach done; sonst würde das EventSource neu verbinden und alles wiederholen
  clearInterval(run.timer);
  const ok = event.ok === true;
  const limit = LIMIT_LABELS[event.limit];
  run.result = { ok, reason: [limit, event.reason ? String(event.reason) : ""].filter(Boolean).join(": "), limit: event.limit ?? null };
  for (const seg of [...run.open.values()].flat()) { seg.dur = now - seg.start; seg.ok = false; }
  run.open.clear();
  if (!ok && run.current) run.stages[run.current].state = "failed";
  if (ok) for (const stage of Object.values(run.stages)) if (stage.state === "active") stage.state = "done";
  run.log.push({ at: now - run.t0, kind: "DONE", label: ok ? "exported" : "stopped", summary: run.result.reason, detail: "" });
  refreshRun(run);
  if (state.run === run && !state.name) renderRunTitleBlock(run);
  if (!run.replay) afterRun(run); // ein wiederholter Lauf lädt nichts neu
}

function connectionLost(run) {
  if (run.done) return;
  run.es?.close();
  clearInterval(run.timer);
  run.lost = true;
  const now = performance.now();
  for (const seg of [...run.open.values()].flat()) { seg.dur = now - seg.start; seg.ok = false; }
  run.open.clear();
  run.log.push({ at: now - run.t0, kind: "LOST", label: "", summary: `Connection lost at ${clock(now - run.t0, false)}` });
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
        stage.state === "failed" ? sr(" stopped") : null),
      h("div", { class: "segs" }, stage.segs.map((seg) => segView(seg, now, still))));
  });
  if (run.done) {
    const ok = run.result.ok;
    items.push(h("li", { class: `result ${ok ? "is-yes" : "is-no"}` },
      ok ? h("p", { class: "result-word" }, "EXPORTED")
        : hatchBox("STOPPED", run.result.reason || "The run ended without an export.")));
  }
  return h("ol", { class: "chain", "aria-label": "Run stages" }, items);
}

function logRow(entry) {
  const detail = entry.detail ? h("details", {}, h("summary", {}, entry.kind === "CALL" ? "Arguments" : "Text"), h("pre", {}, entry.detail)) : null;
  return h("tr", { class: `log-${entry.kind.toLowerCase()}` },
    h("td", { class: "mono t" }, clock(entry.at)),
    h("td", { class: "mono kind" }, entry.kind === "FAIL" ? h("span", { class: "fail" }, icon("cross", 12), " FAILED") : entry.kind),
    h("td", { class: "mono tool" }, entry.label),
    h("td", { class: "mono what" }, entry.dur != null ? `${(entry.dur / 1000).toFixed(1)} s  ` : "", entry.summary, detail));
}

function renderRun() {
  const run = state.run;
  const panel = $("#panel");
  fill(panel,
    h("div", { class: "run" },
      h("div", { id: "chain-host" }, chainView(run, performance.now())),
      h("div", { class: "log", id: "log", role: "log", "aria-live": "polite", "aria-label": "Run log" },
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

/** Einen gespeicherten Lauf laden und seine Ereignisse mit den aufgezeichneten Zeitabständen erneut abspielen. */
async function openStoredRun(name, entry) {
  let data;
  try {
    data = await getJson(`/api/parts/${enc(name)}/runs/${enc(entry.id)}`);
  } catch (error) {
    showNotice(`The run could not be loaded: ${friendly(error)}`);
    return;
  }
  const run = newRun(entry.id, entry.part || name, basename(entry.pdf));
  run.replay = true;
  const still = reducedMotion();
  const started = performance.now();
  run.t0 = started;
  const events = data.events ?? [];
  const offset = (event) => { const at = Number(event?.at); return Number.isFinite(at) && at > 0 ? at * 1000 : 0; };
  for (const event of events) {
    if (still) handleEvent(run, event, started + offset(event));
    else setTimeout(() => handleEvent(run, event, started + offset(event)), offset(event));
  }
  // Ein Lauf ohne Abschlussereignis gilt nach dem letzten Ereignis als abgebrochen.
  setTimeout(() => connectionLost(run), still ? 0 : offset(events[events.length - 1]) + 50);
  if (!still) run.timer = setInterval(() => refreshRun(run), 250);
  state.run = run;
  setTab("run");
}

function renderRunStage(run) {
  $("#drawing").replaceChildren(hatchBox("", `The drawing of ${run.part} appears here as soon as the agent has submitted the specification.`));
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
  if (status === 503) { showNotice(`${message} Stored parts remain available.`); return; }
  if (status === 0 || status === 403 || status === 409 || status === 429) { showNotice(message); return; } // keine Antwort, fremde Seite, Teil belegt, zu viele Läufe

  const target = /part name|revision/i.test(message) ? "part" : /\bmodel\b|language model/i.test(message) ? "model" : "file";
  fieldError(target, message);
  $(`#${target}`).focus();
}

const NO_MODEL = "No language model available: start Ollama (`OLLAMA_CONTEXT_LENGTH=64000 ollama serve`), sign in to Claude Code or set `.env`. Without a model, `python -m d2c.demo` shows the flow.";
const NAME_CHARS = /[^\p{L}\p{N}_-]+/gu; // Unicode-Buchstaben wie im Server und im HTML-Muster
const NAME_OK = /^[\p{L}\p{N}_-]+$/u;
const MAX_NAME = 64;

/** Teilname aus dem Dateinamen; ohne brauchbare Zeichen ein Name aus der Uhrzeit. */
function nameFromFile(fileName) {
  const name = fileName.normalize("NFC").replace(/\.[^.]+$/, "").replace(NAME_CHARS, "_").replace(/^_+|_+$/g, "").slice(0, MAX_NAME);
  if (name) return name;
  const now = new Date(), two = (n) => String(n).padStart(2, "0");
  return `drawing-${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}`;
}

/** Ob die Modellauswahl eine wählbare Eintragung hat (sonst würde der Lauf ohne Modell scheitern). */
const hasModel = () => $("#model").dataset.unknown === "1" || (Boolean($("#model").value) && !$("#model").selectedOptions[0]?.disabled);

function showNoModel() {
  fieldError("model", NO_MODEL);
  announce(NO_MODEL);
}

/** Die Revisionsoption gilt nur für ein Teil, das es schon gibt (ein Ergebnis mit Spezifikation und Vorgängen). */
function syncRevise() {
  const name = $("#part").value.normalize("NFC").trim().toLowerCase();
  const exists = state.parts.some((p) => p.name.toLowerCase() === name);
  $("#revise").disabled = !exists;
  if (!exists) $("#revise").checked = false;
}

function initUpload() {
  const form = $("#upload"), file = $("#file"), part = $("#part");
  part.addEventListener("input", syncRevise);
  let suggested = ""; // der zuletzt vorgeschlagene Teilname: ein von Hand geänderter Name wird nicht überschrieben
  file.addEventListener("change", async () => {
    fieldError("file", "");
    const chosen = file.files[0];
    $("#file-name").textContent = chosen?.name ?? "No file chosen";
    if (!chosen) return;
    if (!part.value || part.value === suggested) part.value = suggested = nameFromFile(chosen.name);
    syncRevise();
    if ($("#manual").checked) return; // "nur hochladen": Name und Modell anpassen, dann STEP erstellen
    const name = part.value.normalize("NFC").trim();
    if (!$("#revise").checked && state.parts.some((p) => p.name.toLowerCase() === name.toLowerCase())) {
      // Das alte Ergebnis wandert als frühere Version zur Seite; ein Lauf soll aber nicht ungefragt dorthin führen.
      showNotice(`The part ${name} already exists. Choose another name or click Create STEP: the existing result is kept as an earlier version.`);
      return;
    }
    if (!NAME_OK.test(name) || name.length > MAX_NAME) return;
    await modelsReady;
    if (hasModel()) form.requestSubmit(); else showNoModel();
  });
  for (const type of ["dragenter", "dragover"]) form.addEventListener(type, (e) => { e.preventDefault(); form.classList.add("is-over"); });
  for (const type of ["dragleave", "drop"]) form.addEventListener(type, (e) => { e.preventDefault(); form.classList.remove("is-over"); });
  form.addEventListener("drop", (e) => {
    if (!e.dataTransfer?.files.length) return;
    file.files = e.dataTransfer.files;
    file.dispatchEvent(new Event("change"));
  });
  $("#new-run").addEventListener("click", () => { file.focus(); file.scrollIntoView({ block: "nearest" }); });

  let starting = false; // zwischen Abschicken und Antwort ist kein zweiter Start möglich
  async function submitRun() {
    if (starting) return;
    clearErrors();
    const chosen = file.files[0];
    const name = part.value.normalize("NFC").trim();
    if (!chosen) { fieldError("file", "Please choose a drawing file (PDF, PNG or JPG)."); file.focus(); return; }
    if (chosen.size > MAX_UPLOAD) { fieldError("file", "The file is larger than 20 MB."); file.focus(); return; }
    if (!NAME_OK.test(name) || name.length > MAX_NAME) { fieldError("part", `Use only letters, digits, '_' and '-' (at most ${MAX_NAME} characters).`); part.focus(); return; }
    if (state.run && !state.run.done && !state.run.lost) { showNotice("A run is still in progress."); return; }
    const body = new FormData();
    body.append("pdf", chosen);
    body.append("part", name);
    if ($("#model").value.trim()) body.append("model", $("#model").value.trim());
    if ($("#revise").checked) body.append("revise", "true");
    const button = $("#start");
    starting = true;
    button.disabled = true;
    try {
      const response = await fetch("/api/runs", { method: "POST", body });
      if (!response.ok) { showServerError(response.status, await errorMessage(response)); return; }
      const { run_id: id } = await response.json();
      button.textContent = "Restart"; // die Zeichnung bleibt gewählt: ein weiterer Klick startet denselben Teil erneut
      announce(`Analysis started: ${name}`);
      startRun(id, name, chosen.name);
    } catch (error) {
      showServerError(0, friendly(error));
    } finally {
      starting = false;
      button.disabled = false;
    }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); submitRun(); });
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
  const known = tab === "run" ? state.run : tab === "runs" ? true : TABS.some(([k]) => k === tab);
  state.tab = tab && known ? tab : "review";
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
    $("#drawing").replaceChildren(hatchBox("", `${name} could not be loaded: ${friendly(error)}`, h("button", { type: "button", class: "btn btn-secondary", onclick: () => openPart(name, tab) }, "Try again")));
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
  announce(`Opened: ${detail.spec?.title || name}`);
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
  $("#drawing").replaceChildren(h("p", { class: "empty empty-sheet" }, "No parts yet. Drop a drawing at the bottom left; the analysis starts automatically."));
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
    syncRevise();
    state.loaded = true;
  } catch (error) {
    state.loadError = friendly(error);
  }
  document.body.classList.remove("loading");
  renderParts();
  if (state.detail) renderTabs();
}

/** Läuft auf dem Server noch ein Lauf (Seite neu geladen, zweiter Tab), die Ansicht wieder an ihn hängen. */
async function attachActiveRun() {
  if (state.run) return;
  try {
    const active = (await getJson("/api/runs")).find((r) => r.status === "running");
    if (active && !state.run) startRun(active.run_id, active.part, basename(active.pdf));
  } catch { /* ohne die Liste bleibt es bei der Teileansicht */ }
}

async function reload() {
  await loadParts();
  await attachActiveRun();
  route();
}

window.addEventListener("hashchange", route);
new ResizeObserver(() => fitPaper()).observe($("#drawing"));
new ResizeObserver(() => fitTitle()).observe($(".cell-title"));
document.fonts?.ready.then(fitTitle);
initPartsList();
initTabs();
initViewer();
/** Die Modellauswahl des Servers (Claude, ein konfigurierter Endpunkt, die lokalen Ollama-Modelle), gruppiert.
 *  Vorgewählt ist das zuletzt gewählte Modell, sonst das erste verfügbare; ohne wählbares Modell steht die Meldung da. */
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
    let last = "";
    try { last = localStorage.getItem("d2c.model") || ""; } catch { /* ohne Speicher gilt das erste verfügbare Modell */ }
    const usable = models.filter((m) => !m.disabled);
    const pick = usable.find((m) => m.value === last) ?? usable[0];
    if (pick) select.value = pick.value;
    select.addEventListener("change", () => { try { localStorage.setItem("d2c.model", select.value); } catch { /* nur eine Annehmlichkeit */ } });
  } catch { $("#model").dataset.unknown = "1"; /* ohne die Liste entscheidet der Server, ob ein Modell da ist */ }
  if (!hasModel() && !$("#file").files.length) showNoModel();
}

const modelsReady = loadModels(); // der Auto-Start nach dem Ablegen wartet darauf
initUpload();
renderParts();
reload();
