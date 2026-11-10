"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

let S = null;            // /api/state
let chatId = null;       // open chat
let busy = false;
let citeInfo = {};       // id -> {source, ref, heading}
let project = "";        // the project chosen in the sidebar ("" for none)

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" && !(opts.body instanceof Blob) && !(opts.body instanceof ArrayBuffer) ? JSON.stringify(opts.body) : opts.body });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

// ---------- markdown (small and safe: escapes first) ----------
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" data-ext>$1</a>')
    .replace(/\[[rf]\d+\](?:[ ,;]*\[[rf]\d+\])*/g, citeRun);
}
// A run of citations, e.g. "[r12][r12]" or "[r12], [r13]": each label once, so a repeat doesn't show as two chips.
function citeRun(run) {
  const seen = new Set();
  return run.match(/[rf]\d+/g).filter((id) => !seen.has(citeLabel(id)) && seen.add(citeLabel(id))).map(citeChip).join(" ");
}
function citeLabel(id) {
  const c = citeInfo[id];
  return c ? `${c.source}${c.ref ? " " + c.ref : ""}` : id;
}
function citeChip(id) {
  const c = citeInfo[id];
  const label = citeLabel(id);
  return `<button class="cite${id[0] === "f" ? " file" : ""}" data-pid="${esc(id)}" title="${esc(c ? (c.heading || c.title || "") : "Open passage")}">${esc(label)}</button>`;
}
function md(text) {
  const lines = esc(text || "").split("\n");
  let out = [], list = null, para = [], i = 0;
  const flushPara = () => { if (para.length) { out.push("<p>" + inline(para.join("<br>")) + "</p>"); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>` + list.items.map((x) => "<li>" + inline(x) + "</li>").join("") + `</${list.tag}>`); list = null; } };
  while (i < lines.length) {
    const ln = lines[i];
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flushPara(); flushList();
      const row = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = row(ln); i += 2; const body = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body.push(row(lines[i++]));
      out.push("<table><thead><tr>" + head.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>" +
        body.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
      continue;
    }
    let m;
    if ((m = ln.match(/^(#{1,4})\s+(.*)/))) { flushPara(); flushList(); out.push(`<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`); }
    else if ((m = ln.match(/^\s*([-*•]|\d+[.)])\s+(.*)/))) {
      flushPara();
      const tag = /\d/.test(m[1]) ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[2]);
    }
    else if (/^&gt;\s?/.test(ln)) { flushPara(); flushList(); out.push("<blockquote>" + inline(ln.replace(/^&gt;\s?/, "")) + "</blockquote>"); }
    else if (!ln.trim()) { flushPara(); flushList(); }
    else if (list && /^\s{2,}\S/.test(ln)) { list.items[list.items.length - 1] += " " + ln.trim(); }
    else { flushList(); para.push(ln); }
    i++;
  }
  flushPara(); flushList();
  return out.join("");
}

// ---------- state ----------
async function refresh() {
  S = await api("/api/state");
  const s = S.settings;
  $("#reasoning").value = s.reasoning || "auto";
  $("#length").value = s.length || "normal";
  applyTheme(s.theme || "auto");
  $("#version").textContent = `Version ${S.version} · library ${S.library.version || "none"}`;
  $("#filesCount").textContent = S.files.filter((f) => !f.project).length || "";
  $("#libCount").textContent = S.library.sources.length || "";
  const b = $("#engineBadge");
  const e = s.engine;
  if (!e.mode) { b.className = "badge none"; b.textContent = "No AI engine set up"; b.onclick = () => openSettings(); }
  else if (s.private) { b.className = "badge private"; b.textContent = "Private: runs on " + (e.mode === "bundled" ? "this computer" : "your machine or network"); b.onclick = null; }
  else { b.className = "badge remote"; b.textContent = "Questions go to " + hostOf(e.base_url); b.onclick = () => openSettings(); }
  const u = S.updates || {};
  const note = $("#updateNote");
  if (u.app || u.library) {
    note.hidden = false;
    note.textContent = u.app ? `Version ${u.app.version} is available` : `Library update ${u.library.version} available`;
    note.onclick = () => { openSettings("about"); };
  } else note.hidden = true;
  renderProjects();
  renderChats();
  return S;
}
const hostOf = (u) => { try { return new URL(u).host; } catch { return u; } };

function renderChats() {
  const nav = $("#chatList");
  nav.innerHTML = "";
  for (const c of S.chats.filter((c) => (c.project || "") === project)) {
    const row = document.createElement("div");
    row.className = "chat-item" + (c.id === chatId ? " active" : "");
    row.innerHTML = `<button class="open" title="${esc(c.title)}">${esc(c.title || "Chat")}</button><button class="del" title="Delete" aria-label="Delete chat"><svg class="x" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8"/></svg></button>`;
    $(".open", row).onclick = () => openChat(c.id);
    $(".del", row).onclick = async () => {
      if (!confirm("Delete this chat?")) return;
      await api("/api/chat?id=" + encodeURIComponent(c.id), { method: "DELETE" });
      if (c.id === chatId) newChat();
      refresh();
    };
    nav.appendChild(row);
  }
}

// ---------- theme ----------
// Saved in Settings; kept in localStorage too so the script in index.html can set it before the page is drawn.
const systemDark = matchMedia("(prefers-color-scheme: dark)");
function applyTheme(t) {
  try { localStorage.setItem("para-theme", t); } catch { }
  document.documentElement.dataset.theme = t === "dark" || (t !== "light" && systemDark.matches) ? "dark" : "light";
  $$("[data-theme-pick]").forEach((b) => b.setAttribute("aria-checked", b.dataset.themePick === t));
}
systemDark.addEventListener("change", () => applyTheme(S ? S.settings.theme : "auto"));
$$("[data-theme-pick]").forEach((b) => b.onclick = async () => {
  applyTheme(b.dataset.themePick);
  S = await api("/api/settings", { method: "POST", body: { theme: b.dataset.themePick } });
});

// ---------- projects ----------
function renderProjects() {
  const names = S.projects.map((p) => p.name);
  project = names.includes(S.settings.current_project) ? S.settings.current_project : "";
  $("#projectBtn").innerHTML = `<span>${esc(project || "No project")}</span>`;
  $("#projectMenu").innerHTML = [["", "No project"], ...names.map((n) => [n, n])].map(([v, label]) =>
    `<button role="menuitemradio" aria-checked="${v === project}" data-project="${esc(v)}"${v ? "" : ' class="none"'}><span>${esc(label)}</span></button>`).join("") +
    `<button role="menuitem" class="new" data-newproject>New project</button>`;
  $("#projectEdit").hidden = !project;
  fitPlaceholder();
}
// The longest wording that fits on the box's one line: browsers cut a textarea's placeholder mid-word, without
// an ellipsis. The box narrows when the reader opens, so it's fitted again then.
let measure;
function fitPlaceholder() {
  const q = $("#question");
  measure = measure || document.createElement("canvas").getContext("2d");
  measure.font = getComputedStyle(q).font;
  const options = project ? [`Ask a question about ${project}…`, `Ask about ${project}…`] : [];
  q.placeholder = options.find((t) => measure.measureText(t).width <= q.clientWidth) || "Ask a question…";
}
new ResizeObserver(fitPlaceholder).observe($("#question"));
async function setProject(name) {
  if (busy) return;
  S = await api("/api/settings", { method: "POST", body: { current_project: name } });
  project = name;
  $("#sidebar").classList.remove("open");
  newChat();
  refresh();
}
// the project menu: drawn here rather than a native select, whose list the system draws in its own style
const pmenu = $("#projectMenu"), pbtn = $("#projectBtn");
function projectMenu(open) {
  pmenu.hidden = !open;
  pbtn.setAttribute("aria-expanded", open);
  if (open) (pmenu.querySelector("[aria-checked=true]") || pmenu.querySelector("button")).focus();
}
pbtn.onclick = () => projectMenu(pmenu.hidden);
pmenu.onclick = (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  projectMenu(false);
  if ("newproject" in b.dataset) openProject(null);
  else if (b.dataset.project !== project) setProject(b.dataset.project);
};
pmenu.addEventListener("keydown", (e) => {
  const items = $$("button", pmenu), i = items.indexOf(document.activeElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus(); }
  else if (e.key === "Escape") { projectMenu(false); pbtn.focus(); }
});
document.addEventListener("click", (e) => { if (!pmenu.hidden && !e.target.closest(".project-pick")) projectMenu(false); });
$("#projectEdit").onclick = () => openProject(project);

const dlgP = $("#dlg-project");
let editing = null;      // the project open in the dialog, null while creating one
function openProject(name) {
  editing = name;
  const p = S.projects.find((x) => x.name === name);
  dlgP.classList.toggle("creating", !p);
  $("#projectTitle").textContent = p ? p.name : "New project";
  $("#projectName").value = p ? p.name : "";
  $("#projectNotesOne").value = p ? p.notes : "";
  $("#projectSave").textContent = p ? "Save" : "Create";
  $("#projectMsg").textContent = "";
  if (p) renderProjectFiles();
  if (!dlgP.open) dlgP.showModal();
  if (!p) $("#projectName").focus();
  if (p) api("/api/files/sync", { method: "POST" }).then((r) => { S.files = r.files; renderProjectFiles(); });
}
function renderProjectFiles() {
  renderFileList($("#projectFileList"), S.files.filter((f) => f.project === editing));
}
$("#projectSave").onclick = async () => {
  const name = $("#projectName").value.trim(), notes = $("#projectNotesOne").value;
  try {
    const r = editing === null
      ? await api("/api/projects", { method: "POST", body: { name, notes } })
      : await api("/api/projects/update", { method: "POST", body: { name: editing, new_name: name, notes } });
    S = r;
    const created = editing === null;
    await setProject(r.project);
    if (created) openProject(r.project);   // carry on: add the project's files
    else dlgP.close();
  } catch (e) { $("#projectMsg").textContent = e.message; }
};
$("#projectDelete").onclick = async () => {
  if (!confirm(`Delete the project ${editing}? Its chats are kept, outside any project.`)) return;
  try {
    S = await api("/api/projects?name=" + encodeURIComponent(editing), { method: "DELETE" });
    dlgP.close();
    await setProject("");
  } catch (e) { $("#projectMsg").textContent = e.message; }
};
$("#openProjectFolder").onclick = (e) => { e.preventDefault(); api("/api/open", { method: "POST", body: { folder: "files", project: editing } }); };
$("#projectPick").onchange = (e) => { upload([...e.target.files], editing); e.target.value = ""; };
dropZone($("#projectDrop"), () => editing);

function newChat() {
  chatId = null;
  $("#messages").innerHTML = "";
  $("#messages").appendChild(welcomeEl);
  renderChats();
  $("#question").focus();
}

async function openChat(id) {
  if (busy) return;
  const chat = await api("/api/chat?id=" + encodeURIComponent(id));
  chatId = id;
  const box = $("#messages");
  box.innerHTML = "";
  for (const t of chat.turns || []) {
    addUser(t.question);
    const v = addAssistant();
    Object.assign(citeInfo, t.cites || {});
    for (const ev of t.events || []) v.activity(ev);
    if (t.error) v.error(t.error);
    v.finish({ answer: t.answer, cites: t.cites || {}, read: t.read || [], seen: t.seen || [], dropped: t.dropped || [], cut_off: t.cut_off, stopped: t.stopped });
  }
  box.scrollTop = box.scrollHeight;
  renderChats();
}

// ---------- messages ----------
function addUser(text) {
  $("#welcome")?.remove();
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  $("#messages").appendChild(el);
}

function addAssistant() {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<details class="reasoning" hidden><summary>Show reasoning</summary><pre></pre></details>
    <details class="activity" hidden><summary>Looked up</summary><ul></ul></details>
    <div class="answer cursor"></div><div class="extras"></div>`;
  $("#messages").appendChild(el);
  let text = "", thinking = "", searches = 0, reads = 0;
  const box = $("#messages");
  const stick = () => { if (box.scrollHeight - box.scrollTop - box.clientHeight < 160) box.scrollTop = box.scrollHeight; };
  const ans = $(".answer", el);
  const act = $(".activity", el);
  const summary = () => { $("summary", act).textContent = `Looked up: ${searches} search${searches === 1 ? "" : "es"}, read ${reads} passage${reads === 1 ? "" : "s"}`; };
  return {
    el,
    token(t) { text += t; ans.innerHTML = md(text); stick(); },
    reset() { text = ""; ans.innerHTML = ""; },
    thinking(t) { thinking += t; const r = $(".reasoning", el); r.hidden = false; $("pre", r).textContent = thinking; },
    activity(ev) {
      act.hidden = false;
      const li = document.createElement("li");
      if (ev.type === "search") {
        searches++;
        // So a citation to a passage only found by search shows its label while streaming, not its id.
        for (const r of ev.results || []) citeInfo[r.id] = citeInfo[r.id] || r;
        li.innerHTML = `Searched “${esc(ev.query)}” · ${ev.results.length} result${ev.results.length === 1 ? "" : "s"}`;
      } else if (ev.type === "read" || ev.type === "show") {
        reads += ev.type === "read" ? 1 : 0;
        citeInfo[ev.id] = citeInfo[ev.id] || ev;
        for (const r of ev.nearby || []) citeInfo[r.id] = citeInfo[r.id] || r;  // the passages read along with it
        li.innerHTML = `${ev.type === "read" ? "Read" : "Showed"} <button class="src" data-pid="${esc(ev.id)}">${esc(ev.source)} ${esc(ev.ref || "")}${ev.heading ? " · " + esc(ev.heading) : ""}</button>`;
        if (ev.type === "show") openPassage(ev.id);
      } else if (ev.type === "notice") {
        li.textContent = ev.text;
      }
      $("ul", act).appendChild(li);
      summary();
    },
    error(msg) {
      ans.classList.remove("cursor");
      $(".extras", el).insertAdjacentHTML("beforeend", `<div class="error">${esc(msg)}</div>`);
      if (/engine|Settings|reach/i.test(msg)) $(".extras", el).insertAdjacentHTML("beforeend", `<p><button class="btn small" data-settings>Open AI engine settings</button></p>`);
    },
    finish(done) {
      ans.classList.remove("cursor");
      Object.assign(citeInfo, done.cites || {});
      ans.innerHTML = md(done.answer || text);
      const x = $(".extras", el);
      if (done.cut_off) x.insertAdjacentHTML("beforeend", `<div class="note">The answer hit the length limit. Ask it to continue, or raise Length below.</div>`);
      if (done.stopped) x.insertAdjacentHTML("beforeend", `<div class="note">Stopped.</div>`);
      if (done.dropped && done.dropped.length) x.insertAdjacentHTML("beforeend", `<div class="note">The AI cited ${done.dropped.length} passage${done.dropped.length > 1 ? "s" : ""} it never looked up, so ${done.dropped.length > 1 ? "they were" : "it was"} removed. Treat the uncited parts with care.</div>`);
      const cited = Object.keys(done.cites || {});
      const read = (done.read || []).filter((r) => !cited.includes(r.id));
      if (cited.length || read.length) {
        const chips = cited.map((id) => chip(done.cites[id])).join("") + read.map(chip).join("");
        x.insertAdjacentHTML("beforeend", `<div class="sources"><span class="label">${cited.length ? "Cited" + (read.length ? " and read" : "") : "Read"}</span>${chips}</div>`);
      } else if ((done.answer || "").trim() && !done.stopped) {
        x.insertAdjacentHTML("beforeend", `<div class="note">No library passages were used for this answer: it comes from the AI's own knowledge. Check it against the documents.</div>`);
      }
    },
  };
}
const chip = (c) => `<button class="source-chip" data-pid="${esc(c.id)}" title="${esc(c.heading || c.title || "")}">${esc(c.source)}${c.ref ? " " + esc(c.ref) : ""}</button>`;

async function ask(question) {
  if (busy || !question.trim()) return;
  if (!S.settings.engine.mode) { openSettings(); return; }
  busy = true;
  $("#send").hidden = true; $("#stop").hidden = false;
  addUser(question);
  const v = addAssistant();
  $("#messages").scrollTop = $("#messages").scrollHeight;
  setStatus("Thinking…");
  let finished = false;
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: chatId, question, project }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, k); buf = buf.slice(k + 2);
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "chat") chatId = ev.id;
        else if (ev.type === "status") setStatus(ev.text || (ev.model ? "Using " + ev.model : ""));
        else if (ev.type === "thinking") { v.thinking(ev.text); setStatus("Reasoning…"); }
        else if (ev.type === "token") { v.token(ev.text); setStatus("Writing…"); }
        else if (ev.type === "retry") v.reset();
        else if (["search", "read", "show", "notice"].includes(ev.type)) { v.activity(ev); setStatus(ev.type === "search" ? `Searching “${ev.query}”…` : "Reading…"); }
        else if (ev.type === "done") { v.finish(ev); finished = true; }
        else if (ev.type === "stopped") { v.finish({ answer: ev.answer, stopped: true }); finished = true; }
        else if (ev.type === "error") { v.error(ev.text); finished = true; }
      }
    }
    if (!finished) v.finish({ answer: "", stopped: true });
  } catch (e) {
    v.error(e.message);
  } finally {
    busy = false; setStatus("");
    $("#send").hidden = false; $("#stop").hidden = true;
    refresh();
  }
}
function setStatus(t) { $("#status").textContent = t; }

// ---------- reader ----------
// A checked description of a drawing is shown under its image, labelled; its copy in the passage text (there for
// search and for the AI) isn't repeated.
const DESCRIPTION_LABEL = "Description of the drawing (written by AI from the image, checked by a person):";
function figureDescription(f) {
  if (!f.description) return "";
  // collapsed: it's there to check what the AI was told about the drawing, not to read instead of the drawing
  return `<details class="figure-desc"><summary>Description of the drawing</summary>${esc(f.description)}` +
    `<span class="who">Written by AI from the image, checked by a person. The document's text comes first.</span></details>`;
}
function withoutDescriptions(p) {
  if (!(p.figures || []).some((f) => f.description)) return p.text;
  return p.text.split("\n\n").filter((para) => !para.startsWith(DESCRIPTION_LABEL)).join("\n\n");
}
// The words printed in a drawing are taken out for search and the AI. Shown collapsed, and never as a description.
const LABELS = "Text in the drawing:";
function passageBody(p) {
  const paras = withoutDescriptions(p).split("\n\n");
  const labels = paras.filter((para) => para.startsWith(LABELS));
  return esc(paras.filter((para) => !para.startsWith(LABELS)).join("\n\n").trimEnd()) + labels.map((para) =>
    `<details class="figure-desc drawing-text"><summary>Text in the drawing</summary>${esc(para.slice(LABELS.length).trim())}` +
    `<span class="who">The words printed in the drawing, read out for search and the AI. Not a description of it.</span></details>`).join("");
}

async function openPassage(pid) {
  try { showDoc(await api("/api/passage?id=" + encodeURIComponent(pid))); } catch (e) { alert(e.message); }
}
async function openSource(key) {
  try { showDoc(await api("/api/source?key=" + encodeURIComponent(key))); } catch (e) { alert(e.message); }
}
function showDoc(doc) {
  const r = $("#reader");
  r.hidden = false;
  $("#readerSource").textContent = doc.source;
  $("#readerTitle").textContent = doc.title;
  const link = $("#readerLink");
  if (doc.url) { link.hidden = false; link.textContent = "Original ↗"; link.onclick = (e) => { e.preventDefault(); openUrl(doc.url); }; }
  else if (doc.path) { link.hidden = false; link.textContent = "Open file ↗"; link.onclick = (e) => { e.preventDefault(); api("/api/open", { method: "POST", body: { file: doc.path } }); }; }
  else link.hidden = true;
  $("#readerLicense").textContent = doc.license || (doc.kind === "file" ? "One of your files." : "");
  const body = $("#readerBody");
  const isPdf = /\.pdf($|[?#])/i.test(doc.url || "");
  const pageLink = (n) => (doc.url || "").split("#")[0] + "#page=" + n;
  body.innerHTML = doc.parts.map((p) => `<div class="passage${p.id === doc.focus ? " focus" : ""}" id="p-${esc(p.id)}"><span class="ph">${esc([p.heading, p.ref].filter(Boolean).join(" · "))}${p.page && isPdf ? ` · <a href="#" data-page="${p.page}">PDF page ${p.page} ↗</a>` : ""}</span>${(p.figures || []).map((f) => `<img class="figure" src="/api/figure?id=${f.id}" ${f.width ? `width="${f.width}" height="${f.height}"` : ""} alt="${esc(f.description || p.ref || "Drawing")}" title="Click to enlarge" loading="lazy">${figureDescription(f)}`).join("")}${passageBody(p)}</div>`).join("");
  $$("[data-page]", body).forEach((a) => { a.onclick = (e) => { e.preventDefault(); openUrl(pageLink(a.dataset.page)); }; });
  const focusPart = doc.parts.find((p) => p.id === doc.focus);
  if (isPdf && focusPart && focusPart.page) {
    link.textContent = `Original, page ${focusPart.page} ↗`;
    link.onclick = (e) => { e.preventDefault(); openUrl(pageLink(focusPart.page)); };
  }
  $("#readerFind").value = "";
  const f = document.getElementById("p-" + doc.focus);
  if (f) f.scrollIntoView({ block: f.querySelector("img.figure") ? "start" : "center" });
}
function openUrl(url) { api("/api/open", { method: "POST", body: { url } }).catch(() => window.open(url, "_blank")); }
$("#closeReader").onclick = () => { $("#reader").hidden = true; };
$("#readerFind").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  let first = null;
  for (const p of $$("#readerBody .passage")) {
    const txt = p.textContent.toLowerCase();
    const hit = q.length > 2 && txt.includes(q);
    p.style.display = !q || q.length <= 2 || hit ? "" : "none";
    if (hit && !first) first = p;
  }
  if (first) first.scrollIntoView({ block: "start" });
});

// ---------- enlarged figures ----------
const dlgFig = $("#dlg-figure");
function enlargeFigure(img) {
  const big = $("#figureBig");
  big.src = img.src; big.alt = img.alt;
  dlgFig.showModal();
}
// Any click closes it: on the drawing, on the dark area round it, or on the close button (a form, so it closes itself).
dlgFig.addEventListener("click", (e) => { if (!e.target.closest("form")) dlgFig.close(); });

// ---------- clicks anywhere ----------
document.addEventListener("click", (e) => {
  const fig = e.target.closest("img.figure");
  if (fig) { enlargeFigure(fig); return; }
  const pidEl = e.target.closest("[data-pid]");
  if (pidEl) { e.preventDefault(); openPassage(pidEl.dataset.pid); return; }
  const ext = e.target.closest("a[data-ext]");
  if (ext) { e.preventDefault(); openUrl(ext.href); return; }
  if (e.target.closest("[data-settings]")) { openSettings(); return; }
  const op = e.target.closest("[data-open]");
  if (op) { ({ settings: openSettings, files: openFiles, library: openLibrary })[op.dataset.open](); }
});

// ---------- composer ----------
const q = $("#question");
q.addEventListener("input", () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 240) + "px"; });
q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); } });
$("#composer").addEventListener("submit", (e) => { e.preventDefault(); const t = q.value; q.value = ""; q.style.height = "auto"; ask(t); });
$("#stop").onclick = () => api("/api/stop", { method: "POST", body: { id: chatId } });
$("#newChat").onclick = () => { if (!busy) newChat(); };
$("#reasoning").onchange = (e) => api("/api/settings", { method: "POST", body: { reasoning: e.target.value } });
$("#length").onchange = (e) => api("/api/settings", { method: "POST", body: { length: e.target.value } });
const welcomeEl = $("#welcome");
$$(".examples button").forEach((b) => b.onclick = () => ask(b.textContent));

// ---------- settings ----------
const dlgS = $("#dlg-settings");
function showTab(name) {
  $$(".tab", dlgS).forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tabpane", dlgS).forEach((p) => p.hidden = p.dataset.pane !== name);
}
$$(".tab", dlgS).forEach((t) => t.onclick = () => showTab(t.dataset.tab));

function openSettings(tab = "engine") {
  const s = S.settings, e = s.engine;
  // a hosted service is saved as an external engine; which card is ticked follows from its address
  const mode = e.mode === "external" && e.base_url && !isLocal(e.base_url) ? "hosted" : e.mode || "";
  $$("input[name=mode]").forEach((r) => r.checked = r.value === mode);
  $("#baseUrl").value = e.base_url || "";
  $("#apiKey").value = "";
  fillModels(e.model ? [e.model] : [], e.model);
  if (e.mode === "external" && e.base_url) loadModels(true);  // the full list, so the menu offers every model
  $("#instructions").value = s.instructions || S.default_instructions;
  $("#projectNotes").value = s.project_notes || "";
  $("#checkUpdates").checked = s.check_updates !== false;
  $("#testResult").textContent = "";
  modeChanged();
  renderAbout();
  showTab(tab);
  if (!dlgS.open) dlgS.showModal();
  if (!mode) { findEngines(); }
}
function modeChanged() {
  const m = $("input[name=mode]:checked")?.value;
  $("#externalPane").hidden = m !== "external" && m !== "hosted";
  $("#localFind").hidden = m !== "external";
  $("#hostedPick").hidden = m !== "hosted";
  $("#bundledPane").hidden = m !== "bundled";
  if (m === "bundled") renderBundled();
  if (m === "hosted") renderHosted();
  remoteCheck();
}
$$("input[name=mode]").forEach((r) => r.onchange = modeChanged);
function isLocal(u) {
  try { return /^(localhost|127\.|::1|\[::1\]|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)|\.local$/.test(new URL(u).hostname); } catch { return true; }
}
function remoteCheck() {
  const u = $("#baseUrl").value.trim(), m = $("input[name=mode]:checked")?.value;
  $("#remoteWarning").hidden = !u || isLocal(u) || (m !== "external" && m !== "hosted");
  $("#remoteHost").textContent = hostOf(u);
  // the saved key is only ever sent to the server it was saved for
  const e = S.settings.engine, saved = e.api_key_set && hostOf(u) === hostOf(e.base_url);
  $("#apiKey").placeholder = saved ? "saved (type to replace)" : m === "hosted" ? "paste your key from the service" : "leave empty for local servers";
  $("#apiKeyHint").textContent = m === "hosted" ? "(from your account with the service; kept on this computer)" : "(only if the server needs one)";
  const p = (S.hosted || []).find((h) => h.base_url === u.replace(/\/+$/, ""));
  $$("#hostedList .btn").forEach((b) => b.classList.toggle("on", b.textContent === p?.name));
  $("#hostedAbout").innerHTML = p ? `${esc(p.where)} <a href="#" id="hostedKeys">Get an API key ↗</a>` : "Or type the address of another OpenAI-compatible service below.";
  if (p) $("#hostedKeys").onclick = (ev) => { ev.preventDefault(); openUrl(p.keys); };
  $("#modelSelect").placeholder = p ? `e.g. ${p.example}` : "press Refresh, or type the model's name";
}
$("#baseUrl").addEventListener("input", remoteCheck);
function renderHosted() {
  $("#hostedList").innerHTML = "";
  for (const h of S.hosted || []) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn"; b.textContent = h.name;
    b.onclick = () => { $("#baseUrl").value = h.base_url; $("#apiKey").value = ""; fillModels([], ""); remoteCheck(); $("#apiKey").focus(); };
    $("#hostedList").appendChild(b);
  }
}
let modelNames = [], modelActive = -1;
function fillModels(models, chosen) {
  modelNames = models;
  $("#modelSelect").value = chosen || "";
  modelMenu(false);
}
// The model field: type any name, or pick from the server's list. Drawn here rather than a datalist, which shows
// only the names matching what's in the box, so with a model chosen it listed that one alone.
const mfield = $("#modelSelect"), mmenu = $("#modelMenu");
function modelMenu(open, filter = "") {
  const f = filter.toLowerCase(), shown = modelNames.filter((m) => m.toLowerCase().includes(f));
  open = open && shown.length > 0;
  mmenu.hidden = !open;
  mfield.setAttribute("aria-expanded", open);
  if (!open) return;
  mmenu.innerHTML = shown.map((m, i) =>
    `<div role="option" id="modelOpt${i}" aria-selected="${m === mfield.value}"><span>${esc(m)}</span></div>`).join("");
  // opens upwards where the dialog has no room for it below the field
  const room = mfield.closest("dialog").getBoundingClientRect().bottom - mfield.getBoundingClientRect().bottom;
  mmenu.classList.toggle("up", room < Math.min(mmenu.scrollHeight, 280) + 24);
  modelActive = Math.max(0, shown.indexOf(mfield.value));
  modelHighlight();
}
function modelHighlight() {
  const items = $$("[role=option]", mmenu);
  items.forEach((el, i) => el.classList.toggle("active", i === modelActive));
  const el = items[modelActive];  // scrolls the menu only: scrollIntoView would scroll the dialog too
  if (el && el.offsetTop < mmenu.scrollTop) mmenu.scrollTop = el.offsetTop - 6;
  else if (el && el.offsetTop + el.offsetHeight > mmenu.scrollTop + mmenu.clientHeight) {
    mmenu.scrollTop = el.offsetTop + el.offsetHeight - mmenu.clientHeight + 6;
  }
  mfield.setAttribute("aria-activedescendant", items[modelActive]?.id || "");
}
function modelPick(name) {
  mfield.value = name;
  modelMenu(false);
}
mfield.addEventListener("click", () => {
  if (mmenu.hidden) mfield.select();  // so typing searches the list, rather than adding to the chosen name
  modelMenu(mmenu.hidden);
});
mfield.addEventListener("input", () => modelMenu(true, mfield.value));
mfield.addEventListener("blur", () => modelMenu(false));
mfield.addEventListener("keydown", (e) => {
  const items = $$("[role=option]", mmenu);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (mmenu.hidden) return modelMenu(true);
    modelActive = (modelActive + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
    modelHighlight();
  } else if (e.key === "Enter" && !mmenu.hidden && items[modelActive]) {
    e.preventDefault();
    modelPick(items[modelActive].textContent);
  } else if (e.key === "Escape" && !mmenu.hidden) {
    e.preventDefault();  // closes the list, not the dialog
    e.stopPropagation();
    modelMenu(false);
  }
});
$("#modelArrow").addEventListener("mousedown", (e) => {  // mousedown, so the field keeps focus
  e.preventDefault();
  mfield.focus();
  modelMenu(mmenu.hidden);
});
mmenu.addEventListener("mousedown", (e) => {
  e.preventDefault();
  const o = e.target.closest("[role=option]");
  if (o) modelPick(o.textContent);
});
mmenu.addEventListener("click", (e) => e.preventDefault());  // inside a label, a click would reopen it via the field
async function findEngines() {
  $("#findResult").textContent = "Looking…";
  $("#foundList").innerHTML = "";
  try {
    const { found } = await api("/api/engines");
    $("#findResult").textContent = found.length ? "Found:" : "None found on the usual ports. Start your engine, or type its address.";
    for (const f of found) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn";
      b.textContent = `${f.name} (${f.models.length} model${f.models.length === 1 ? "" : "s"})`;
      b.onclick = () => {
        $$("input[name=mode]").forEach((r) => r.checked = r.value === "external"); modeChanged();
        $("#baseUrl").value = f.base_url; fillModels(f.models, f.models[0]); remoteCheck();
      };
      $("#foundList").appendChild(b);
      if (found.length === 1 && !S.settings.engine.mode) b.click();
    }
  } catch (e) { $("#findResult").textContent = e.message; }
}
$("#findEngines").onclick = findEngines;
// quiet: on opening Settings, where an engine that's stopped shouldn't greet the person with an error
async function loadModels(quiet) {
  const url = $("#baseUrl").value.trim();
  try {
    const { models } = await api("/api/models", { method: "POST", body: { base_url: url, api_key: $("#apiKey").value } });
    if (quiet && (!models.length || $("#baseUrl").value.trim() !== url)) return;
    const now = $("#modelSelect").value;
    if (quiet && document.activeElement === mfield) { modelNames = models; return; }  // don't disturb typing
    fillModels(models, models.includes(now) ? now : models.includes(S.settings.engine.model) ? S.settings.engine.model : models[0]);
    $("#testResult").textContent = models.length ? "" : "The server has no models.";
  } catch (e) { if (!quiet) $("#testResult").textContent = e.message; }
}
$("#loadModels").onclick = () => loadModels(false);
let chosenBundled = null;
function renderBundled() {
  const b = S.bundled;
  $("#machineInfo").textContent = `This computer has ${b.ram_gb} GB of memory and ${b.free_gb} GB of free disk space.`;
  chosenBundled = chosenBundled || S.settings.engine.bundled_model || S.recommended;
  $("#modelCards").innerHTML = S.catalog.map((m) => {
    const installed = b.models.includes(m.id);
    const tooBig = m.min_ram_gb > b.ram_gb + 0.5;
    return `<label class="model-card ${m.id === chosenBundled ? "selected" : ""}${tooBig ? " too-big" : ""}">
      <input type="radio" name="bmodel" value="${esc(m.id)}" ${m.id === chosenBundled ? "checked" : ""}>
      <div class="info"><b>${esc(m.name)}</b>${m.id === S.recommended ? '<span class="tag">Recommended</span>' : ""}<br>
      <small class="muted">${esc(m.about)}</small><small class="meta">${m.size_gb} GB download · needs ${m.min_ram_gb} GB memory${tooBig ? " — too big for this computer" : ""}</small></div>
      ${installed ? `<span class="installed">Installed</span> <button type="button" class="btn danger" data-remove="${esc(m.id)}">Delete</button>`
        : `<button type="button" class="btn" data-install="${esc(m.id)}">Download</button>`}
    </label>`;
  }).join("");
  $$("input[name=bmodel]").forEach((r) => r.onchange = () => { chosenBundled = r.value; renderBundled(); });
  $$("[data-install]").forEach((x) => x.onclick = async (e) => {
    e.preventDefault();
    try { await api("/api/bundled/install", { method: "POST", body: { model: x.dataset.install, gpu: $("#gpu").checked } }); chosenBundled = x.dataset.install; pollJobs(); }
    catch (err) { alert(err.message); }
  });
  $$("[data-remove]").forEach((x) => x.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Delete this model from this computer?")) return;
    S.bundled = await api("/api/bundled/remove", { method: "POST", body: { model: x.dataset.remove } }); renderBundled();
  });
  renderJob(b.jobs.install);
}
function renderJob(j) {
  const v = $("#jobView");
  if (!j) { v.innerHTML = ""; return; }
  const pct = j.total ? Math.round(j.done / j.total * 100) : 0;
  const gb = (n) => (n / 2 ** 30).toFixed(1);
  v.innerHTML = j.state === "running" ? `<div class="job-line"><span>Downloading ${esc(j.label)}…</span><span class="meta">${gb(j.done)} of ${j.total ? gb(j.total) : "?"} GB</span></div><div class="progress"><div style="width:${pct}%"></div></div><button type="button" class="btn small" id="cancelJob">Cancel</button>`
    : j.state === "error" ? `<div class="error">${esc(j.error)}</div>` : `<div class="ok-line">Downloaded. Press Save to use it.</div>`;
  const c = $("#cancelJob"); if (c) c.onclick = () => api("/api/bundled/cancel", { method: "POST" });
}
let polling = false;
async function pollJobs() {
  if (polling) return; polling = true;
  try {
    for (;;) {
      const { bundled, library_job } = await api("/api/jobs");
      S.bundled = bundled;
      if (!$("#bundledPane").hidden) renderBundled();
      if (library_job) renderLibraryJob(library_job);
      const running = (bundled.jobs.install && bundled.jobs.install.state === "running") || (library_job && library_job.state === "running");
      if (!running) break;
      await new Promise((r) => setTimeout(r, 1000));
    }
  } finally { polling = false; refresh(); }
}
function engineForm() {
  const m = $("input[name=mode]:checked")?.value || "";
  const e = { mode: m === "hosted" ? "external" : m };
  if (e.mode === "external") {
    e.base_url = $("#baseUrl").value.trim(); e.model = $("#modelSelect").value;
    if ($("#apiKey").value) e.api_key = $("#apiKey").value;
  } else if (m === "bundled") { e.bundled_model = chosenBundled; }
  return e;
}
$("#saveEngine").onclick = async () => {
  const e = engineForm();
  if (e.mode === "bundled" && !S.bundled.models.includes(e.bundled_model)) { $("#testResult").textContent = "Download the model first."; return; }
  if (e.mode === "external" && !e.base_url) { $("#testResult").textContent = "Enter the server address."; return; }
  if ($("input[name=mode]:checked")?.value === "hosted" && !e.model) { $("#testResult").textContent = "Choose a model: press Refresh, or type its name."; return; }
  S = await api("/api/settings", { method: "POST", body: { engine: e } });
  await refresh();
  $("#testResult").textContent = "Saved. Testing…";
  testEngine();
};
async function testEngine() {
  $("#testResult").textContent = "Testing… (the built-in engine can take a minute to load the model)";
  const r = await api("/api/test-engine", { method: "POST" });
  $("#testResult").textContent = r.ok ? `✓ Working: ${r.model} replied “${r.reply}”` : `✗ ${r.error}`;
}
$("#testEngine").onclick = async () => { await api("/api/settings", { method: "POST", body: { engine: engineForm() } }); await refresh(); testEngine(); };
$("#saveInstructions").onclick = async () => {
  const v = $("#instructions").value.trim();
  await api("/api/settings", { method: "POST", body: { instructions: v === S.default_instructions.trim() ? "" : v } });
  await refresh(); dlgS.close();
};
$("#resetInstructions").onclick = () => { $("#instructions").value = S.default_instructions; };
$("#saveNotes").onclick = async () => { await api("/api/settings", { method: "POST", body: { project_notes: $("#projectNotes").value } }); await refresh(); dlgS.close(); };
$("#checkUpdates").onchange = (e) => api("/api/settings", { method: "POST", body: { check_updates: e.target.checked } });
$("#checkNow").onclick = async () => { $("#updateView").textContent = "Checking…"; S.updates = await api("/api/updates/check", { method: "POST" }); renderAbout(); refresh(); };
function renderAbout() {
  const L = S.library;
  $("#aboutInfo").innerHTML = `<p class="about"><b>Para ${esc(S.version)}</b><br><span class="meta">Regulations library ${esc(L.version || "not installed")} · ${L.sources.length} documents</span></p>
    <p class="muted small">Copyright (C) 2026 Tomás Val. Para is free software under the GNU Affero General Public License, version 3 or later, and comes with no warranty. <a href="https://github.com/valtomas/para-building-regs" data-ext>Source code and licence</a>. Library content is Crown copyright under the Open Government Licence v3.0.</p>`;
  const u = S.updates || {};
  let h = "";
  if (u.error) h += `<p class="muted small">Couldn't check for updates: ${esc(u.error)}</p>`;
  if (u.app) h += `<p class="update-row">Version ${esc(u.app.version)} is available. <button type="button" class="btn" id="getApp">Download</button></p>`;
  if (u.library) h += `<p class="update-row">A newer regulations library (${esc(u.library.version)}, ${(u.library.size / 2 ** 20).toFixed(0)} MB) is available. <button type="button" class="btn primary" id="getLib">Install</button></p><div id="libJob"></div>`;
  if (u.checked && !u.app && !u.library && !u.error) h += `<p class="muted small">You're up to date.</p>`;
  $("#updateView").innerHTML = h;
  const ga = $("#getApp"); if (ga) ga.onclick = () => openUrl(u.app.url);
  const gl = $("#getLib"); if (gl) gl.onclick = async () => { await api("/api/updates/library", { method: "POST" }); pollJobs(); };
}
function renderLibraryJob(j) {
  const v = $("#libJob"); if (!v) return;
  const pct = j.total ? Math.round(j.done / j.total * 100) : 0;
  v.innerHTML = j.state === "running" ? `<div class="progress"><div style="width:${pct}%"></div></div>` : j.state === "error" ? `<div class="error">${esc(j.error)}</div>` : `<p class="ok-line">Library updated.</p>`;
}

// ---------- files ----------
const dlgF = $("#dlg-files");
function openFiles() { renderFiles(); dlgF.showModal(); api("/api/files/sync", { method: "POST" }).then((r) => { S.files = r.files; renderFiles(); }); }
function renderFiles() { renderFileList($("#fileList"), S.files.filter((f) => !f.project)); }
function renderFileList(list, files) {
  if (!files.length) { list.innerHTML = `<p class="muted small">No files yet.</p>`; return; }
  list.innerHTML = files.map((f) => `<div class="file-row">
    <div class="info"><div class="name">${esc(f.title)}</div>
      <div class="fstate ${esc(f.status)}">${f.status === "ok" ? `${f.passages} passages` : f.status === "reading" ? "Reading…" : f.status === "empty" ? "No text found (a scan?)" : esc(f.status)}</div>
      <input data-note="${esc(f.key)}" value="${esc(f.note || "")}" placeholder="What is this? (helps the AI, e.g. 'Fire strategy rev C')"></div>
    <button class="btn" data-openfile="${esc(f.key)}">Open</button>
    <button class="btn danger" data-delfile="${esc(f.key)}">Remove</button></div>`).join("");
  $$("[data-note]", list).forEach((i) => i.onchange = () => api("/api/files/note", { method: "POST", body: { key: i.dataset.note, note: i.value } }));
  $$("[data-openfile]", list).forEach((b) => b.onclick = () => api("/api/open", { method: "POST", body: { file: b.dataset.openfile } }));
  $$("[data-delfile]", list).forEach((b) => b.onclick = async () => {
    if (!confirm("Remove this file? It is deleted from your files folder too.")) return;
    S.files = (await api("/api/files?key=" + encodeURIComponent(b.dataset.delfile), { method: "DELETE" })).files;
    renderFiles(); if (dlgP.open) renderProjectFiles(); refresh();
  });
}
function permission() {
  if (S.settings.files_warning_ok) return Promise.resolve(true);
  const d = $("#dlg-permission");
  $("#permAgree").checked = false; $("#permOk").disabled = true;
  d.showModal();
  return new Promise((res) => {
    $("#permAgree").onchange = (e) => { $("#permOk").disabled = !e.target.checked; };
    $("#permOk").onclick = async () => { d.close(); await api("/api/settings", { method: "POST", body: { files_warning_ok: true } }); S.settings.files_warning_ok = true; res(true); };
    $("#permCancel").onclick = () => { d.close(); res(false); };
  });
}
// proj: the project the files go in ("" or null: shared by every chat)
async function upload(files, proj = "") {
  if (!files.length || !(await permission())) return;
  const list = proj ? $("#projectFileList") : $("#fileList");
  for (const f of files) {
    const row = document.createElement("div"); row.className = "muted small"; row.textContent = `Adding ${f.name}…`;
    list.prepend(row);
    try {
      const r = await fetch("/api/files", { method: "POST", headers: { "X-Filename": encodeURIComponent(f.name), "X-Project": encodeURIComponent(proj || "") }, body: f });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error);
      S.files = data.files;
    } catch (e) { alert(`${f.name}: ${e.message}`); }
  }
  renderFiles(); if (dlgP.open) renderProjectFiles(); refresh();
}
$("#filePick").onchange = (e) => { upload([...e.target.files]); e.target.value = ""; };
$("#openFolder").onclick = (e) => { e.preventDefault(); api("/api/open", { method: "POST", body: { folder: "files" } }); };
function dropZone(dz, proj) {
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.remove("over"); upload([...e.dataTransfer.files], proj()); });
}
dropZone($("#dropZone"), () => "");
document.addEventListener("dragover", (e) => e.preventDefault());
// files dropped anywhere else go to the project being worked on, or to the shared files
document.addEventListener("drop", (e) => {
  if (e.target.closest(".drop")) return;
  e.preventDefault();
  if (!e.dataTransfer.files.length) return;
  if (project) { openProject(project); upload([...e.dataTransfer.files], project); }
  else { openFiles(); upload([...e.dataTransfer.files]); }
});

// ---------- library ----------
const dlgL = $("#dlg-library");
const KIND = { "approved-document": "Approved Documents", legislation: "Legislation", guidance: "Government guidance" };
function openLibrary() {
  const groups = {};
  for (const s of S.library.sources) (groups[s.kind] = groups[s.kind] || []).push(s);
  $("#libSources").innerHTML = S.library.sources.length ? Object.entries(groups).map(([k, items]) => `<div class="group-title">${esc(KIND[k] || k)}<span class="count">${items.length}</span></div>` +
    items.map((s) => {
      const dated = /^\d{4}-\d\d-\d\d$/.test(s.published || "");  // otherwise it's an edition: show it under the title
      const title = dated || !s.published ? s.title : s.title.replace(s.published, "").replace(/[\s,(]+\)?\s*$/, "");
      return `<div class="lib-source" data-key="${esc(s.key)}"><b>${esc(s.short)}</b><span>${esc(title)}${s.published && !dated ? `<i>${esc(s.published)}</i>` : ""}</span><small>${dated ? esc(s.published) : ""}</small></div>`; }).join("")).join("")
    : `<p class="muted">No regulations library is installed. Check for updates in Settings.</p>`;
  $$(".lib-source").forEach((el) => el.onclick = () => { dlgL.close(); openSource(el.dataset.key); });
  $("#libResults").innerHTML = "";
  $("#libSearch").value = "";
  dlgL.showModal();
}
let libTimer;
$("#libSearch").addEventListener("input", (e) => {
  clearTimeout(libTimer);
  libTimer = setTimeout(async () => {
    const qv = e.target.value.trim();
    $("#libSources").hidden = !!qv;
    if (!qv) { $("#libResults").innerHTML = ""; return; }
    const { results } = await api("/api/search?" + new URLSearchParams({ q: qv, project }));
    $("#libResults").innerHTML = results.length ? results.map((h) => `<div class="hit" data-hit="${esc(h.id)}"><div class="where">${esc(h.source)} ${esc(h.ref || "")}${h.heading ? " · " + esc(h.heading) : ""}</div>${esc(h.snippet)}</div>`).join("") : `<p class="muted">Nothing found.</p>`;
    $$("[data-hit]").forEach((el) => el.onclick = () => { dlgL.close(); openPassage(el.dataset.hit); });
  }, 250);
});

// ---------- no library yet (running from source) ----------
async function offerLibrary() {
  if (S.library.sources.length || $("#libOffer")) return;
  const w = $("#welcome"); if (!w) return;
  w.insertAdjacentHTML("afterbegin", `<div id="libOffer" class="note offer"><span class="eyebrow">Library</span>No regulations library is installed yet. <button class="btn primary" id="getLibNow">Download it (about 15 MB)</button><div id="libJob"></div></div>`);
  $("#getLibNow").onclick = async () => {
    try {
      if (!(S.updates && S.updates.library)) S.updates = await api("/api/updates/check", { method: "POST" });
      if (!S.updates.library) throw new Error(S.updates.error || "No library is published yet. Build one with library/build.py.");
      await api("/api/updates/library", { method: "POST" }); await pollJobs();
      if (S.library.sources.length) $("#libOffer").remove();
    } catch (e) { $("#libJob").textContent = e.message; }
  };
}

// ---------- phone menu (below 900 px the sidebar is a drawer) ----------
$("#menuBtn").onclick = () => $("#sidebar").classList.add("open");
$("#sidebar").addEventListener("click", (e) => { if (e.target.closest("button") && !e.target.closest("#projectBtn, .theme-pick")) $("#sidebar").classList.remove("open"); });

// ---------- start ----------
refresh().then(() => {
  offerLibrary();
  if (!S.settings.engine.mode) openSettings();
  const running = S.bundled.jobs.install && S.bundled.jobs.install.state === "running";
  if (running) pollJobs();
});
