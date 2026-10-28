/* GPH editor front end: plain JavaScript over the JSON API in gph/server.py. */
"use strict";

const state = {
  files: [],            // from /api/files
  rev: {},              // file id -> counter, busts image caches after edits
  route: { f: null, s: null, i: null },
  rows: [],             // rows of the current section
  filter: "",
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

function updateFile(info) {
  const i = state.files.findIndex((f) => f.id === info.id);
  if (i >= 0) state.files[i] = info; else state.files.push(info);
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
    box.append(h("div", { class: "file-tools" },
      h("button", { class: "small primary", onclick: () => saveDialog(f.id) }, "Save…"),
      h("button", { class: "small", disabled: !f.undo, onclick: () => fileAction(f.id, "undo") }, "Undo"),
      h("button", { class: "small", disabled: !f.dirty || !f.path, onclick: () => confirmRevert(f.id) }, "Revert"),
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
}

// ---------------------------------------------------------------------------
// section views
async function loadSection() {
  const { f, s } = state.route;
  const main = $("#main");
  if (f == null || !s || !fileById(f)) {
    state.rows = [];
    main.innerHTML = "";
    main.append(h("div", { class: "empty" }, state.files.length ? "Pick a section on the left." : "Open a GPH file or folder to start."));
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
    tb.append(h("tr", { "data-i": r.index, onclick: () => go(state.route.f, state.route.s, r.index) },
      h("td", { class: "idx" }, hex4(r.index)),
      h("td", { class: "mono" }, summary(r.fields), r.problems.length ? h("div", { class: "problem" }, r.problems[0]) : null)));
  }
  return { shown: rows.length, el: h("table", { class: "items" }, h("thead", {}, h("tr", {}, h("th", {}, "index"), h("th", {}, "fields"))), tb) };
}

// ---------------------------------------------------------------------------
// detail pane
function hideDetail() { const d = $("#detail"); d.hidden = true; d.innerHTML = ""; }

async function showDetail() {
  const { f, s, i } = state.route;
  const file = fileById(f);
  if (!file) return;
  const sec = file.sections.find((x) => x.name === s);
  const d = $("#detail");
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
  if (Object.keys(item.described).length) {
    const dl = h("dl", { class: "kv" });
    for (const [k, v] of Object.entries(item.described)) dl.append(h("dt", {}, k), h("dd", {}, Array.isArray(v) ? v.join(", ") : v));
    d.append(h("h3", {}, "Links"), dl);
  }
  if (item.problems.length) d.append(h("p", { class: "problem" }, item.problems.join("; ")));
  d.append(rawJson(f, s, i, item, true));
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

async function openDialog(startPath) {
  const pathIn = h("input", { type: "text", placeholder: "/path/to/Graphics or a .gph.bz2 file" });
  const list = h("div", { class: "browse" });
  const openPath = async (p) => {
    const r = await act(() => api("POST", "/api/open", { path: p }));
    for (const f of r.opened) updateFile(f);
    closeModal();
    if (r.opened.length) { toast(`Opened ${r.opened.length} file${r.opened.length > 1 ? "s" : ""}`); const f = r.opened[0]; go(f.id, f.sections.find((s) => s.used)?.name); }
    else if (r.already_open.length) { const f = r.already_open[0]; go(f.id, f.sections.find((s) => s.used)?.name); toast("Already open", "warn"); }
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
    h("p", { class: "muted" }, "Pick a file, or open a folder (all GPH files below it, e.g. the whole Graphics folder: that lets the editor resolve links between files)."),
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
      const f = r.opened[0];
      go(f.id, f.sections.find((s) => s.used)?.name);
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
  $("#status").textContent = `working folder ${r.cwd}`;
  renderSidebar();
  if (!location.hash && state.files.length) {
    const f = state.files[0];
    go(f.id, (f.sections.find((s) => s.used) || {}).name);
  } else onRoute();
}

boot().catch((e) => toast(e.message, "err", 10000));
