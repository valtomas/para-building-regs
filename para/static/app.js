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
    .replace(/\[g?[rf]\d+\](?:[ ,;]*\[g?[rf]\d+\])*/g, citeRun);
}
// A run of citations, e.g. "[r12][r12]" or "[r12], [r13]": each label once, so a repeat doesn't show as two chips.
function citeRun(run) {
  const seen = new Set();
  return run.match(/g?[rf]\d+/g).filter((id) => !seen.has(citeLabel(id)) && seen.add(citeLabel(id))).map(citeChip).join(" ");
}
function citeLabel(id) {
  const c = citeInfo[id];
  return c ? `${c.source}${c.ref ? " " + c.ref : ""}` : id;
}
// What's to be said about a citation besides where it points: said in its tooltip and in the Cited list.
function citeState(c) {
  if (!c) return "";
  if (c.gone) return "No longer in the library";
  if (c.changed) return "Text changed since this answer";
  if (c.read === false) return "Extract only";
  return "";
}
function citeChip(id) {
  const c = citeInfo[id];
  const label = citeLabel(id), state = citeState(c);
  const tip = [state, c ? (c.heading || c.title || "") : "Open passage"].filter(Boolean).join(" · ");
  const cls = "cite" + (id.replace("g", "")[0] === "f" ? " file" : "") + (c?.gone ? " gone" : c?.changed ? " changed" : c?.read === false ? " extract" : "");
  // a passage that's gone opens nothing: its id now belongs to some other passage, or to none
  return c?.gone ? `<span class="${cls}" title="${esc(tip)}">${esc(label)}</span>`
    : `<button class="${cls}" data-pid="${esc(id)}" title="${esc(tip)}">${esc(label)}</button>`;
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
  renderWelcome();
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

let chatHits = null;     // chats found by the box above the list; null when it's empty
function renderChats() {
  const nav = $("#chatList");
  nav.innerHTML = "";
  if (chatHits && !chatHits.length) nav.innerHTML = `<p class="none-found">Nothing found.</p>`;
  for (const c of chatHits || S.chats.filter((c) => (c.project || "") === project)) {
    const row = document.createElement("div");
    row.className = "chat-item" + (c.id === chatId ? " active" : "");
    row.innerHTML = `<button class="open" title="${esc(c.title)}"><span>${esc(c.title || "Chat")}</span>${c.snippet ? `<small>${esc(c.snippet)}</small>` : ""}</button><button class="ren" title="Rename" aria-label="Rename chat"><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 10l.6-2.6L8.4 1.6l2 2L4.6 9.4z"/></svg></button><button class="del" title="Delete" aria-label="Delete chat"><svg class="x" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8"/></svg></button>`;
    $(".open", row).onclick = () => openChat(c.id);
    $(".ren", row).onclick = () => renameChat(row, c);
    $(".del", row).onclick = async () => {
      if (!(await confirmBox("Delete this chat?", "", "Delete"))) return;
      await api("/api/chat?id=" + encodeURIComponent(c.id), { method: "DELETE" });
      if (c.id === chatId) newChat();
      await refresh();
      findChats();
    };
    nav.appendChild(row);
  }
}
// The title becomes a box to type in: Enter saves, Escape or clicking away leaves it as it was.
function renameChat(row, c) {
  const input = document.createElement("input");
  input.value = c.title || ""; input.maxLength = 120; input.setAttribute("aria-label", "Chat name");
  $(".open", row).replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const title = input.value.trim();
    if (save && title && title !== c.title) {
      try { S.chats = (await api("/api/chat/rename", { method: "POST", body: { id: c.id, title } })).chats; } catch (e) { alert(e.message); }
    }
    if (chatHits) findChats(); else renderChats();
  };
  input.onkeydown = (e) => { if (e.key === "Enter") finish(true); else if (e.key === "Escape") { e.stopPropagation(); finish(false); } };
  input.onblur = () => finish(false);
}
let findTimer;
async function findChats() {
  const qv = $("#chatFind").value.trim();
  if (qv.length < 2) { chatHits = null; renderChats(); return; }
  chatHits = (await api("/api/chats/search?" + new URLSearchParams({ q: qv, project }))).chats;
  if ($("#chatFind").value.trim() === qv) renderChats();
}
$("#chatFind").addEventListener("input", () => { clearTimeout(findTimer); findTimer = setTimeout(findChats, 250); });

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
  $("#chatFind").value = ""; chatHits = null;
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

// What has been typed or chosen in a dialog since it was last saved (see "closing a dialog" at the end).
const unsaved = new Map();   // dialog -> the parts changed and not yet saved
function changed(dlg, part) { if (!unsaved.has(dlg)) unsaved.set(dlg, new Set()); unsaved.get(dlg).add(part); }
function saved(dlg, part) { if (part) unsaved.get(dlg)?.delete(part); else unsaved.delete(dlg); }
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
  if (!dlgP.open) { saved(dlgP); dlgP.showModal(); }
  if (!p) $("#projectName").focus();
  if (p) api("/api/files/sync", { method: "POST" }).then((r) => { S.files = r.files; renderProjectFiles(); });
}
function renderProjectFiles() {
  renderFileList($("#projectFileList"), S.files.filter((f) => f.project === editing));
}
// Saves the project; true if it was saved. After creating one the dialog stays open for its files, unless it's closing.
async function saveProject(closing = false) {
  const name = $("#projectName").value.trim(), notes = $("#projectNotesOne").value;
  try {
    const r = editing === null
      ? await api("/api/projects", { method: "POST", body: { name, notes } })
      : await api("/api/projects/update", { method: "POST", body: { name: editing, new_name: name, notes } });
    S = r;
    const created = editing === null;
    await setProject(r.project);
    saved(dlgP);
    if (created && !closing) openProject(r.project);   // carry on: add the project's files
    else dlgP.close();
    return true;
  } catch (e) { $("#projectMsg").textContent = e.message; return false; }
}
$("#projectSave").onclick = () => saveProject();
$("#projectDelete").onclick = async () => {
  if (!(await confirmBox(`Delete the project ${editing}?`, "Its chats are kept, outside any project.", "Delete"))) return;
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
  pickExamples();
  if (S) renderWelcome();
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
    const v = addAssistant(t.question);
    Object.assign(citeInfo, t.cites || {});
    for (const ev of t.events || []) v.activity(ev);
    if (t.error) v.error(t.error);
    v.finish({ answer: t.answer, cites: t.cites || {}, read: t.read || [], seen: t.seen || [], dropped: t.dropped || [], cut_off: t.cut_off, stopped: t.stopped, figures: t.figures || [] });
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

function addAssistant(question) {
  $$(".answer-actions .again").forEach((b) => b.remove());  // only the last answer can be asked again
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
  // Under the answer: copy it with its references, or ask the question again (the last one only).
  const actions = (done) => {
    if ($(".answer-actions", el)) return;
    const row = document.createElement("div");
    row.className = "answer-actions";
    if (done && (done.answer || "").trim()) {
      const copy = document.createElement("button");
      copy.type = "button"; copy.textContent = "Copy with references";
      copy.onclick = async () => { copy.textContent = (await copyAnswer(el, done)) ? "Copied" : "Couldn't copy"; setTimeout(() => { copy.textContent = "Copy with references"; }, 2000); };
      row.appendChild(copy);
    }
    if (question) {
      const again = document.createElement("button");
      again.type = "button"; again.className = "again"; again.textContent = "Ask again";
      again.title = "Ask this question again; the new answer replaces this one";
      again.onclick = () => { if (busy) return; el.previousElementSibling?.remove(); el.remove(); ask(question, true); };
      row.appendChild(again);
    }
    if (row.children.length) el.appendChild(row);
  };
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
        for (const r of ev.results || []) citeInfo[r.id] = r;
        li.innerHTML = `Searched “${esc(ev.query)}” · ${ev.results.length} result${ev.results.length === 1 ? "" : "s"}`;
      } else if (ev.type === "read" || ev.type === "show") {
        reads += ev.type === "read" ? 1 : 0;
        citeInfo[ev.id] = ev;
        for (const r of ev.nearby || []) citeInfo[r.id] = r;  // the passages read along with it
        const what = `${esc(ev.source)} ${esc(ev.ref || "")}${ev.heading ? " · " + esc(ev.heading) : ""}`;
        li.innerHTML = `${ev.type === "read" ? "Read" : "Showed"} ` + (ev.gone ? `${what} (no longer in the library)` : `<button class="src" data-pid="${esc(ev.id)}">${what}</button>`);
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
      actions(null);
    },
    finish(done) {
      ans.classList.remove("cursor");
      Object.assign(citeInfo, done.cites || {});
      ans.innerHTML = md(done.answer || text);
      const x = $(".extras", el);
      if (done.cut_off) x.insertAdjacentHTML("beforeend", `<div class="note">The answer hit the length limit. Ask it to continue, or raise Length below.</div>`);
      if (done.stopped) x.insertAdjacentHTML("beforeend", `<div class="note">Stopped.</div>`);
      if (!done.stopped && !(done.answer || text || "").trim()) x.insertAdjacentHTML("beforeend", `<div class="note">The AI wrote no answer. Ask again, or try another model in Settings.</div>`);
      if (done.dropped && done.dropped.length) x.insertAdjacentHTML("beforeend", `<div class="note">The AI cited ${done.dropped.length} passage${done.dropped.length > 1 ? "s" : ""} it never looked up, so ${done.dropped.length > 1 ? "they were" : "it was"} removed. Treat the uncited parts with care.</div>`);
      const cited = Object.keys(done.cites || {});
      const all = Object.values(done.cites || {}), n = (list, one, many) => `${list.length} ${list.length === 1 ? one : many}`;
      const extract = all.filter((c) => c.read === false && !c.gone), changed = all.filter((c) => c.changed && !c.gone), gone = all.filter((c) => c.gone);
      if (extract.length) x.insertAdjacentHTML("beforeend", `<div class="note">${n(extract, "citation rests", "citations rest")} on a search result's extract: the AI didn't read the whole passage. Open ${extract.length === 1 ? "it" : "them"} to check.</div>`);
      if ((done.figures || []).length) {
        const list = done.figures.map((f) => `${esc(f.figure)} (${f.ids.map(citeChip).join(" ")})`).join(", ");
        x.insertAdjacentHTML("beforeend", `<div class="note"><span>Not found in the passage cited for ${done.figures.length === 1 ? "it" : "them"}: ${list}. Only the numbers are compared, so check ${done.figures.length === 1 ? "this figure" : "these figures"} against the passage.</span></div>`);
      }
      if (changed.length || gone.length) x.insertAdjacentHTML("beforeend", `<div class="note">The library or your files have changed since this answer. ${[changed.length ? `${n(changed, "citation opens", "citations open")} a paragraph whose text has changed` : "", gone.length ? `${n(gone, "passage it cites is", "passages it cites are")} no longer there` : ""].filter(Boolean).join(", and ")}.</div>`);
      const read = (done.read || []).filter((r) => !cited.includes(r.id));
      if (cited.length || read.length) {
        const chips = cited.map((id) => chip(done.cites[id])).join("") + read.map(chip).join("");
        x.insertAdjacentHTML("beforeend", `<div class="sources"><span class="label">${cited.length ? "Cited" + (read.length ? " and read" : "") : "Read"}</span>${chips}</div>`);
      } else if ((done.answer || "").trim() && !done.stopped) {
        x.insertAdjacentHTML("beforeend", `<div class="note">No library passages were used for this answer: it comes from the AI's own knowledge. Check it against the documents.</div>`);
      }
      actions(done);
    },
  };
}
const chip = (c) => `<button class="source-chip${c.gone ? " gone" : ""}" ${c.gone ? "disabled" : `data-pid="${esc(c.id)}"`} title="${esc([citeState(c), c.heading || c.title || ""].filter(Boolean).join(" · "))}">${esc(c.source)}${c.ref ? " " + esc(c.ref) : ""}</button>`;

// ---------- copying an answer ----------
// For pasting into an email or a report: each citation written out as the document's own reference, and the
// references listed underneath with links to the originals. Copied as formatted text and as plain text.
function citeLink(c) {
  if (!c || !c.url) return "";
  return c.page && /\.pdf($|[?#])/i.test(c.url) ? c.url.split("#")[0] + "#page=" + c.page : c.url;
}
function answerCopy(el, done) {
  const cites = done.cites || {}, used = [];
  const label = (id) => { const c = cites[id]; return c ? `${c.source}${c.ref ? " " + c.ref : ""}` : ""; };
  const text = (done.answer || "").replace(/\[(g?[rf]\d+)\](?:[ ,;]*\[(g?[rf]\d+)\])*/g, (run) => {
    const labels = [...new Set(run.match(/g?[rf]\d+/g).filter((id) => cites[id] && (used.includes(id) || used.push(id))).map(label))];
    return labels.length ? `(${labels.join("; ")})` : "";
  }).replace(/\*\*([^*]+)\*\*/g, "$1").replace(/^#{1,4}\s+/gm, "").replace(/`([^`]+)`/g, "$1").replace(/[ \t]+([.,;:])/g, "$1");
  const clone = $(".answer", el).cloneNode(true);
  $$(".cite", clone).forEach((b) => {
    const c = cites[b.dataset.pid], url = citeLink(c), a = document.createElement(url ? "a" : "span");
    if (url) a.href = url;
    a.textContent = b.textContent;
    b.replaceWith("(", a, ")");
  });
  const refs = used.map((id) => cites[id]);
  const line = (c) => [`${c.source}${c.ref ? " " + c.ref : ""}`, c.heading, c.title !== c.source ? c.title : "", c.kind === "file" ? "one of my own files" : "", c.gone ? "no longer in the library" : ""].filter(Boolean).join(". ");
  const edition = refs.some((c) => c.kind !== "file") && S.library.version ? `Para regulations library, edition ${S.library.version}.` : "";
  const html = clone.innerHTML + (refs.length ? `<p><strong>References</strong></p><ul>${refs.map((c) => `<li>${esc(line(c))}${citeLink(c) ? ` <a href="${esc(citeLink(c))}">${esc(citeLink(c))}</a>` : ""}</li>`).join("")}</ul>${edition ? `<p>${esc(edition)}</p>` : ""}` : "");
  const plain = text.trim() + (refs.length ? "\n\nReferences\n" + refs.map((c) => `- ${line(c)}${citeLink(c) ? " " + citeLink(c) : ""}`).join("\n") + (edition ? "\n" + edition : "") : "");
  return { html, plain };
}
async function copyAnswer(el, done) {
  const { html, plain } = answerCopy(el, done);
  try {
    await navigator.clipboard.write([new ClipboardItem({ "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([plain], { type: "text/plain" }) })]);
    return true;
  } catch { }
  // windows that don't let a page write to the clipboard directly: select the formatted text and copy that
  const box = document.createElement("div");
  box.contentEditable = "true"; box.innerHTML = html;
  box.style.cssText = "position:fixed;left:-9999px;top:0;white-space:pre-wrap";
  document.body.appendChild(box);
  const range = document.createRange(); range.selectNodeContents(box);
  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { }
  sel.removeAllRanges(); box.remove();
  return ok;
}

// again: asked again from under the last answer, which the new one replaces
async function ask(question, again = false) {
  if (busy || !question.trim()) return;
  if (!S.settings.engine.mode) { openSettings(); return; }
  busy = true;
  $("#send").hidden = true; $("#stop").hidden = false;
  addUser(question);
  const v = addAssistant(question);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  setStatus("Thinking…");
  let finished = false;
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: chatId, question, project, again }) });
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
    refresh().then(() => { if (chatHits) findChats(); });
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
  if (pidEl) { e.preventDefault(); pidEl.closest("dialog")?.close(); openPassage(pidEl.dataset.pid); return; }
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

// ---------- the empty chat ----------
// What it offers depends on who is looking. Someone with no chats yet gets four questions to try, drawn from the ones
// the models are tested on (tests/ask_models.py), so a first answer is one we know comes out well. In a project the
// questions are about its files. Anyone who has asked before gets the documents by Part instead: a contents page
// doesn't wear out. After a library update, one line says so.
const EXAMPLES = [
  "What is the maximum travel distance in a single-direction escape route from a flat entrance door?",
  "Maximum rise, minimum going and maximum pitch for a private stair",
  "Guarding height for a balcony in a dwelling",
  "Limiting U-value for a new external wall in a new dwelling",
  "When is a building a higher-risk building during design and construction?",
  "What must a principal designer be competent to do?",
  "Does a bathroom in a flat need a fire door?",
  "At what height does a block of flats need sprinklers?",
  "Maximum travel distance in an office where escape is possible in one direction only",
  "Minimum size of an emergency escape window in a house",
  "What standard of fire alarm system does a new house need?",
  "Minimum extract rates for a kitchen and a bathroom with intermittent fans",
  "How much water may a new dwelling be designed to use per person?",
  "Maximum hot water temperature at a bath in a new dwelling",
  "What height should a handrail on a stair be?",
  "What headroom does a stair in a loft conversion need?",
  "What is the worst air permeability allowed for a new dwelling?",
  "Limiting U-value for windows in a new dwelling",
  "Maximum gradient for a ramp with a 5 m going to a building other than a dwelling",
  "Minimum rated output of an electric vehicle charge point",
  "How thick must a constructional hearth be?",
  "What airborne sound insulation must a separating wall between new houses achieve?",
  "What is the radon action level?",
  "What standard should the front door of a new dwelling meet for security?",
];
const PARTS = {
  A: "Structure", B: "Fire safety", C: "Site preparation and resistance to contaminants and moisture",
  D: "Toxic substances", E: "Resistance to sound", F: "Ventilation", G: "Sanitation, hot water safety and water efficiency",
  H: "Drainage and waste disposal", J: "Combustion appliances and fuel storage", K: "Protection from falling, collision and impact",
  L: "Conservation of fuel and power", M: "Access to and use of buildings", O: "Overheating", P: "Electrical safety",
  Q: "Security in dwellings", R: "Infrastructure for electronic communications", S: "Charging electric vehicles",
  T: "Toilet accommodation", 7: "Materials and workmanship",
};
let shownExamples = [];
function pickExamples() {
  const pool = [...EXAMPLES];
  shownExamples = Array.from({ length: 4 }, () => pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
}
function renderWelcome() {
  const w = welcomeEl;
  if (!shownExamples.length) pickExamples();
  // after an update: once, until the list of changes has been opened
  const ch = S.library_changes, seen = (() => { try { return localStorage.getItem("para-changes-seen"); } catch { return null; } })();
  const news = $("#welcomeNews", w);
  if (ch && (ch.documents.length || ch.removed.length) && seen !== ch.to) {
    const paras = ch.documents.reduce((n, d) => n + (d.changed_count || 0) + (d.added_count || 0) + (d.removed_count || 0), 0);
    const docs = ch.documents.length + ch.removed.length;
    news.innerHTML = `<div class="note edition"><span>The library is now edition ${esc(ch.to)}: ${paras ? `${paras} paragraph${paras === 1 ? "" : "s"} differ in ` : ""}${docs} document${docs === 1 ? "" : "s"}.</span><button class="link-btn" id="seeChanges">See what changed</button></div>`;
    $("#seeChanges").onclick = () => { try { localStorage.setItem("para-changes-seen", ch.to); } catch { /* shown again next time */ } news.innerHTML = ""; openLibrary(true); };
  } else news.innerHTML = "";

  const start = $("#welcomeStart", w);
  const files = project ? S.files.filter((f) => f.project === project && f.status !== "reading") : [];
  const named = (f) => f.title.replace(/\.[a-z0-9]{2,5}$/i, "");
  let questions = [], label = "";
  if (project && files.length) {
    label = `In ${project}`;
    questions = files.slice(0, 3).map((f) => `What does “${named(f)}” ask for or say, and which requirements does it touch?`);
    if (files.length > 1) questions.push("Which parts of the regulations do this project's files raise?");
  } else if (!S.chats.length) { label = "Try one"; questions = shownExamples; }
  start.innerHTML = (project ? `<p class="welcome-project">${files.length ? `${files.length} file${files.length === 1 ? "" : "s"} searched only in this project's chats.` : "No files in this project yet."} <button class="link-btn" id="welcomeProject">${files.length ? "Notes and files" : "Add drawings, specifications or comments"}</button></p>` : "") +
    (questions.length ? `<div class="eyebrow">${esc(label)}</div><div class="examples">${questions.map((q) => `<button>${esc(q)}</button>`).join("")}</div>` : "");
  $$(".examples button", start).forEach((b) => b.onclick = () => ask(b.textContent));
  if (project) $("#welcomeProject").onclick = () => openProject(project);

  // the documents by Part: the Approved Documents themselves (not their FAQs or amendment slips), then the law
  const parts = $("#welcomeParts", w), by = {};
  for (const s of S.library.sources) {
    const m = /^AD([A-T7])([12])?$/.exec(s.key);
    if (m) (by[m[1]] = by[m[1]] || []).push({ key: s.key, vol: m[2] });
  }
  const law = S.library.sources.filter((s) => s.kind === "legislation");
  const rows = Object.keys(PARTS).filter((p) => by[p]).sort((a, b) => (a === "7") - (b === "7") || a.localeCompare(b)).map((p) => {
    const docs = by[p].sort((a, b) => (a.vol || "").localeCompare(b.vol || ""));
    const open = docs.length === 1 ? `<button class="part-open" data-key="${esc(docs[0].key)}">Open</button>`
      : docs.map((d) => `<button class="part-open" data-key="${esc(d.key)}" title="${d.vol === "1" ? "Dwellings" : "Buildings other than dwellings"}">Vol ${d.vol}</button>`).join("");
    return `<div class="part"><span class="part-letter">${p === "7" ? "Reg 7" : p}</span><span class="part-name">${esc(PARTS[p])}</span><span class="part-links">${open}</span></div>`;
  }).join("");
  parts.innerHTML = rows ? `<div class="eyebrow">The Approved Documents, by Part</div><div class="parts">${rows}</div>` +
    (law.length ? `<div class="eyebrow">Acts and regulations</div><div class="law">${law.map((s) => `<button class="part-open" data-key="${esc(s.key)}" title="${esc(s.title)}">${esc(s.short)}</button>`).join("")}<button class="link-btn" id="wholeLibrary">The whole library</button></div>` : "") : "";
  $$("[data-key]", parts).forEach((b) => b.onclick = () => openSource(b.dataset.key));
  if ($("#wholeLibrary")) $("#wholeLibrary").onclick = () => openLibrary();
}

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
  if (!dlgS.open) { saved(dlgS); dlgS.showModal(); }
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
// The same rule as llm.is_local: whole addresses only, since "10.example.com" is a name anyone can register.
function isLocal(u) {
  try { return /^(localhost|((127|10)\.\d+|192\.168|169\.254|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+|0\.0\.0\.0|\[(::1|f[cd][0-9a-f]{2}:.*|fe[89ab][0-9a-f]:.*)\])$|\.local$/.test(new URL(u).hostname); } catch { return true; }
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
      <small class="muted">${esc(m.about)}</small><small class="meta">${m.size_gb} GB download · needs ${m.min_ram_gb} GB memory${m.tested ? ` · ${esc(m.tested)} test questions right` : ""}${tooBig ? " — too big for this computer" : ""}</small></div>
      ${installed ? `<span class="installed">Installed</span> <button type="button" class="btn danger" data-remove="${esc(m.id)}">Delete</button>`
        : `<button type="button" class="btn" data-install="${esc(m.id)}">Download</button>`}
    </label>`;
  }).join("");
  $$("input[name=bmodel]").forEach((r) => r.onchange = () => { chosenBundled = r.value; renderBundled(); });
  $$("[data-install]").forEach((x) => x.onclick = async (e) => {
    e.preventDefault();
    try { await api("/api/bundled/install", { method: "POST", body: { model: x.dataset.install, gpu: $("#gpu").checked } }); chosenBundled = x.dataset.install; changed(dlgS, "engine"); pollJobs(); }
    catch (err) { alert(err.message); }
  });
  $$("[data-remove]").forEach((x) => x.onclick = async (e) => {
    e.preventDefault();
    if (!(await confirmBox("Delete this model?", "It is deleted from this computer. You can download it again.", "Delete"))) return;
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
      const { bundled, library_job, app_job } = await api("/api/jobs");
      S.bundled = bundled; S.app_job = app_job;
      if (!$("#bundledPane").hidden) renderBundled();
      if (library_job) renderLibraryJob(library_job);
      if (app_job) renderAppJob(app_job);
      const running = [bundled.jobs.install, library_job, app_job].some((j) => j && j.state === "running");
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
// Each of these saves one part of Settings and says whether it did (the engine isn't saved while something is missing).
async function saveEngine() {
  const e = engineForm();
  if (e.mode === "bundled" && !S.bundled.models.includes(e.bundled_model)) { $("#testResult").textContent = "Download the model first."; return false; }
  if (e.mode === "external" && !e.base_url) { $("#testResult").textContent = "Enter the server address."; return false; }
  if ($("input[name=mode]:checked")?.value === "hosted" && !e.model) { $("#testResult").textContent = "Choose a model: press Refresh, or type its name."; return false; }
  S = await api("/api/settings", { method: "POST", body: { engine: e } });
  await refresh();
  saved(dlgS, "engine");
  $("#testResult").textContent = "Saved. Testing…";
  testEngine();
  return true;
}
$("#saveEngine").onclick = saveEngine;
async function testEngine() {
  $("#testResult").textContent = "Testing… (the built-in engine can take a minute to load the model)";
  const r = await api("/api/test-engine", { method: "POST" });
  $("#testResult").textContent = r.ok ? `✓ Working: ${r.model} replied “${r.reply}”` : `✗ ${r.error}`;
}
$("#testEngine").onclick = async () => { await api("/api/settings", { method: "POST", body: { engine: engineForm() } }); await refresh(); saved(dlgS, "engine"); testEngine(); };
async function saveInstructions() {
  const v = $("#instructions").value.trim();
  await api("/api/settings", { method: "POST", body: { instructions: v === S.default_instructions.trim() ? "" : v } });
  await refresh(); saved(dlgS, "instructions");
  return true;
}
async function saveNotes() {
  await api("/api/settings", { method: "POST", body: { project_notes: $("#projectNotes").value } });
  await refresh(); saved(dlgS, "project");
  return true;
}
$("#saveInstructions").onclick = async () => { await saveInstructions(); tryClose(dlgS); };
$("#resetInstructions").onclick = () => { $("#instructions").value = S.default_instructions; changed(dlgS, "instructions"); };
$("#saveNotes").onclick = async () => { await saveNotes(); tryClose(dlgS); };
$("#checkUpdates").onchange = (e) => api("/api/settings", { method: "POST", body: { check_updates: e.target.checked } });
$("#checkNow").onclick = async () => { $("#updateView").textContent = "Checking…"; S.updates = await api("/api/updates/check", { method: "POST" }); renderAbout(); refresh(); };
function renderAbout() {
  const L = S.library;
  $("#aboutInfo").innerHTML = `<p class="about"><b>Para ${esc(S.version)}</b><br><span class="meta">Regulations library ${esc(L.version || "not installed")} · ${L.sources.length} documents</span></p>
    <p class="muted small">Copyright (C) 2026 Tomás Val. Para is free software under the GNU Affero General Public License, version 3 or later, and comes with no warranty. <a href="https://github.com/valtomas/para-building-regs" data-ext>Source code and licence</a>. Library content is Crown copyright under the Open Government Licence v3.0.</p>
    <p class="muted small feedback">A wrong answer, a citation that opens the wrong paragraph, or something confusing? Tell us at
      <a href="mailto:hello@pararegs.co.uk?subject=Para%20feedback" data-ext>hello@pararegs.co.uk</a>, with the
      question you asked.</p>`;
  const u = S.updates || {};
  let h = "";
  if (u.error) h += `<p class="muted small">Couldn't check for updates: ${esc(u.error)}</p>`;
  // a copy that can replace itself updates in one click; others (a zip, a folder, from source) get the release page
  if (u.app && u.app.update) h += `<p class="update-row">Version ${esc(u.app.version)} is available. <button type="button" class="btn primary" id="updateApp">Update now</button></p><div id="appJob"></div>`;
  else if (u.app) h += `<p class="update-row">Version ${esc(u.app.version)} is available. <button type="button" class="btn" id="getApp">Download</button></p>`;
  if (u.library) h += `<p class="update-row">A newer regulations library (${esc(u.library.version)}, ${(u.library.size / 2 ** 20).toFixed(0)} MB) is available. <button type="button" class="btn primary" id="getLib">Install</button></p><div id="libJob"></div>`;
  if (u.checked && !u.app && !u.library && !u.error) h += `<p class="muted small">You're up to date.</p>`;
  const ch = S.library_changes, differ = ch ? ch.documents.length + ch.removed.length : 0;
  if (differ) h += `<p class="update-row">This library edition differs from the one before in ${differ} document${differ === 1 ? "" : "s"}. <button type="button" class="btn" data-open="library">See what changed</button></p>`;
  $("#updateView").innerHTML = h;
  const ga = $("#getApp"); if (ga) ga.onclick = () => openUrl(u.app.url);
  const ua = $("#updateApp");
  if (ua) ua.onclick = async () => {
    ua.disabled = true;
    try { await api("/api/updates/app", { method: "POST" }); pollJobs(); }
    catch (e) { ua.disabled = false; $("#appJob").innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  };
  if (S.app_job) renderAppJob(S.app_job);
  const gl = $("#getLib"); if (gl) gl.onclick = async () => { await api("/api/updates/library", { method: "POST" }); pollJobs(); };
}
function renderAppJob(j) {
  const v = $("#appJob"); if (!v) return;
  const pct = j.total ? Math.round(j.done / j.total * 100) : 0;
  if ($("#updateApp")) $("#updateApp").disabled = j.state !== "error";
  v.innerHTML = j.state === "running" ? `<div class="progress"><div style="width:${pct}%"></div></div>${j.note ? `<p class="muted small">${esc(j.note)}</p>` : ""}`
    : j.state === "error" ? `<div class="error">${esc(j.error)}</div>` : `<p class="ok-line">${esc(j.note)}</p>`;
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
    if (!(await confirmBox("Remove this file?", "It is deleted from your files folder too.", "Remove"))) return;
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
// What differs from the edition this computer had before, worked out when the update was installed.
function libraryChanges() {
  const ch = S.library_changes;
  if (!ch || !(ch.documents.length || ch.removed.length)) return "";
  const name = (p) => esc(p.ref || p.heading || "untitled passage");
  const list = (d, kind, word) => {
    const items = d[kind] || [];
    if (!items.length) return "";
    const more = d[kind + "_count"] - items.length;
    return `<div class="chg-line"><span>${word}</span><span>${items.map((p) => p.id ? `<button class="src" data-pid="${esc(p.id)}" title="${esc(p.heading || "")}">${name(p)}</button>` : name(p)).join(", ")}${more > 0 ? `, and ${more} more` : ""}</span></div>`;
  };
  const docs = ch.documents.map((d) => `<div class="chg-doc"><div class="chg-head"><b>${esc(d.short)}</b><span>${esc(d.title)}</span></div>` +
    (d.new ? `<div class="chg-line"><span>New</span><span>Added to the library in this edition.</span></div>`
      : (d.was ? `<div class="chg-line"><span>Edition</span><span>${esc(d.was)} before, now ${esc(d.published || "undated")}.</span></div>` : "") +
        list(d, "changed", "Changed") + list(d, "added", "Added") + list(d, "removed", "Removed")) + `</div>`).join("") +
    ch.removed.map((d) => `<div class="chg-doc"><div class="chg-head"><b>${esc(d.short)}</b><span>${esc(d.title)}</span></div><div class="chg-line"><span>Removed</span><span>No longer in the library.</span></div></div>`).join("");
  const count = ch.documents.length + ch.removed.length;
  return `<details class="lib-changes"><summary>Changed in this edition: ${count} document${count === 1 ? "" : "s"}</summary>${docs}` +
    `<p class="who">Edition ${esc(ch.to)} compared, paragraph by paragraph, with ${esc(ch.from || "the one before")}, which this computer had until the update. A difference can also be a correction to how the app reads a document, not a change to the document.</p></details>`;
}
function openLibrary(changesOpen = false) {
  const groups = {};
  for (const s of S.library.sources) (groups[s.kind] = groups[s.kind] || []).push(s);
  $("#libSources").innerHTML = S.library.sources.length ? libraryChanges() + Object.entries(groups).map(([k, items]) => `<div class="group-title">${esc(KIND[k] || k)}<span class="count">${items.length}</span></div>` +
    items.map((s) => {
      const dated = /^\d{4}-\d\d-\d\d$/.test(s.published || "");  // otherwise it's an edition: show it under the title
      const title = dated || !s.published ? s.title : s.title.replace(s.published, "").replace(/[\s,(]+\)?\s*$/, "");
      return `<div class="lib-source" data-key="${esc(s.key)}"><b>${esc(s.short)}</b><span>${esc(title)}${s.published && !dated ? `<i>${esc(s.published)}</i>` : ""}</span><small>${dated ? esc(s.published) : ""}</small></div>`; }).join("")).join("")
    : `<p class="muted">No regulations library is installed. Check for updates in Settings.</p>`;
  $$(".lib-source").forEach((el) => el.onclick = () => { dlgL.close(); openSource(el.dataset.key); });
  $("#libResults").innerHTML = "";
  $("#libSearch").value = "";
  if (changesOpen && $(".lib-changes", dlgL)) $(".lib-changes", dlgL).open = true;
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

// ---------- closing a dialog ----------
// The cross, Escape and a click outside the dialog all close it the same way. Settings and Project hold things that
// are only kept on Save, so what has been typed or chosen since the last save is remembered, and closing asks first.
const SAVE = { engine: saveEngine, instructions: saveInstructions, project: saveNotes, "this project": () => saveProject(true) };

// The app's own "are you sure": a question, a line of what follows, Cancel and the action. Escape is Cancel.
function confirmBox(question, detail, action) {
  const d = $("#dlg-confirm");
  $("h2", d).textContent = question;
  $("p", d).textContent = detail; $("p", d).hidden = !detail;
  $("[value=yes]", d).textContent = action;
  return new Promise((res) => {
    $$("button", d).forEach((b) => b.onclick = (e) => { e.preventDefault(); res(b.value === "yes"); d.close(); });
    d.onclose = () => res(false);
    d.showModal();
    $("[value=no]", d).focus();   // so Enter doesn't delete
  });
}
function askUnsaved() {
  const d = $("#dlg-unsaved");
  return new Promise((res) => {
    $$("button", d).forEach((b) => b.onclick = (e) => { e.preventDefault(); res(b.value); d.close(); });
    d.onclose = () => res("keep");   // Escape: carry on editing (after a button this changes nothing)
    d.showModal();
  });
}
async function tryClose(dlg) {
  const parts = [...(unsaved.get(dlg) || [])];
  if (parts.length) {
    const answer = await askUnsaved();
    if (answer === "keep") return;
    if (answer === "save") {
      for (const part of parts) {
        if (await SAVE[part]()) continue;
        if (dlg === dlgS) showTab(part);   // something is missing: show where, and stay
        return;
      }
    }
    saved(dlg);
  }
  if (dlg.open) dlg.close();
}
for (const dlg of [dlgS, dlgP, dlgF, dlgL]) {
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); tryClose(dlg); });
  $(".modal-head", dlg).addEventListener("submit", (e) => { e.preventDefault(); tryClose(dlg); });
  // a click outside: pressed and let go on the backdrop, so selecting text and letting go outside doesn't close it
  let pressedOutside = false;
  const outside = (e) => {
    const r = dlg.getBoundingClientRect();
    return e.target === dlg && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom);
  };
  dlg.addEventListener("mousedown", (e) => { pressedOutside = outside(e); });
  dlg.addEventListener("click", (e) => { if (pressedOutside && outside(e)) tryClose(dlg); pressedOutside = false; });
}
dlgS.addEventListener("input", (e) => {
  const pane = e.target.closest(".tabpane")?.dataset.pane;
  if (pane in SAVE) changed(dlgS, pane);   // not Updates & about: its tick box saves itself
});
dlgS.addEventListener("change", (e) => { if (e.target.closest("[data-pane=engine]")) changed(dlgS, "engine"); });
for (const id of ["#projectName", "#projectNotesOne"]) $(id).addEventListener("input", () => changed(dlgP, "this project"));
