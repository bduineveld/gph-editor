/* GPH editor front end: plain JavaScript over the JSON API in gph/server.py. */
"use strict";

const state = {
  files: [],            // from /api/files
  rev: {},              // file id -> counter, busts image caches after edits
  route: { f: null, s: null, i: null },
  rows: [],             // rows of the current section
  filter: "",
  screens: null,        // from /api/screens (the unit's screen database), loaded on demand
};

const $ = (sel, el = document) => el.querySelector(sel);
const hex4 = (n) => n.toString(16).toUpperCase().padStart(4, "0");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(c));
  }
  return el;
}

function toast(msg, kind = "ok", ms = 4000) {
  const t = h("div", { class: "toast " + kind }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

async function api(method, url, body) {
  const r = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error(data.error || (r.status + " " + r.statusText));
  return data;
}

function busy(on) { document.body.style.cursor = on ? "progress" : ""; }

async function act(fn) {
  busy(true);
  try { return await fn(); }
  catch (e) { toast(e.message, "err", 7000); throw e; }
  finally { busy(false); }
}

const fileById = (id) => state.files.find((f) => f.id === id);
const isPublic = () => !!(state.settings && state.settings.public);

async function refreshFiles() {
  const r = await api("GET", "/api/files");
  state.files = r.files;
  renderSidebar();
}

function updateFile(info) {
  const i = state.files.findIndex((f) => f.id === info.id);
  if (i >= 0) state.files[i] = info; else { state.files.push(info); loadSettings(); }
  state.rev[info.id] = (state.rev[info.id] || 0) + 1;
  renderSidebar();
}

function imgUrl(fid, kind, index, extra = "") {
  const path = kind === "anim" ? "image" : kind;
  return `/api/files/${fid}/${path}/${index}.${kind === "anim" ? "gif" : "png"}?v=${state.rev[fid] || 0}${extra}`;
}

// ---------------------------------------------------------------------------
// routing
function go(f, s, i) {
  const parts = [];
  if (f != null) parts.push("f=" + f);
  if (s) parts.push("s=" + s);
  if (i != null) parts.push("i=" + i);
  location.hash = parts.join("&");
}

function parseRoute() {
  const q = Object.fromEntries(location.hash.slice(1).split("&").filter(Boolean).map((p) => p.split("=")));
  return { f: q.f != null ? +q.f : null, s: q.s || null, i: q.i != null ? +q.i : null };
}

async function onRoute() {
  const r = parseRoute();
  const sectionChanged = r.f !== state.route.f || r.s !== state.route.s;
  state.route = r;
  if (sectionChanged) {
    state.filter = "";
    await loadSection();
  }
  renderSidebar();
  highlightActive();
  if (r.i != null && r.s) showDetail(); else hideDetail();
}

// ---------------------------------------------------------------------------
// sidebar
const GROUP_ORDER = [["texts", "Texts"], ["colors", "Colours"], ["images", "Images"], ["textures", "Textures"], ["fonts", "Fonts"], ["other", "Widgets & layout"]];

function renderSidebar() {
  const nav = $("#sidebar");
  nav.innerHTML = "";
  const scr = state.settings && state.settings.screens;
  if (scr) {
    const active = state.route.s === "screens";
    nav.append(h("div", { class: "file" },
      h("div", { class: "file-head", title: scr.path || "" }, h("span", { class: "name" }, "Unit"), h("small", { class: "mono" }, "GraphicBase")),
      h("div", { class: "sec" + (active ? " active" : ""), onclick: () => go(null, "screens") },
        h("span", {}, "Screens ", h("small", { class: "mono" }, `${scr.named} named`)), h("span", { class: "n" }, String(scr.count)))));
  }
  if (!state.files.length) {
    nav.append(h("div", { class: "empty" }, "No files open"));
    return;
  }
  for (const f of state.files) {
    const box = h("div", { class: "file" + (f.dirty ? " dirty" : "") });
    box.append(h("div", { class: "file-head", title: f.path || "uploaded" },
      h("span", { class: "dot", title: "unsaved changes" }),
      h("span", { class: "name" }, f.label),
      h("small", { class: "mono" }, f.name.trim())));
    const pub = isPublic();
    box.append(h("div", { class: "file-tools" },
      pub ? h("button", { class: "small primary", onclick: () => downloadDialog(f.id) }, "Download")
          : h("button", { class: "small primary", onclick: () => saveDialog(f.id) }, "Save…"),
      h("button", { class: "small", disabled: !f.undo, onclick: () => fileAction(f.id, "undo") }, "Undo"),
      pub ? null : h("button", { class: "small", disabled: !f.dirty || !f.path, onclick: () => confirmRevert(f.id) }, "Revert"),
      h("button", { class: "small", onclick: () => closeFile(f.id) }, "Close")));
    for (const [g, title] of GROUP_ORDER) {
      const secs = f.sections.filter((s) => s.group === g && (g !== "other" || s.used));
      if (!secs.length) continue;
      if (g === "other") box.append(h("div", { class: "group-title" }, title));
      for (const s of secs) {
        const active = state.route.f === f.id && state.route.s === s.name;
        const label = g === "other" ? `${s.description || s.kind}` : title;
        box.append(h("div", { class: "sec" + (active ? " active" : "") + (s.used ? "" : " zero"), onclick: () => go(f.id, s.name) },
          h("span", {}, label, " ", h("small", { class: "mono" }, s.name)),
          h("span", { class: "n" }, String(s.used))));
      }
    }
    nav.append(box);
  }
}

async function fileAction(fid, action, body) {
  return act(async () => {
    const r = await api("POST", `/api/files/${fid}/${action}`, body || {});
    if (r.file) updateFile(r.file);
    if (action === "undo" || action === "revert") { await loadSection(); if (state.route.i != null) showDetail(); }
    return r;
  });
}

function confirmRevert(fid) {
  modal(h("div", {}, h("h2", {}, "Discard changes?"),
    h("p", {}, "This reloads ", h("code", {}, fileById(fid).label), " from disk and drops every unsaved edit."),
    h("div", { class: "modal-actions" },
      h("button", { onclick: closeModal }, "Keep editing"),
      h("button", { class: "danger", onclick: async () => { closeModal(); await fileAction(fid, "revert"); toast("Reverted"); } }, "Discard"))));
}

async function closeFile(fid) {
  const f = fileById(fid);
  if (f.dirty && !confirm(`${f.label} has unsaved changes. Close anyway?`)) return;
  await act(() => api("POST", `/api/files/${fid}/close`, {}));
  state.files = state.files.filter((x) => x.id !== fid);
  if (state.route.f === fid) go(null);
  renderSidebar();
  loadSettings();
}

// ---------------------------------------------------------------------------
// section views
async function loadSection() {
  const { f, s } = state.route;
  const main = $("#main");
  if (s === "screens") {
    if (!state.screens) state.screens = (await act(() => api("GET", "/api/screens"))).screens;
    state.rows = state.screens;
    renderScreens();
    return;
  }
  if (f == null || !s || !fileById(f)) {
    state.rows = [];
    main.innerHTML = "";
    main.append(h("div", { class: "empty" }, state.files.length ? "Pick a section on the left."
      : isPublic() ? "Upload GPH files to start (drop them on this page, or use Upload…)." : "Open a GPH file or folder to start."));
    return;
  }
  const r = await act(() => api("GET", `/api/files/${f}/items?section=${encodeURIComponent(s)}`));
  state.rows = r.items;
  renderSection();
}

function viewHead(title, sub, extra) {
  const input = h("input", { type: "text", placeholder: "filter…", value: state.filter,
    oninput: (e) => { state.filter = e.target.value; renderSection(true); } });
  return h("div", { class: "view-head" }, h("h2", {}, title), h("span", { class: "muted" }, sub), input,
    h("span", { class: "count" }), extra);
}

function matches(row, text) {
  if (!state.filter) return true;
  const q = state.filter.toLowerCase();
  return hex4(row.index).toLowerCase().includes(q) || String(row.index) === q || (text || "").toLowerCase().includes(q);
}

function renderSection(keepFocus) {
  if (state.route.s === "screens") return renderScreens(keepFocus);
  const { f, s } = state.route;
  const file = fileById(f);
  const sec = file.sections.find((x) => x.name === s);
  const main = $("#main");
  const focused = keepFocus && document.activeElement && document.activeElement.tagName === "INPUT";
  const caret = focused ? document.activeElement.selectionStart : 0;
  main.innerHTML = "";
  let body;
  if (sec.group === "texts") body = renderTexts();
  else if (sec.group === "colors") body = renderColors();
  else if (sec.group === "images") body = renderImages();
  else if (sec.group === "textures") body = renderTextures();
  else if (sec.group === "fonts") body = renderFonts();
  else body = renderOther();
  const head = viewHead(`${sec.description || sec.kind}`, `${s} · ${file.label}`);
  main.append(head, body.el);
  $(".count", head).textContent = `${body.shown} of ${state.rows.length}`;
  if (focused) { const inp = $(".view-head input"); inp.focus(); inp.setSelectionRange(caret, caret); }
  highlightActive();
}

function highlightActive() {
  for (const el of document.querySelectorAll("#main .active")) el.classList.remove("active");
  if (state.route.i == null) return;
  const el = $(`#main [data-i="${state.route.i}"]`);
  if (el) { el.classList.add("active"); if (el.scrollIntoViewIfNeeded) el.scrollIntoViewIfNeeded(false); }
}

function showText(t) {
  const frag = document.createDocumentFragment();
  const parts = String(t).split("\r");
  parts.forEach((p, i) => { if (i) frag.append(h("span", { class: "br" }, "↵")); frag.append(p); });
  return frag;
}

function renderTexts() {
  const rows = state.rows.filter((r) => matches(r, r.text));
  const tb = h("tbody");
  for (const r of rows) {
    tb.append(h("tr", { "data-i": r.index, onclick: () => go(state.route.f, state.route.s, r.index) },
      h("td", { class: "idx" }, hex4(r.index)), h("td", {}, showText(r.text))));
  }
  return { shown: rows.length, el: h("table", { class: "items" }, h("thead", {}, h("tr", {}, h("th", {}, "index"), h("th", {}, "text"))), tb) };
}

function renderColors() {
  const rows = state.rows.filter((r) => matches(r, r.description + " " + r.hex));
  const grid = h("div", { class: "grid" });
  for (const r of rows) {
    grid.append(h("div", { class: "card", "data-i": r.index, onclick: () => go(state.route.f, state.route.s, r.index) },
      h("div", { class: "swatch" }, h("span", { style: `background:#${r.hex}` })),
      h("div", { class: "cap" }, h("b", {}, hex4(r.index)), "#" + r.hex),
      r.description ? h("div", { class: "cap" }, r.description) : null));
  }
  return { shown: rows.length, el: grid };
}

function renderImages() {
  const f = state.route.f;
  const rows = state.rows.filter((r) => matches(r, ""));
  const grid = h("div", { class: "grid" });
  for (const r of rows) {
    const fr = r.frames.filter(Boolean);
    const pic = fr[0];
    grid.append(h("div", { class: "card", "data-i": r.index, onclick: () => go(f, state.route.s, r.index) },
      h("div", { class: "thumb" }, pic ? h("img", { src: imgUrl(f, "image", r.index), loading: "lazy", alt: "" }) : h("small", {}, "no picture")),
      h("div", { class: "cap" }, h("b", {}, hex4(r.index)), `${r.width}×${r.height}`,
        fr.length > 1 ? h("span", { class: "badge" }, `×${fr.length}`) : null,
        pic && (pic.width !== r.width || pic.height !== r.height) ? h("span", { class: "badge", title: "texture size differs from display size" }, `${pic.width}×${pic.height}`) : null)));
  }
  return { shown: rows.length, el: grid };
}

function renderTextures() {
  const f = state.route.f;
  const rows = state.rows.filter((r) => matches(r, r.format));
  const grid = h("div", { class: "grid" });
  for (const r of rows) {
    grid.append(h("div", { class: "card", "data-i": r.index, onclick: () => go(f, state.route.s, r.index) },
      h("div", { class: "thumb" }, h("img", { src: imgUrl(f, "texture", r.index), loading: "lazy", alt: "" })),
      h("div", { class: "cap" }, h("b", {}, hex4(r.index)), `${r.width}×${r.height}`, h("span", { class: "badge" }, r.format),
        r.users.length !== 1 ? h("span", { class: "badge", title: "images using this texture" }, `${r.users.length} use`) : null)));
  }
  return { shown: rows.length, el: grid };
}

function renderFonts() {
  const f = state.route.f;
  const rows = state.rows.filter((r) => matches(r, ""));
  const list = h("div");
  for (const r of rows) {
    list.append(h("div", { class: "card", "data-i": r.index, style: "text-align:left;margin-bottom:8px;cursor:default" },
      h("div", { class: "cap", style: "justify-content:flex-start" }, h("b", {}, hex4(r.index)),
        ...[`height ${r.char_height}px`, `${r.glyphs} glyphs`, `code points ${hex4(r.unicode_high_byte << 8)}…`,
            `canvas ${r.canvas_width}×${r.canvas_height}`].map((t) => h("span", {}, t))),
      h("div", { class: "thumb big", style: "justify-content:flex-start;overflow:auto" }, h("img", { src: imgUrl(f, "font", r.index), alt: "", style: "max-height:none" }))));
  }
  return { shown: rows.length, el: list };
}

function summary(fields) {
  const out = [];
  for (const [k, v] of Object.entries(fields)) {
    if (k === "index" || k === "size" || k.startsWith("_")) continue;
    if (typeof v === "number" || typeof v === "string") out.push(`${k}=${typeof v === "string" ? JSON.stringify(v) : v}`);
    else if (v && v.module) out.push(`${k}=${v.module}:${v.index}`);
    else if (Array.isArray(v)) out.push(`${k}[${v.length}]`);
    else if (v && v.hex) out.push(`${k}=#${v.hex}`);
    if (out.length >= 8) break;
  }
  return out.join("  ");
}

function renderOther() {
  const rows = state.rows.filter((r) => matches(r, summary(r.fields)));
  const tb = h("tbody");
  for (const r of rows) {
    const pics = h("td", { class: "pics" });
    for (const p of r.pictures || []) {
      pics.append(h("span", { class: "thumb mini", title: `${p.field}: ${p.module} ${hex4(p.index)} ${p.width}×${p.height}${p.count > 1 ? ` (first of ${p.count})` : ""}`,
        onclick: (e) => { e.stopPropagation(); go(p.file, "IMAG2000", p.index); } },
        h("img", { src: p.url + "&v=" + (state.rev[p.file] || 0), loading: "lazy", alt: "" })));
    }
    tb.append(h("tr", { "data-i": r.index, onclick: () => go(state.route.f, state.route.s, r.index) },
      h("td", { class: "idx" }, hex4(r.index)), pics,
      h("td", { class: "mono" }, summary(r.fields), r.problems.length ? h("div", { class: "problem" }, r.problems[0]) : null)));
  }
  return { shown: rows.length, el: h("table", { class: "items" }, h("thead", {}, h("tr", {}, h("th", {}, "index"), h("th", {}, "images"), h("th", {}, "fields"))), tb) };
}

// ---------------------------------------------------------------------------
// screens of the unit (GraphicBase.RNEG): which widgets form each screen
function renderScreens(keepFocus) {
  const main = $("#main");
  const focused = keepFocus && document.activeElement && document.activeElement.tagName === "INPUT";
  const caret = focused ? document.activeElement.selectionStart : 0;
  main.innerHTML = "";
  const q = state.filter.toLowerCase();
  const rows = state.rows.filter((r) => !q || String(r.id) === q || (r.name || "").toLowerCase().includes(q) || (r.module || "").toLowerCase().includes(q) || (r.class || "").toLowerCase().includes(q));
  const tb = h("tbody");
  for (const r of rows) {
    tb.append(h("tr", { "data-i": r.id, onclick: () => go(null, "screens", r.id) },
      h("td", { class: "idx" }, String(r.id)),
      h("td", {}, r.name ? h("b", {}, r.name) : h("span", { class: "muted" }, "(no name found)"), r.class && r.class !== "C_" + r.name && r.class !== "C_Menu_" + r.name ? h("small", { class: "muted mono" }, " " + r.class) : null),
      h("td", { class: "mono" }, r.module || ""),
      h("td", { class: "idx" }, String(r.count)),
      h("td", { class: "mono muted", style: "font-size:12px" }, r.sections.map((x) => (x.startsWith("type") ? "?" : x.slice(0, 4))).join(" "))));
  }
  const info = state.settings && state.settings.screens;
  const head = viewHead("Screens", info ? info.path.split("/").slice(-3).join("/") : "", null);
  main.append(head, h("p", { class: "muted", style: "margin:-6px 0 10px" },
    "Every screen the unit's software can show, with the GEN_common widgets it is built from (names come from the firmware modules; the unit fills in texts and pictures at runtime)."),
    h("table", { class: "items" }, h("thead", {}, h("tr", {}, h("th", {}, "id"), h("th", {}, "screen"), h("th", {}, "module"), h("th", {}, "widgets"), h("th", {}, "kinds"))), tb));
  $(".count", head).textContent = `${rows.length} of ${state.rows.length}`;
  if (focused) { const inp = $(".view-head input"); inp.focus(); inp.setSelectionRange(caret, caret); }
  highlightActive();
}

const OBJ_KIND = { BKGD: "background", FXPI: "fixed picture", VRPI: "variable picture", FXTX: "fixed text", ITXT: "indexed text", DTXT: "dynamic text",
  FBTN: "button", CBTN: "check button", DBTN: "dynamic button", DCBT: "dynamic check button", ICBT: "indexed check button", MENU: "menu", IMEN: "indexed menu",
  LIST: "list", MCTX: "multiple choice", EDTX: "edited text", EDTB: "keyboard", VWTX: "view text", FBAR: "bar", SEBR: "selectable bar", CRZO: "crossroad zoom" };

async function detailScreen(d, id) {
  let sc;
  try { sc = await act(() => api("GET", `/api/screens/${id}`)); }
  catch (e) { d.append(h("p", { class: "problem" }, e.message)); return; }
  const close = h("button", { class: "small", style: "float:right", onclick: () => go(null, "screens") }, "✕");
  d.append(close, h("h2", {}, sc.name || `screen ${sc.id}`),
    h("p", { class: "muted mono", style: "font-size:12px;margin:0 0 8px" }, `screen ${sc.id}`, sc.module ? ` · module ${sc.module}` : "", sc.class ? ` · ${sc.class}` : "", ` · flags 0x${sc.flags.toString(16)}`));
  // one stage with every widget at its own position
  const items = [];
  const drawn = sc.objects.filter((o) => o.fields);
  for (const o of drawn) {
    const its = stageItems(o.section, o.fields, o.resolved || {});
    // menus and indexed texts know their lines/texts only through the screen's list: use them
    if (o.list_texts && o.list_texts.length) {
      const texts = its.filter((it) => it.type === "text");
      texts.forEach((it, n) => { const t = o.list_texts[n] != null ? o.list_texts[n] : o.list_texts[0]; if (t != null && it.placeholder) { it.text = t; it.placeholder = false; } });
    }
    for (const it of its) { it.obj = o.obj; it.label = `${o.section.slice(0, 4)} ${hex4(o.index)} ${it.label}`; items.push(it); }
  }
  d.append(screenStage(items, sc));
  // the widgets
  const tb = h("tbody");
  for (const o of sc.objects) {
    const sec = o.section ? o.section.slice(0, 4) : null;
    const fl = o.fields || {};
    const texts = (o.list_texts || []).filter((t) => t != null);
    const res = o.resolved || {};
    const own = res.text_index && res.text_index.texts ? res.text_index.texts.filter(Boolean) : [];
    const what = o.section ? h("span", { class: "link", onclick: () => go(o.file, o.section, o.index) }, `${sec} ${hex4(o.index)}`) : h("span", { class: "muted" }, `type ${o.type}`);
    tb.append(h("tr", { "data-obj": o.obj, onmouseenter: () => hilite(d, o.obj, true), onmouseleave: () => hilite(d, o.obj, false) },
      h("td", { class: "mono" }, what, o.missing ? h("span", { class: "problem" }, " empty slot") : null),
      h("td", {}, (sec && OBJ_KIND[sec]) || o.kind || "", o.probable ? h("small", { class: "muted" }, " (section guessed)") : null, o.fixed ? h("small", { class: "muted" }, " · fixed part") : null),
      h("td", { class: "idx" }, fl.x != null ? `${fl.x},${fl.y}` : ""),
      h("td", { class: "idx" }, o.fixed ? "" : String(o.obj)),
      h("td", { style: "font-size:12.5px" }, texts.length ? h("span", { title: o.list }, texts.slice(0, 8).map((t) => String(t).replace(/\r/g, " ")).join(" · "), texts.length > 8 ? ` … (${texts.length})` : "")
        : own.length ? h("span", {}, own.join(" / ")) : o.list ? h("small", { class: "muted mono" }, o.list.replace(/^List_GDO_/, "")) : "")));
  }
  d.append(h("h3", {}, `${sc.objects.length} widgets`),
    h("table", { class: "items screen-objects" }, h("thead", {}, h("tr", {}, h("th", {}, "widget"), h("th", {}, "kind"), h("th", {}, "x,y"), h("th", {}, "object id"), h("th", {}, "texts / list"))), tb),
    h("p", { class: "muted", style: "font-size:12px" }, "Click a widget to edit it; hover a row to find it on the screen. Which items a list or menu really shows depends on the unit (the lists above are what the firmware attaches to this screen)."));
}

function hilite(root, obj, on) {
  for (const el of root.querySelectorAll(`.stage [data-obj="${obj}"]`)) el.classList.toggle("hl", on);
}

function screenStage(items, sc) {
  const scr = (state.settings && state.settings.screen) || { width: 800, height: 480 };
  const bg = state.settings && state.settings.background;
  const wrap = h("div", { class: "stage-wrap" });
  const stage = h("div", { class: "stage", style: `width:${scr.width}px;height:${scr.height}px` });
  const showBg = h("input", { type: "checkbox", checked: state.stageBg !== false, title: "the theme's first background under everything" });
  const draw = () => {
    stage.innerHTML = "";
    const hasOwnBg = items.some((it) => it.type === "img" && it.x === 0 && it.y === 0 && it.w >= scr.width && it.h >= scr.height);
    if (showBg.checked && bg && !hasOwnBg) stage.append(h("img", { class: "stage-bg", src: bg.url + "&v=" + (state.rev[bg.file] || 0), alt: "", style: `width:${bg.width}px;height:${bg.height}px` }));
    paintItems(stage, items, 0, 0, bg ? state.rev[bg.file] || 0 : 0);
  };
  showBg.onchange = () => { state.stageBg = showBg.checked; draw(); };
  draw();
  wrap.append(stage);
  let scale = 1;
  const fit = () => { scale = Math.min(1, (wrap.clientWidth || 340) / scr.width); stage.style.transform = `scale(${scale})`; wrap.style.height = Math.round(scr.height * scale) + "px"; };
  requestAnimationFrame(fit);
  window.addEventListener("resize", fit);
  const big = h("button", { class: "small", onclick: () => { const clone = stage.cloneNode(true); clone.style.transform = ""; modal(h("div", {}, h("h2", {}, `${sc.name || "screen " + sc.id} (1:1)`), h("div", { class: "stage-wrap big" }, clone), h("div", { class: "modal-actions" }, h("button", { onclick: closeModal }, "Close")))); } }, "1:1");
  const sample = items.some((it) => it.type === "text" && it.placeholder)
    ? h("input", { type: "text", value: state.sampleText || SAMPLE_TEXT, title: "sample text for texts the unit fills in at runtime", style: "width:150px",
        oninput: (e) => { state.sampleText = e.target.value; draw(); } }) : null;
  return h("div", {}, h("div", { class: "row", style: "justify-content:space-between" },
    h("label", {}, h("span", {}, showBg, " theme background")), sample, big), wrap);
}

// ---------------------------------------------------------------------------
// detail pane
function hideDetail() { const d = $("#detail"); d.hidden = true; d.innerHTML = ""; }

async function showDetail() {
  const { f, s, i } = state.route;
  const d = $("#detail");
  if (s === "screens") { d.hidden = false; d.innerHTML = ""; return detailScreen(d, i); }
  const file = fileById(f);
  if (!file) return;
  const sec = file.sections.find((x) => x.name === s);
  d.hidden = false;
  d.innerHTML = "";
  const close = h("button", { class: "small", style: "float:right", onclick: () => go(f, s) }, "✕");
  d.append(close, h("h2", {}, `${s} [${hex4(i)}]`));
  try {
    if (sec.group === "texts") return detailText(d, f, i);
    if (sec.group === "colors") return detailColor(d, f, i);
    const item = await act(() => api("GET", `/api/files/${f}/item/${s}/${i}`));
    if (sec.group === "images") return detailImage(d, f, i, item);
    if (sec.group === "textures") return detailTexture(d, f, i, item);
    return detailOther(d, f, s, i, item);
  } catch (e) { d.append(h("p", { class: "problem" }, e.message)); }
}

function detailText(d, f, i) {
  const row = state.rows.find((r) => r.index === i);
  if (!row) return;
  const ta = h("textarea", { rows: 4 }, row.text.replace(/\r/g, "\n"));
  const save = async () => {
    const r = await act(() => api("POST", `/api/files/${f}/text/${i}`, { text: ta.value }));
    row.text = r.text;
    updateFile(r.file);
    renderSection();
    toast(`Text ${hex4(i)} saved in memory`);
  };
  ta.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") save(); });
  d.append(h("p", { class: "muted" }, "Enter inserts a line break (stored as U+000D, as the unit expects). Ctrl/Cmd+Enter applies."),
    ta, h("div", { class: "row" }, h("button", { class: "primary", onclick: save }, "Apply"),
      h("button", { onclick: () => { ta.value = row.text.replace(/\r/g, "\n"); } }, "Reset")));
  ta.focus();
}

function detailColor(d, f, i) {
  const row = state.rows.find((r) => r.index === i);
  if (!row) return;
  const rgb = "#" + row.hex.slice(0, 6);
  const alpha = parseInt(row.hex.slice(6, 8), 16);
  const sw = h("div", { class: "swatch" }, h("span", { style: `background:#${row.hex}` }));
  const picker = h("input", { type: "color", value: rgb });
  const hexIn = h("input", { type: "text", class: "mono", value: "#" + row.hex, size: 10 });
  const a = h("input", { type: "range", min: 0, max: 255, value: alpha });
  const aNum = h("input", { type: "number", min: 0, max: 255, value: alpha });
  const sync = (from) => {
    if (from === "hex") {
      const m = /^#?([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(hexIn.value.trim());
      if (!m) return;
      picker.value = "#" + m[1];
      if (m[2]) { a.value = aNum.value = parseInt(m[2], 16); }
    }
    if (from === "a") aNum.value = a.value; else if (from === "an") a.value = aNum.value;
    const hx = picker.value.slice(1) + (+a.value).toString(16).padStart(2, "0");
    if (from !== "hex") hexIn.value = "#" + hx;
    $("span", sw).style.background = "#" + hx;
  };
  picker.oninput = () => sync("picker"); a.oninput = () => sync("a"); aNum.oninput = () => sync("an"); hexIn.oninput = () => sync("hex");
  const apply = async () => {
    const r = await act(() => api("POST", `/api/files/${f}/color/${i}`, { color: hexIn.value.trim() }));
    row.hex = r.hex;
    updateFile(r.file);
    renderSection();
    toast(`Colour ${hex4(i)} set to #${r.hex}`);
  };
  d.append(row.description ? h("p", {}, row.description) : null, sw,
    h("div", { class: "row" }, h("label", {}, "colour"), picker, hexIn),
    h("div", { class: "row" }, h("label", {}, "alpha"), a, aNum),
    h("div", { class: "row" }, h("button", { class: "primary", onclick: apply }, "Apply")));
}

const FORMATS = ["BMP8.4", "BMP8.3", "DXT1", "DXT1A", "DXT3", "DXT5"];

function formatSelect(keepLabel) {
  const sel = h("select", {}, h("option", { value: "" }, keepLabel));
  for (const fm of FORMATS) sel.append(h("option", { value: fm }, fm));
  return sel;
}

function readFiles(input) {
  return Promise.all([...input.files].map((file) => new Promise((res, rej) => {
    const rd = new FileReader();
    rd.onload = () => res(rd.result.split(",", 2)[1]);
    rd.onerror = () => rej(new Error("cannot read " + file.name));
    rd.readAsDataURL(file);
  })));
}

function previewBlock(fileInput, formatSel) {
  const box = h("div");
  const btn = h("button", { onclick: async () => {
    const data = await readFiles(fileInput);
    if (!data.length) return toast("Choose an image first", "warn");
    const r = await act(() => api("POST", "/api/preview", { data_b64: data[0], format: formatSel.value || formatSel.dataset.current || "BMP8.4" }));
    box.innerHTML = "";
    box.append(h("div", { class: "preview-box" }, h("img", { src: "data:image/png;base64," + r.png_b64, alt: "" })),
      h("p", { class: "muted" }, `${r.format} ${r.width}×${r.height}, ${r.bytes} bytes, PSNR `, h("span", { class: "psnr" }, r.psnr >= 99 ? "lossless" : r.psnr + " dB")));
  } }, "Preview quality");
  return { box, btn };
}

function detailImage(d, f, i, item) {
  const fl = item.fields;
  const pics = (fl.pictures || []).filter(Boolean);
  const tex = (idx) => h("span", { class: "link", onclick: () => go(f, "TXUR2008", idx) }, "TXUR " + hex4(idx));
  d.append(h("div", { class: "thumb big" }, pics.length ? h("img", { src: imgUrl(f, pics.length > 1 ? "anim" : "image", i), alt: "" }) : h("small", {}, "no picture")));
  const frames = h("div", { class: "frames" });
  pics.forEach((p, n) => frames.append(h("div", {},
    h("div", { class: "thumb" }, h("img", { src: imgUrl(f, "image", i, "&frame=" + n), alt: "" })),
    h("div", { class: "frame-cap" }, `${p.width_minus_1 + 1}×${p.height_minus_1 + 1}`, h("br"), tex(p.texture)))));
  if (pics.length > 1) d.append(h("h3", {}, `${pics.length} frames`), frames);
  else if (pics.length === 1) d.append(h("p", { class: "muted" }, `picture ${pics[0].width_minus_1 + 1}×${pics[0].height_minus_1 + 1} in `, tex(pics[0].texture)));

  // properties form
  const W = h("input", { type: "number", value: fl.width }), H = h("input", { type: "number", value: fl.height });
  const T = h("input", { type: "number", value: fl.time, min: 0 });
  const FL = h("select", {}, ...[0, 1, 2, 3].map((v) => h("option", { value: v, selected: v === fl.flag }, `${v} ${["none", "loop", "autostart", "loop+autostart"][v]}`)));
  const ST = (v) => h("select", {}, ...[[0, "direct"], [1, "third"], [4, "bilinear"]].map(([k, n]) => h("option", { value: k, selected: k === v }, `${k} ${n}`)));
  const HS = ST(fl.h_stretch), VS = ST(fl.v_stretch);
  const applyProps = async () => {
    const changes = { width: +W.value, height: +H.value, time: +T.value, flag: +FL.value, h_stretch: +HS.value, v_stretch: +VS.value };
    const r = await act(() => api("PUT", `/api/files/${f}/item/IMAG2000/${i}`, { changes }));
    updateFile(r.file); await loadSection(); showDetail(); toast(`Image ${hex4(i)} updated`);
  };
  d.append(h("h3", {}, "Properties"),
    h("div", { class: "row" }, h("label", {}, "display size"), W, "×", H),
    h("div", { class: "row" }, h("label", {}, "frame time"), T, h("small", {}, "ms")),
    h("div", { class: "row" }, h("label", {}, "animation"), FL),
    h("div", { class: "row" }, h("label", {}, "stretch h / v"), HS, VS),
    h("div", { class: "row" }, h("button", { onclick: applyProps }, "Apply properties")));

  // replace
  const inp = h("input", { type: "file", accept: "image/*", multiple: true });
  const fmt = formatSelect("keep current format");
  const disp = h("select", {}, h("option", { value: "auto" }, "automatic"), h("option", { value: "image" }, "follow the image"),
    h("option", { value: "keep" }, "keep current"));
  const share = h("input", { type: "checkbox" });
  const pv = previewBlock(inp, fmt);
  const apply = async () => {
    const frames = await readFiles(inp);
    if (!frames.length) return toast("Choose one or more image files", "warn");
    const body = { frames, format: fmt.value || null, share: share.checked };
    if (disp.value === "keep") body.display = [fl.width, fl.height];
    if (disp.value === "image") body.display = "image";
    const r = await act(() => api("POST", `/api/files/${f}/image/${i}`, body));
    updateFile(r.file); await loadSection(); showDetail();
    const nw = r.result.new_slots.length ? `, new texture slot${r.result.new_slots.length > 1 ? "s" : ""} ${r.result.new_slots.map(hex4).join(", ")}` : "";
    toast(`Image ${hex4(i)} replaced: ${frames.length} frame${frames.length > 1 ? "s" : ""}, display ${r.result.display.join("×")}${nw}`);
  };
  d.append(h("h3", {}, "Replace picture(s)"),
    h("p", { class: "muted" }, "Several files or an animated GIF become an animation. S3TC formats are padded to multiples of 4 internally."),
    h("div", { class: "row" }, inp),
    h("div", { class: "row" }, h("label", {}, "format"), fmt),
    h("div", { class: "row" }, h("label", {}, "display size"), disp),
    h("div", { class: "row" }, h("label", {}, h("span", {}, share, " overwrite shared textures")),
      h("small", {}, "(otherwise a new slot is used when another image shares the texture)")),
    h("div", { class: "row" }, pv.btn, h("button", { class: "primary", onclick: apply }, "Replace")), pv.box);
  if (item.used_by && item.used_by.length) {
    d.append(h("h3", {}, `Used by (${item.used_by.length})`), h("ul", { class: "plain mono" }, ...item.used_by.slice(0, 40).map((u) => h("li", {}, u))));
  }
  if (item.problems.length) d.append(h("p", { class: "problem" }, item.problems.join("; ")));
  d.append(rawJson(f, "IMAG2000", i, item));
}

function detailTexture(d, f, i, item) {
  const fl = item.fields;
  const name = item.format;
  d.append(h("div", { class: "thumb big" }, h("img", { src: imgUrl(f, "texture", i), alt: "" })),
    h("dl", { class: "kv" }, h("dt", {}, "format"), h("dd", {}, name), h("dt", {}, "size"), h("dd", {}, `${fl.width}×${fl.height}, ${fl.size} bytes`),
      h("dt", {}, "flags"), h("dd", {}, "0x" + fl.flags.toString(16).padStart(8, "0"))));
  d.append(h("h3", {}, `Used by ${item.users.length} image${item.users.length === 1 ? "" : "s"}`),
    h("p", {}, ...item.users.map((u) => h("span", { class: "link", style: "margin-right:8px", onclick: () => go(f, "IMAG2000", u) }, "IMAG " + hex4(u)))));
  const inp = h("input", { type: "file", accept: "image/*" });
  const fmt = formatSelect("keep " + name); fmt.dataset.current = name;
  const keep = h("input", { type: "checkbox" });
  const pv = previewBlock(inp, fmt);
  const apply = async () => {
    const data = await readFiles(inp);
    if (!data.length) return toast("Choose an image file", "warn");
    const r = await act(() => api("POST", `/api/files/${f}/texture/${i}`, { data_b64: data[0], format: fmt.value || null, keep_pictures: keep.checked }));
    updateFile(r.file); await loadSection(); showDetail(); toast(`Texture ${hex4(i)} replaced`);
  };
  d.append(h("h3", {}, "Replace texture"),
    h("div", { class: "row" }, inp), h("div", { class: "row" }, h("label", {}, "format"), fmt),
    h("div", { class: "row" }, h("label", {}, h("span", {}, keep, " keep picture sizes")), h("small", {}, "(normally every image using this slot follows the new size)")),
    h("div", { class: "row" }, pv.btn, h("button", { class: "primary", onclick: apply }, "Replace")), pv.box);
  d.append(rawJson(f, "TXUR2008", i, item));
}

function detailOther(d, f, s, i, item) {
  const fl = item.fields, r = item.resolved || {};
  if (STAGED.has(s.slice(0, 4))) d.append(stageBlock(f, s, i, fl, r));
  const geo = ["level", "x", "y", "text_width", "text_dx", "text_dy", "visible_width", "visible_height", "width", "height", "style", "alignment", "text_hor_alignment", "text_ver_alignment", "interline", "time", "blinking_period"]
    .filter((k) => fl[k] != null).map((k) => `${k}=${fl[k]}`).join("  ");
  if (geo) d.append(h("p", { class: "mono muted", style: "font-size:12px" }, geo));
  if (Object.keys(r).length) d.append(h("h3", {}, "References"), resolvedBlock(r));
  if (item.screens && item.screens.length) {
    d.append(h("h3", {}, `On ${item.screens.length} screen${item.screens.length === 1 ? "" : "s"}`),
      h("p", { style: "font-size:12.5px" }, ...item.screens.slice(0, 60).map((sc) => h("span", { class: "link", style: "margin-right:10px;white-space:nowrap", onclick: () => go(null, "screens", sc.id) }, sc.name || `screen ${sc.id}`)),
        item.screens.length > 60 ? h("span", { class: "muted" }, `… and ${item.screens.length - 60} more`) : null));
  } else if (state.settings && state.settings.screens && STAGED.has(s.slice(0, 4))) d.append(h("p", { class: "muted", style: "font-size:12px" }, "Not placed on any screen by the firmware's screen database."));
  if (item.problems.length) d.append(h("p", { class: "problem" }, item.problems.join("; ")));
  d.append(rawJson(f, s, i, item, false));
}

// ---------------------------------------------------------------------------
// resolved references (links into the theme, font, common and language files)
function refView(res) {
  if (!res) return h("span", { class: "muted" }, "–");
  const jump = (file, sec, idx) => () => go(file, sec, idx);
  if (res.kind === "missing") return h("span", { class: "muted" }, `${res.module}:${res.index} (${res.what}: file not open)`);
  if (res.kind === "image") {
    return h("span", { class: "ref ref-img", onclick: jump(res.file, "IMAG2000", res.index), title: "open image" },
      h("span", { class: "thumb mini" }, h("img", { src: res.url + "&v=" + (state.rev[res.file] || 0), alt: "" })),
      h("span", {}, h("b", {}, `${res.module} ${hex4(res.index)}`), ` ${res.width}×${res.height}`, res.frames > 1 ? h("span", { class: "badge" }, `×${res.frames}`) : null));
  }
  if (res.kind === "color") {
    return h("span", { class: "ref", onclick: jump(res.file, "TXCO2000", res.index), title: "open colour" },
      h("span", { class: "swatch inline" }, h("span", { style: `background:#${res.hex}` })),
      h("b", {}, `${res.module} ${hex4(res.index)}`), ` #${res.hex}`, res.description ? h("small", {}, ` ${res.description}`) : null);
  }
  if (res.kind === "font") {
    return h("span", { class: "ref", onclick: jump(res.file, "FONT2007", res.index), title: "open font table" },
      h("b", {}, `${res.module} ${hex4(res.index)}`), ` scale ${res.scale}`, res.height ? `, ${res.height} px` : "", res.style ? `, style ${res.style}` : "");
  }
  if (res.kind === "text_index") {
    const el = h("span", { class: "ref" }, h("b", { onclick: jump(res.file, "TXIN2000", res.index), title: "open text index" }, `${res.module} ${hex4(res.index)}`), " ");
    res.slots.forEach((slot, n) => {
      if (n) el.append(h("span", { class: "muted" }, " / "));
      const t = res.texts[n];
      el.append(h("span", { class: "text-ref", title: `language text ${hex4(slot)}`, onclick: res.language_file != null ? jump(res.language_file, "TEXT2006", slot) : null },
        t == null ? h("span", { class: "muted" }, `<${hex4(slot)}>`) : showText(t)));
    });
    if (res.unit_dependance) el.append(h("small", { class: "muted" }, " (unit dependent)"));
    return el;
  }
  if (res.kind === "text") {
    return h("span", { class: "ref", onclick: res.file != null ? jump(res.file, "TEXT2006", res.index) : null },
      h("b", {}, hex4(res.index)), " ", res.text == null ? h("span", { class: "muted" }, "(not in this language file)") : showText(res.text));
  }
  return h("span", {}, JSON.stringify(res));
}

function resolvedBlock(resolved) {
  const dl = h("dl", { class: "kv refs" });
  for (const [k, v] of Object.entries(resolved)) {
    dl.append(h("dt", {}, k));
    if (Array.isArray(v)) {
      const ul = h("ol", { class: "reflist", start: 0 });
      v.forEach((x) => ul.append(h("li", {}, refView(x))));
      dl.append(h("dd", {}, v.length ? ul : h("span", { class: "muted" }, "empty")));
    } else dl.append(h("dd", {}, refView(v)));
  }
  return dl;
}

// ---------------------------------------------------------------------------
// stage: the widget drawn at its position on the 800x480 screen
const SAMPLE_TEXT = "Beispiel 12:34";
const STAGED = new Set(["BKGD", "FXPI", "VRPI", "DYPI", "ITXT", "DTXT", "FXTX", "FBTN", "CBTN", "DBTN", "DCBT", "ICBT", "MCTX", "EDTX", "MENU", "IMEN", "LIST", "EDTB", "VWTX", "FBAR", "SEBR", "CRZO", "ACLK"]);

function stageItems(section, f, r, stateNo = 0) {
  const kind = section.slice(0, 4);
  const items = [];
  const first = (v) => (Array.isArray(v) ? (v[stateNo] || v.find(Boolean)) : v) || null;
  const img = (res, x, y, label) => { if (res && res.kind === "image") items.push({ type: "img", x, y, w: res.width, h: res.height, url: res.url, label: label || `${res.module} ${hex4(res.index)}` }); return res && res.kind === "image" ? res : null; };
  const box = (x, y, w, h, label) => items.push({ type: "box", x, y, w: w || 48, h: h || 24, label });
  const fontH = (res) => (res && res.kind === "font" && res.height) ? Math.max(8, Math.round(res.height * (res.scale || 1000) / 1000)) : 20;
  const colorOf = (res) => (res && res.kind === "color" ? "#" + res.hex.slice(0, 6) : "#ffffff");
  const textOf = (res) => (res && res.kind === "text_index" && res.texts.find((t) => t != null)) || null;
  const text = (x, y, w, res, color, font, label, align) => items.push({ type: "text", x, y, w: w || 120, h: fontH(font), text: textOf(res) || label, color: colorOf(color),
    hex: color && color.kind === "color" ? color.hex : "ffffffff", font: font && font.kind === "font" ? font.index : null,
    interline: f.interline || 0, align: align != null ? align : (f.text_hor_alignment != null ? f.text_hor_alignment : 0), placeholder: !textOf(res), label });
  // buttons and menus: text_dx is an anchor inside the image. alignment 0 = centred on it (factory
  // files have text_dx = image width / 2), 1 = starts there (text_dx 7..10, 45 after a check box), 2 = ends there (assumed)
  const anchored = (ax, ay, w, res, color, font, label, align) => {
    const bw = w || 200;
    const left = align === 0 ? ax - Math.round(bw / 2) : align === 2 ? ax - bw : ax;
    text(left, ay, bw, res, color, font, label, align === 0 ? 1 : align === 2 ? 2 : 0);
  };
  const x = f.x || 0, y = f.y || 0;
  switch (kind) {
    case "BKGD": case "FXPI": img(r.image, x, y); break;
    case "VRPI": box(x, y, 48, 48, "variable picture"); break;
    case "DYPI": box(0, 0, f.width, f.height, "dynamic picture"); img(r.image, 0, 0); break;
    case "ITXT": case "DTXT": case "FXTX":
      // the background sits at x - offset: DTXT 002C has a 536x48 background with offset (8,10) around a 512 wide
      // text, ITXT 003E a 280 wide one with offset 176 at x=696, which ends exactly at the screen edge
      if (f.has_background || r.background) img(r.background, x - (f.bg_h_offset || 0), y - (f.bg_v_offset || 0), "background (x/y minus bg offset)");
      text(x, y, f.text_width, r.text_index, r.text_color, r.font, kind === "ITXT" ? "indexed text" : kind === "DTXT" ? "dynamic text" : "fixed text");
      break;
    case "FBTN": case "CBTN": case "DBTN": case "DCBT": case "ICBT": case "MCTX": case "EDTX": {
      const im = img(first(r.images), x, y, `state ${stateNo} image`);
      if (kind === "DBTN") img(r.arrow_image, x + (f.arrow_dx || 0), y + (f.arrow_dy || 0), "arrow");
      const col = first(r.text_colors) || first(r.not_selected_text_colors);
      anchored(x + (f.text_dx || 0), y + (f.text_dy || 0), f.text_width || (im ? im.width : 0), r.text_index || first(r.text_indexes), col, r.font, kind.toLowerCase() + " text", f.alignment || 0);
      if (kind === "EDTX") img(first(r.backgrounds), x, y, "background");
      break;
    }
    case "MENU": case "IMEN": {
      img(r.image, x, y, "menu image");
      img(r.cursor_image, x, y, "cursor image");
      const fh = fontH(r.font), col = first(r.text_colors);
      const texts = (r.texts || []).length ? r.texts : [null];
      texts.forEach((t, n) => anchored(x + (f.text_dx || 0), y + (f.text_dy || 0) + n * Math.round(fh * 1.4), f.text_width || 300, t, col, r.font, `item ${n}`, f.text_alignment || 0));
      break;
    }
    case "LIST":
      img(r.background, x, y, "background");
      box(x + (f.left_margin || 0), y + (f.top_margin || 0), f.visible_width, f.visible_height, "visible area");
      img(r.scrollbar_bg, x + (f.scrollbar_dx || 0), y + (f.scrollbar_dy || 0), "scrollbar background");
      img(r.scrollbar, x + (f.scrollbar_dx || 0), y + (f.scrollbar_dy || 0), "scrollbar");
      img(r.cursor, x + (f.left_margin || 0), y + (f.top_margin || 0), "cursor");
      break;
    case "EDTB": {
      const imgs = (r.images || []).filter(Boolean), col = r.not_selected_color;
      (f.key_dx || []).forEach((dx, n) => {
        const im = img(imgs[n] || imgs[0], x + dx, y + (f.key_dy || [])[n], `key ${n}`);
        text(x + dx, y + (f.key_dy || [])[n], im ? im.width : 40, (r.texts || [])[n], col, r.font, `key ${n}`);
      });
      break;
    }
    case "VWTX": img(first(r.backgrounds), x, y, "background"); box(x + (f.left_margin || 0), y + (f.top_margin || 0), f.visible_width, f.visible_height, "visible area"); img(first(r.scrollbar_bgs), x + (f.scrollbar_dx || 0), y + (f.scrollbar_dy || 0), "scrollbar background"); break;
    case "FBAR": img(r.background, x, y, "background"); img(r.foreground, x, y, "foreground"); img(r.cursor_bg_image, x, y, "cursor background"); img(r.cursor_pointer_image, x, y, "cursor"); break;
    case "SEBR": img(first(r.backgrounds), x, y, "background"); img(first(r.foregrounds), x, y, "foreground"); img(first(r.cursor_backgrounds), x, y, "cursor background"); img(first(r.cursor_images), x, y, "cursor"); break;
    case "CRZO": img(r.bg_image, x, y, "background"); box(x, y, f.zoom_width, f.zoom_height, "zoom window"); break;
    case "ACLK": img(r.background_image, x, y, "background"); img(r.center_image, x, y, "centre"); break;
    default:
      for (const [k, v] of Object.entries(r)) { const res = first(v); if (res && res.kind === "image") img(res, x, y, k); }
  }
  return items;
}

/** Draw stage items (from stageItems) into a stage element, shifted by ox/oy screen pixels. */
function paintItems(stage, items, ox, oy, rev) {
  for (const it of items) {
    const st = `left:${it.x + ox}px;top:${it.y + oy}px;width:${it.w}px;height:${it.h}px`;
    const attrs = it.obj != null ? { "data-obj": it.obj } : {};
    if (it.type === "img") stage.append(h("img", { class: "stage-img", src: it.url + "&v=" + rev, alt: "", title: `${it.label} @ ${it.x},${it.y} ${it.w}×${it.h}`, style: st, ...attrs }));
    else if (it.type === "text") {
      const txt = it.placeholder ? (state.sampleText || SAMPLE_TEXT) : it.text;
      const just = ["flex-start", "center", "flex-end"][it.align] || "flex-start";
      const box = h("div", { class: "stage-text" + (it.placeholder ? " ph" : ""), title: `${it.label} @ ${it.x},${it.y} width ${it.w}${it.placeholder ? " (sample text: the real one is chosen by the unit)" : ""}`,
        style: `${st};justify-content:${just};color:${it.color};font-size:${Math.round(it.h * 0.78)}px;line-height:${it.h}px`, ...attrs });
      if (it.font != null) box.append(h("img", { class: "stage-font", alt: txt,
        src: `/api/render.png?font=${it.font}&text=${encodeURIComponent(txt)}&color=${it.hex}&interline=${it.interline}` }));
      else box.append(txt);
      stage.append(box);
    }
    else stage.append(h("div", { class: "stage-box", title: `${it.label} @ ${it.x},${it.y} ${it.w}×${it.h}`, style: st, ...attrs }, h("span", {}, it.label)));
  }
}

function stageBlock(fid, section, index, fields, resolved) {
  const scr = (state.settings && state.settings.screen) || { width: 800, height: 480 };
  const bg = state.settings && state.settings.background;
  const wrap = h("div", { class: "stage-wrap" });
  const stage = h("div", { class: "stage", style: `width:${scr.width}px;height:${scr.height}px` });
  const showBg = h("input", { type: "checkbox", checked: state.stageBg !== false });
  const states = Math.max((resolved.images || []).length, (resolved.text_colors || []).length);
  const stateSel = states > 1 ? h("select", { class: "small" }, ...Array.from({ length: states }, (_, n) => h("option", { value: n }, `state ${n}`))) : null;
  let items = stageItems(section, fields, resolved);
  let scale = 1, moved = null;  // moved = {dx, dy} in screen pixels while dragging / before apply
  const movable = fields.x != null && fields.y != null;
  const posX = h("input", { type: "number", min: 0, max: 65535, value: fields.x, disabled: !movable });
  const posY = h("input", { type: "number", min: 0, max: 65535, value: fields.y, disabled: !movable });
  const applyPos = async () => {
    const changes = { x: +posX.value, y: +posY.value };
    const r = await act(() => api("PUT", `/api/files/${fid}/item/${section}/${index}`, { changes }));
    updateFile(r.file); await loadSection(); showDetail(); toast(`${section} [${hex4(index)}] moved to ${changes.x},${changes.y}`);
  };
  const draw = () => {
    stage.innerHTML = "";
    if (moved) stage.style.cursor = "grabbing";
    if (showBg.checked && bg && !(section.startsWith("BKGD") && fields.x === 0 && fields.y === 0 && resolved.image && resolved.image.index === bg.index)) {
      stage.append(h("img", { class: "stage-bg", src: bg.url + "&v=" + (state.rev[bg.file] || 0), alt: "", style: `width:${bg.width}px;height:${bg.height}px` }));
    }
    paintItems(stage, items, moved ? moved.dx : 0, moved ? moved.dy : 0, state.rev[bg ? bg.file : 0] || 0);
  };
  showBg.onchange = () => { state.stageBg = showBg.checked; draw(); };
  if (stateSel) stateSel.onchange = () => { items = stageItems(section, fields, resolved, +stateSel.value); draw(); };
  // drag the whole widget: every drawn part follows x/y, so they move together
  if (movable) {
    stage.classList.add("movable");
    let start = null;
    stage.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      start = { x: e.clientX, y: e.clientY }; moved = { dx: 0, dy: 0 };
      stage.setPointerCapture(e.pointerId); e.preventDefault();
    });
    stage.addEventListener("pointermove", (e) => {
      if (!start) return;
      moved = { dx: Math.round((e.clientX - start.x) / scale), dy: Math.round((e.clientY - start.y) / scale) };
      posX.value = Math.max(0, fields.x + moved.dx); posY.value = Math.max(0, fields.y + moved.dy);
      draw();
    });
    stage.addEventListener("pointerup", async (e) => {
      if (!start) return;
      const m = moved; start = null; moved = null; stage.style.cursor = "";
      if (!m || (!m.dx && !m.dy)) { draw(); return; }
      await applyPos();
    });
  }
  draw();
  wrap.append(stage);
  const fit = () => { scale = Math.min(1, (wrap.clientWidth || 340) / scr.width); stage.style.transform = `scale(${scale})`; wrap.style.height = Math.round(scr.height * scale) + "px"; };
  requestAnimationFrame(fit);
  window.addEventListener("resize", fit);
  const big = h("button", { class: "small", onclick: () => { const clone = stage.cloneNode(true); clone.style.transform = ""; modal(h("div", {}, h("h2", {}, `${section} on screen (1:1)`), h("div", { class: "stage-wrap big" }, clone), h("div", { class: "modal-actions" }, h("button", { onclick: closeModal }, "Close")))); } }, "1:1");
  const sample = items.some((it) => it.type === "text" && it.placeholder)
    ? h("input", { type: "text", value: state.sampleText || SAMPLE_TEXT, title: "sample text for a text the unit fills in at runtime", style: "width:150px",
        oninput: (e) => { state.sampleText = e.target.value; draw(); } }) : null;
  return h("div", {}, h("div", { class: "row", style: "justify-content:space-between" },
    h("label", {}, h("span", {}, showBg, " screen background")), sample, stateSel, big), wrap,
    h("div", { class: "row" }, h("label", {}, "position"), posX, "×", posY,
      movable ? h("button", { class: "small", onclick: applyPos }, "Apply") : null,
      h("small", { class: "muted" }, movable ? "drag the widget on the screen, or type x/y" : "this item has no x/y")));
}

function rawJson(f, s, i, item, open) {
  const fields = Object.fromEntries(Object.entries(item.fields).filter(([k]) => !k.startsWith("_") && k !== "index" && k !== "size"));
  const ta = h("textarea", { rows: Math.min(28, JSON.stringify(fields, null, 1).split("\n").length + 1) }, JSON.stringify(fields, null, 1));
  const apply = async () => {
    let changes;
    try { changes = JSON.parse(ta.value); } catch (e) { return toast("Invalid JSON: " + e.message, "err"); }
    const r = await act(() => api("PUT", `/api/files/${f}/item/${s}/${i}`, { changes }));
    updateFile(r.file); await loadSection(); showDetail(); toast(`${s} [${hex4(i)}] updated`);
  };
  const det = h("details", { open: !!open }, h("summary", {}, "All fields (JSON)"),
    h("p", { class: "muted" }, "Links are {\"module\": \"GEN\", \"index\": n} or null; colours {\"hex\": \"rrggbbaa\"}; count fields follow the lists. Blobs shown as {\"bytes\": n} are kept as they are."),
    ta, h("div", { class: "row" }, h("button", { class: "primary", onclick: apply }, "Apply JSON")));
  return det;
}

// ---------------------------------------------------------------------------
// modals: open / save
function modal(content) { const m = $("#modal"); $(".modal-box", m).innerHTML = ""; $(".modal-box", m).append(content); m.hidden = false; }
function closeModal() { $("#modal").hidden = true; }

function uploadDialog() {
  const up = h("input", { type: "file", accept: ".gph,.bz2,.gz", multiple: true, onchange: () => uploadFiles(up.files) });
  modal(h("div", {},
    h("h2", {}, "Upload"),
    h("p", {}, "Pick the GPH files of your unit: they live in ", h("code", {}, "SDC/Data_Base/Graphics/RNEG"),
      " (", h("code", {}, "Common/GEN_common.gph.bz2"), ", ", h("code", {}, "Common/FNT_common.gph.bz2"), ", one language such as ",
      h("code", {}, "DU/GEN_DU.gph.bz2"), " and a theme folder like ", h("code", {}, "HARM00"), "). Upload them together and every link between them is resolved."),
    h("p", { class: "muted" }, "Optional: ", h("code", {}, "Data_Base/Graphics/GraphicBase.RNEG.gz"), " from the unit's software update adds the Screens view. ",
      "Files stay in your own session on the server for two hours of inactivity and are never written to its disk; use Download to get the edited package."),
    h("div", { class: "row" }, up),
    h("div", { class: "modal-actions" }, h("button", { onclick: closeModal }, "Cancel"))));
}

function downloadDialog(fid) {
  const f = fileById(fid);
  const ver = h("input", { type: "text", placeholder: "keep / now", size: 16, class: "mono" });
  const go = () => {
    const q = ver.value.trim() ? "?version=" + encodeURIComponent(ver.value.trim()) : "";
    window.location.href = `/api/files/${fid}/download.zip${q}`;
    closeModal();
    toast("Download started: a zip with the .gph.bz2 and its .inf");
    setTimeout(() => refreshFiles().catch(() => {}), 1500);
  };
  modal(h("div", {},
    h("h2", {}, "Download ", h("code", {}, f.label)),
    h("p", { class: "muted" }, "You get a zip with the compressed package and its .inf side-car (checksums included), ready for the ",
      h("code", {}, "Graphics/RNEG"), " folder of the unit."),
    h("div", { class: "row" }, h("label", {}, "VER"), ver, h("small", {}, "yy.mm.dd.hh.mm")),
    h("div", { class: "modal-actions" }, h("button", { onclick: closeModal }, "Cancel"), h("button", { class: "primary", onclick: go }, "Download"))));
}

async function openDialog(startPath) {
  if (isPublic()) return uploadDialog();
  const pathIn = h("input", { type: "text", placeholder: "/path/to/Graphics or a .gph.bz2 file" });
  const list = h("div", { class: "browse" });
  const openPath = async (p) => {
    const r = await act(() => api("POST", "/api/open", { path: p }));
    for (const f of r.opened) updateFile(f);
    closeModal();
    if (r.screens) { state.screens = null; await loadSettings(); renderSidebar(); toast(`Screen database loaded: ${r.screens.count} screens`); }
    if (r.opened.length) { toast(`Opened ${r.opened.length} file${r.opened.length > 1 ? "s" : ""}`); const f = r.opened[0]; go(f.id, f.sections.find((s) => s.used)?.name); }
    else if (r.already_open.length) { const f = r.already_open[0]; go(f.id, f.sections.find((s) => s.used)?.name); toast("Already open", "warn"); }
    else if (r.screens) go(null, "screens");
    else toast("No GPH files found there", "warn");
  };
  const browse = async (p) => {
    const r = await act(() => api("POST", "/api/browse", { path: p }));
    pathIn.value = r.path;
    list.innerHTML = "";
    if (r.parent) list.append(h("div", { class: "dir", onclick: () => browse(r.parent) }, ".."));
    for (const dname of r.dirs) list.append(h("div", { class: "dir", onclick: () => browse(r.path + "/" + dname) }, dname));
    for (const file of r.files) list.append(h("div", { class: "gph", onclick: () => openPath(r.path + "/" + file.name) }, file.name, h("small", {}, (file.size / 1024).toFixed(0) + " KB")));
    if (!r.dirs.length && !r.files.length) list.append(h("div", { class: "muted" }, "empty"));
  };
  pathIn.addEventListener("keydown", (e) => { if (e.key === "Enter") openPath(pathIn.value); });
  const up = h("input", { type: "file", accept: ".gph,.bz2", multiple: true, onchange: () => uploadFiles(up.files) });
  modal(h("div", {},
    h("h2", {}, "Open"),
    h("p", { class: "muted" }, "Pick a file, or open a folder (all GPH files below it, e.g. the whole Graphics folder: that lets the editor resolve links between files). ",
      "A GraphicBase.RNEG.gz (Data_Base/Graphics of the unit's software update) adds the screens view."),
    h("div", { class: "pathbar" }, pathIn, h("button", { onclick: () => browse(pathIn.value) }, "Go"), h("button", { class: "primary", onclick: () => openPath(pathIn.value) }, "Open this folder / file")),
    list,
    h("h3", {}, "Or upload"), h("div", { class: "row" }, up, h("small", {}, "files sent by the browser can only be saved by typing an output path")),
    h("div", { class: "modal-actions" }, h("button", { onclick: closeModal }, "Cancel"))));
  browse(startPath || "");
}

async function uploadFiles(files) {
  for (const file of files) {
    const data = await new Promise((res) => { const rd = new FileReader(); rd.onload = () => res(rd.result.split(",", 2)[1]); rd.readAsDataURL(file); });
    try {
      const r = await act(() => api("POST", "/api/upload", { name: file.name, data_b64: data }));
      for (const f of r.opened) updateFile(f);
      closeModal();
      if (r.screens) { state.screens = null; await loadSettings(); renderSidebar(); toast(`Screen database loaded: ${r.screens.count} screens`); if (!r.opened.length) go(null, "screens"); }
      const f = r.opened[0];
      if (f) go(f.id, f.sections.find((s) => s.used)?.name);
    } catch (e) { /* toast shown */ }
  }
}

function saveDialog(fid) {
  const f = fileById(fid);
  const out = h("input", { type: "text", value: f.suggested_out, style: "width:100%" });
  const ver = h("input", { type: "text", placeholder: "keep / now", size: 16, class: "mono" });
  const inf = h("input", { type: "checkbox", checked: true });
  const result = h("div");
  const doSave = async () => {
    const r = await fileAction(fid, "save", { out: out.value, version: ver.value || null, inf: inf.checked });
    result.innerHTML = "";
    result.append(h("p", {}, "Written ", h("code", {}, r.saved.path), ` (${r.saved.size} bytes)`,
      r.saved.inf ? [h("br"), "and ", h("code", {}, r.saved.inf), r.saved.inf_fields ? ` VER ${r.saved.inf_fields.VER}` : ""] : null));
    toast("Saved");
  };
  modal(h("div", {},
    h("h2", {}, "Save ", h("code", {}, f.label)),
    h("p", { class: "muted" }, "A path ending in .bz2 is compressed and gets its .inf side-car (VER and the other fields are copied from the original's .inf). A folder gets the file name the unit expects. The original is never overwritten."),
    h("div", { class: "row" }, h("label", {}, "output"), out),
    h("div", { class: "row" }, h("label", {}, "VER"), ver, h("small", {}, "yy.mm.dd.hh.mm")),
    h("div", { class: "row" }, h("label", {}, h("span", {}, inf, " write .inf"))),
    result,
    h("div", { class: "modal-actions" }, h("button", { onclick: closeModal }, "Close"), h("button", { class: "primary", onclick: doSave }, "Save"))));
  out.focus();
}

// ---------------------------------------------------------------------------
// boot
async function loadSettings() {
  try { state.settings = await api("GET", "/api/settings"); } catch (e) { state.settings = null; }
  renderSettings();
}

function renderSettings() {
  const box = $("#settings");
  box.innerHTML = "";
  const st = state.settings;
  if (!st || (!st.themes.length && !st.languages.length)) return;
  const mk = (label, key, options, current) => {
    const sel = h("select", { title: `${label} used to resolve links` }, ...options.map((o) => h("option", { value: o, selected: o === current }, o)));
    sel.onchange = async () => {
      state.settings = await act(() => api("POST", "/api/settings", { [key]: sel.value }));
      renderSettings();
      if (state.route.i != null) showDetail();
    };
    return h("label", { class: "setting" }, label + " ", sel);
  };
  if (st.themes.length) box.append(mk("Theme", "theme", st.themes, st.theme));
  if (st.languages.length) box.append(mk("Language", "language", st.languages, st.language));
}

async function boot() {
  $("#btn-open").onclick = () => openDialog();
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  // drag & drop upload
  let drag = 0;
  document.addEventListener("dragenter", (e) => { e.preventDefault(); drag++; document.body.classList.add("drop"); });
  document.addEventListener("dragleave", () => { if (--drag <= 0) { drag = 0; document.body.classList.remove("drop"); } });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => { e.preventDefault(); drag = 0; document.body.classList.remove("drop"); if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });
  window.addEventListener("beforeunload", (e) => { if (state.files.some((f) => f.dirty)) { e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("hashchange", onRoute);
  const r = await api("GET", "/api/files");
  state.files = r.files;
  for (const f of state.files) state.rev[f.id] = 1;
  $("#status").textContent = r.cwd ? `working folder ${r.cwd}` : "";
  await loadSettings();
  if (isPublic()) {
    $("#btn-open").textContent = "Upload…";
    $("#status").textContent = "online version: your files stay in your own session (two hours of inactivity), nothing is written on the server";
  }
  renderSidebar();
  if (!location.hash && state.files.length) {
    const f = state.files[0];
    go(f.id, (f.sections.find((s) => s.used) || {}).name);
  } else onRoute();
}

boot().catch((e) => toast(e.message, "err", 10000));
