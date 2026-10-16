"use strict";

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const $ = (id) => document.getElementById(id);
const lang = () => $("lang").value;

const S = {
  docId: null,
  pdf: null,
  numPages: 0,
  scale: 1.4,
  zoom: { mode: "width", pct: 100 },  // see "PDF zoom"
  // per page: {el, w1, h1 (size in points), canvas, textEl, rendered, gen, segs, translated, trLoading}
  pages: [],
  pageObs: null,  // IntersectionObserver for rendering/translating
  dropObs: null,  // IntersectionObserver that frees the drawing of pages far from the view
  anatomyLoaded: false,
  anatomyModel: null,
  summaryLoaded: false,
  trToken: 0,
  aiConfig: null,
  rateLimited: null,
};

class RateLimited extends Error {}

const TASK_LABELS = { translate: "Translation", summary: "Summary", anatomy: "Anatomy",
  chat: "Chat", explain: "Explain", equation: "Equations", brief: "Glossary", review: "Review" };

function showRateLimit() {
  $("ai-banner").textContent = "구독 사용 한도에 도달했습니다. 남은 번역은 원문으로 표시합니다. 잠시 후 다시 번역하세요.";
  show($("ai-banner"));
}

async function loadConfig() {
  const res = await fetch("/api/config", { cache: "no-store" });
  S.aiConfig = res.ok ? await res.json() : null;
  const backend = S.aiConfig && S.aiConfig.backend;
  const row = backend && S.aiConfig.backends.find((b) => b.id === backend);
  $("ai-badge").textContent = "AI: " + (row ? row.label : "not set");
  if (!backend) openSettings();
}

const chatGPT = {
  busy: false, status: null, generation: 0, initialModels: false,
  focusReturn: null, pendingAccountId: null,
};

function setChatGPTBusy(busy) {
  const controls = ["chatgpt-account", "chatgpt-login", "chatgpt-reconnect", "chatgpt-refresh", "chatgpt-logout"];
  if (busy) {
    const active = document.activeElement;
    chatGPT.focusReturn = controls.map((id) => $(id)).includes(active) ? active : null;
  }
  chatGPT.busy = busy;
  for (const id of controls) {
    $(id).disabled = busy;
  }
  $("chatgpt-settings").setAttribute("aria-busy", String(busy));
  if (!busy) {
    const target = chatGPT.focusReturn;
    chatGPT.focusReturn = null;
    if (target && document.activeElement === document.body && document.hasFocus()
        && target.isConnected && !target.disabled && !target.classList.contains("hidden")) target.focus();
  }
}

function chatGPTError(err) {
  $("chatgpt-error").textContent = err.message;
  show($("chatgpt-error"));
}

function clearChatGPTLink() {
  hide($("chatgpt-link-row"));
  $("chatgpt-link").removeAttribute("href");
}

function renderChatGPTStatus(status) {
  chatGPT.status = status;
  if (status.ready) clearChatGPTLink();
  $("chatgpt-status").textContent = status.ready
    ? "Connected to ChatGPT" + (status.email ? ": " + status.email : "")
    : status.connected ? "Sign in again to finish connecting your ChatGPT subscription."
    : "Sign in with your ChatGPT subscription to connect.";
  const accounts = status.accounts || [];
  const select = $("chatgpt-account");
  select.innerHTML = "";
  for (const account of accounts) {
    const option = document.createElement("option");
    option.value = account.account_id; option.textContent = account.label || account.email || "ChatGPT account";
    select.appendChild(option);
  }
  const pendingAccount = accounts.find((account) =>
    account.account_id === chatGPT.pendingAccountId && account.provisional);
  if (pendingAccount) select.value = pendingAccount.account_id;
  else {
    chatGPT.pendingAccountId = null;
    if (status.account_id || status.active) select.value = status.account_id || status.active;
  }
  (accounts.length > 1 ? show : hide)($("chatgpt-account-label"));
  (select.value ? show : hide)($("chatgpt-reconnect"));
  (status.connected ? show : hide)($("chatgpt-logout"));
  $("chatgpt-login").textContent = status.connected ? "Add new ChatGPT account" : "Continue with ChatGPT";
}

async function getChatGPTJSON(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error("Could not load the ChatGPT connection. Refresh to try again.");
    return await response.json();
  } finally { clearTimeout(timeout); }
}

async function refreshChatGPT() {
  if (chatGPT.busy || $("set-backend").value !== "chatgpt" || !$("settings").open) return;
  setChatGPTBusy(true);
  const generation = chatGPT.generation;
  hide($("chatgpt-error"));
  try {
    const status = await getChatGPTJSON("/api/chatgpt/status");
    if (generation !== chatGPT.generation) return;
    renderChatGPTStatus(status);
    if (status.ready) {
      const data = await getChatGPTJSON("/api/chatgpt/models");
      if (generation !== chatGPT.generation) return;
      const models = data.models || [];
      const list = $("suggest-chatgpt");
      list.innerHTML = "";
      for (const model of models) {
        const option = document.createElement("option");
        option.value = model.slug; option.label = model.display_name || model.slug; list.appendChild(option);
      }
      if (chatGPT.initialModels && models.length) {
        const available = new Set(models.map((model) => model.slug));
        const defaults = (S.aiConfig.default_models || {}).chatgpt || {};
        for (const input of $("set-models").querySelectorAll("input[data-task]")) {
          if (!input.dataset.edited && input.value === defaults[input.dataset.task] && !available.has(input.value)) {
            input.value = models[0].slug;
          }
        }
        chatGPT.initialModels = false;
      }
    }
  } catch (err) {
    if (generation === chatGPT.generation) chatGPTError(err);
  } finally {
    setChatGPTBusy(false);
    if (generation !== chatGPT.generation) refreshChatGPT();
  }
}

async function changeChatGPTAccount(path, accountId) {
  if (chatGPT.busy) {
    if (chatGPT.status) $("chatgpt-account").value = chatGPT.status.account_id || chatGPT.status.active || "";
    return;
  }
  setChatGPTBusy(true);
  clearChatGPTLink();
  hide($("chatgpt-error"));
  let remoteRevoked = true;
  let failure = null;
  try {
    const result = await postJSON(path, { account_id: accountId });
    remoteRevoked = result.remote_revoked !== false;
  } catch (err) { failure = err; }
  finally { setChatGPTBusy(false); }
  await refreshChatGPT();
  if (failure) chatGPTError(failure);
  else if (path.endsWith("/logout") && !remoteRevoked) {
    $("chatgpt-status").textContent += " Signed out locally; the remote session could not be revoked.";
  }
}

function selectChatGPTAccount(accountId) {
  const account = (chatGPT.status?.accounts || []).find((item) => item.account_id === accountId);
  if (account?.provisional) {
    chatGPT.pendingAccountId = accountId;
    return;
  }
  chatGPT.pendingAccountId = null;
  changeChatGPTAccount("/api/chatgpt/select", accountId);
}

$("chatgpt-refresh").addEventListener("click", refreshChatGPT);
$("chatgpt-account").addEventListener("change", (e) => selectChatGPTAccount(e.target.value));
$("chatgpt-logout").addEventListener("click", () => changeChatGPTAccount("/api/chatgpt/logout", $("chatgpt-account").value || null));
async function loginChatGPT(accountId, newAccount = false) {
  if (chatGPT.busy) return;
  clearChatGPTLink();
  const generation = chatGPT.generation;
  const popup = window.open("about:blank", "_blank");
  if (popup) popup.opener = null;
  setChatGPTBusy(true); hide($("chatgpt-error"));
  try {
    const result = await postJSON("/api/chatgpt/login", { account_id: accountId, new_account: newAccount });
    const url = new URL(result.auth_url || result.url);
    if (url.protocol !== "https:") throw new Error("Could not open ChatGPT sign-in.");
    if (generation !== chatGPT.generation) { if (popup) popup.close(); return; }
    $("chatgpt-link").href = url.href; show($("chatgpt-link-row"));
    if (popup) popup.location.href = url.href;
    $("chatgpt-status").textContent = "Finish signing in in the new tab, then refresh the connection.";
  } catch (err) { if (popup) popup.close(); chatGPTError(err); }
  finally { setChatGPTBusy(false); }
}
$("chatgpt-login").addEventListener("click", () => loginChatGPT(null, chatGPT.status?.connected === true));
$("chatgpt-reconnect").addEventListener("click", () => {
  const accountId = $("chatgpt-account").value;
  if (accountId) loginChatGPT(accountId);
});
window.addEventListener("focus", refreshChatGPT);

function fillModels(backend) {
  clearChatGPTLink();
  chatGPT.generation++;
  chatGPT.initialModels = backend === "chatgpt";
  (backend === "chatgpt" ? show : hide)($("chatgpt-settings"));
  (backend === "chatgpt" ? show : hide)($("chatgpt-save-hint"));
  const box = $("set-models");
  box.innerHTML = "";
  const row = S.aiConfig.backends.find((b) => b.id === backend);
  const listId = "suggest-" + backend;
  const list = document.createElement("datalist"); list.id = listId;
  for (const m of row.suggestions) { const o = document.createElement("option"); o.value = m; list.appendChild(o); }
  box.appendChild(list);
  for (const task of S.aiConfig.tasks) {
    const label = document.createElement("label");
    label.textContent = TASK_LABELS[task] || task;
    const input = document.createElement("input");
    input.dataset.task = task; input.setAttribute("list", listId);
    input.value = S.aiConfig.models[backend][task];
    input.addEventListener("input", () => { input.dataset.edited = "true"; });
    label.appendChild(input); box.appendChild(label);
  }
  $("set-conc").value = S.aiConfig.concurrency[backend];
  $("set-provider").textContent = row.provider ? "Provider: " + row.provider : "";
}

function openSettings() {
  if (!S.aiConfig) return;
  const sel = $("set-backend");
  sel.innerHTML = "";
  for (const b of S.aiConfig.backends) {
    const o = document.createElement("option");
    o.value = b.id; o.textContent = b.label + (b.installed ? "" : " (not installed)");
    o.disabled = !b.installed; sel.appendChild(o);
  }
  const first = S.aiConfig.backend || (S.aiConfig.backends.find((b) => b.installed) || {}).id;
  if (first) { sel.value = first; fillModels(first); }
  $("settings-reason").textContent = S.aiConfig.reason || "";
  (S.aiConfig.reason ? show : hide)($("settings-reason"));
  hide($("settings-error"));
  $("settings").showModal();
  refreshChatGPT();
}

$("ai-badge").addEventListener("click", openSettings);
$("set-backend").addEventListener("change", (e) => { fillModels(e.target.value); refreshChatGPT(); });
$("settings-cancel").addEventListener("click", () => $("settings").close());
$("settings-form").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.isComposing) e.preventDefault();
});
$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const models = {};
  for (const input of $("set-models").querySelectorAll("input[data-task]")) models[input.dataset.task] = input.value.trim();
  $("settings-save").disabled = true; $("settings-save").textContent = "Testing…";
  try {
    await postJSON("/api/settings", { backend: $("set-backend").value, models, concurrency: parseInt($("set-conc").value, 10) });
    location.reload();  // every cache key and the pool size follow the new settings
  } catch (err) {
    $("settings-error").textContent = err.message; show($("settings-error"));
  } finally {
    $("settings-save").disabled = false; $("settings-save").textContent = "Save";
  }
});

// ================= Library =================
// Two sources: the papers folder, or the user's Zotero library (read-only, via Zotero's local API).
let libSource = "folder";
try { libSource = localStorage.getItem("libSource") || "folder"; } catch {}

function setLibSource(src) {
  libSource = src;
  try { localStorage.setItem("libSource", src); } catch {}
  $("src-folder").classList.toggle("active", src === "folder");
  $("src-zotero").classList.toggle("active", src === "zotero");
  $("zot-controls").classList.toggle("hidden", src !== "zotero");
  loadLibrary();
}
$("src-folder").addEventListener("click", () => setLibSource("folder"));
$("src-zotero").addEventListener("click", () => setLibSource("zotero"));

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

// Flat Zotero collections (each with a parent key) -> picker options in tree order, indented.
function zoteroCollectionOptions(cols) {
  const keys = new Set(cols.map((c) => c.key));
  const kids = new Map();
  for (const c of cols) {
    const parent = c.parent && keys.has(c.parent) ? c.parent : null;
    if (!kids.has(parent)) kids.set(parent, []);
    kids.get(parent).push(c);
  }
  const out = [];
  const walk = (parent, depth) => {
    const list = (kids.get(parent) || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    for (const c of list) {
      out.push({ key: c.key, label: "\u00a0\u00a0".repeat(depth) + c.name + " (" + c.count + ")" });
      walk(c.key, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

let zotCollectionsLoaded = false;
// Each fresh listing (new query, collection, or source) gets a generation number; a reply that
// arrives after a newer listing started, or after switching to Folder, is dropped.
let zotGen = 0;
let folderGen = 0;
async function loadZotero(start = 0) {
  const gen = start === 0 ? ++zotGen : zotGen;
  const stale = () => gen !== zotGen || libSource !== "zotero";
  const list = $("paperlist");
  if (start === 0) list.innerHTML = '<li class="emptylib">Loading Zotero…</li>';
  $("libdir").textContent = "Zotero · My Library";
  try {
    if (!zotCollectionsLoaded) {
      const sel = $("zot-coll");
      sel.innerHTML = '<option value="">All items</option>';
      const cols = (await getJSON("/api/zotero/collections")).collections;
      if (stale()) return;
      for (const o of zoteroCollectionOptions(cols)) {
        const opt = document.createElement("option");
        opt.value = o.key; opt.textContent = o.label;
        sel.appendChild(opt);
      }
      zotCollectionsLoaded = true;
    }
    const params = new URLSearchParams({ start: String(start) });
    if ($("zot-coll").value) params.set("collection", $("zot-coll").value);
    if ($("zot-q").value.trim()) params.set("q", $("zot-q").value.trim());
    const r = await getJSON("/api/zotero/items?" + params);
    if (stale()) return;
    if (start === 0) list.innerHTML = "";
    list.querySelector("li.more")?.remove();
    for (const it of r.items) {
      const who = it.authors.length > 2 ? it.authors[0] + " et al." : it.authors.join(", ");
      const li = document.createElement("li");
      li.innerHTML = `${esc(it.title)}<span class="meta">${esc([who, it.year].filter(Boolean).join(" · "))}</span>`;
      if (!it.pdfs.length) {
        li.classList.add("nopdf"); li.title = "No PDF attached in Zotero";
      } else {
        const button = document.createElement("button");
        button.type = "button"; button.className = "paper-action";
        button.innerHTML = li.innerHTML; li.innerHTML = "";
        button.addEventListener("click", () => openFromZotero(it.key, it.pdfs[0].key, li));
        li.appendChild(button);
      }
      list.appendChild(li);
      for (const pdf of it.pdfs.slice(1)) {  // extra PDFs (e.g. supplementary material)
        const sub = document.createElement("li");
        sub.className = "sub";
        const button = document.createElement("button");
        button.type = "button"; button.className = "paper-action";
        button.textContent = "↳ " + pdf.name;
        button.addEventListener("click", () => openFromZotero(it.key, pdf.key, sub));
        sub.appendChild(button);
        list.appendChild(sub);
      }
    }
    // "next" counts every Zotero entry read, including skipped notes and attachments.
    const next = r.next ?? start + r.items.length;
    if (next < r.total) {
      const more = document.createElement("li");
      more.className = "more";
      const button = document.createElement("button");
      button.type = "button"; button.className = "paper-action";
      button.textContent = `Load more (${next}/${r.total})`;
      button.addEventListener("click", () => {
        if (button.disabled) return;
        button.disabled = true;
        loadZotero(next);
      });
      more.appendChild(button);
      list.appendChild(more);
    } else if (!list.children.length) {
      list.innerHTML = '<li class="emptylib">No matching Zotero items.</li>';
    }
  } catch (err) {
    if (stale()) return;
    list.innerHTML = '<li class="emptylib">' + esc(err.message) + "</li>";
    zotCollectionsLoaded = false;
  }
}
$("zot-coll").addEventListener("change", () => loadZotero());
let zotSearchT;
$("zot-q").addEventListener("input", () => { clearTimeout(zotSearchT); zotSearchT = setTimeout(() => loadZotero(), 300); });

async function openFromZotero(item, attachment, li) {
  document.querySelectorAll(".paperlist li").forEach((x) => x.classList.remove("active"));
  if (li) li.classList.add("active");
  setTitle("Opening…");
  try {
    await openDoc(await postJSON("/api/zotero/open", { item, attachment }));
  } catch (err) { setTitle("Open failed: " + err.message); }
}

async function loadLibrary() {
  if (libSource === "zotero") return loadZotero();
  const gen = ++folderGen;
  const stale = () => gen !== folderGen || libSource !== "folder";
  const list = $("paperlist");
  list.innerHTML = '<li class="emptylib">Loading…</li>';
  try {
    const r = await fetch("/api/papers").then((x) => x.json());
    if (stale()) return;
    $("libdir").textContent = r.dir;
    if (!r.papers.length) {
      list.innerHTML = '<li class="emptylib">No PDFs here yet.<br>Drop .pdf files in the folder, then ⟳.</li>';
      return;
    }
    list.innerHTML = "";
    for (const p of r.papers) {
      const li = document.createElement("li");
      li.innerHTML = `${esc(p.name)}<span class="meta">${esc(p.file || "")} · ${fmtSize(p.size)}</span>`;
      li.addEventListener("click", () => openFromLibrary(p.rel, li));
      list.appendChild(li);
    }
  } catch (err) {
    if (stale()) return;
    list.innerHTML = '<li class="emptylib">Error: ' + err.message + "</li>";
  }
}
$("refresh").addEventListener("click", () => { zotCollectionsLoaded = false; loadLibrary(); });

async function openFromLibrary(rel, li) {
  document.querySelectorAll(".paperlist li").forEach((x) => x.classList.remove("active"));
  if (li) li.classList.add("active");
  setTitle("Opening…");
  try {
    const meta = await postJSON("/api/open", { rel });
    await openDoc(meta);
  } catch (err) { setTitle("Open failed: " + err.message); }
}

$("file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  setTitle("Uploading…");
  document.querySelectorAll(".paperlist li").forEach((x) => x.classList.remove("active"));
  const fd = new FormData(); fd.append("file", file);
  try {
    const meta = await postForm("/api/upload", fd);
    await openDoc(meta);
    loadLibrary();  // the upload is now also saved under papers/, so refresh the list
  } catch (err) { setTitle("Upload failed: " + err.message); }
});

// ================= Open document =================
async function openDoc(meta) {
  S.docId = meta.doc_id;
  S.numPages = meta.num_pages;
  setTitle(meta.title + (meta.byline ? "  ·  " + meta.byline : "") + "  ·  " + meta.num_pages + " pages");

  // Cover the whole workspace with the loading screen the instant the paper is opened, before
  // any await, so the PDF never flashes into view before it is ready. The warm-up below keeps it
  // up until the first page is translated and a few pages of layout are buffered, then hides it.
  const ov = $("prep-overlay");
  $("prep-fill").style.width = "0%";
  $("prep-text").textContent = "0%";
  ov.classList.remove("hidden");

  $("empty").classList.add("hidden");
  $("pdfcol").classList.remove("hidden");
  $("aipanel").classList.remove("hidden");
  $("summary").innerHTML = "";
  $("chatlog").innerHTML = "";
  S.summaryLoaded = false;
  S.anatomyLoaded = false; S.anatomyModel = null;
  updateReviewBtn();
  $("anatomy").innerHTML = '<p class="hint" style="padding:6px">Open the Anatomy tab to analyze the paper (grounded in the source).</p>';

  S.pdf = await pdfjsLib.getDocument("/api/pdf/" + meta.doc_id).promise;

  // page 1 sizes the fit modes (scale 1 = 1 CSS px per PDF point)
  const first = await S.pdf.getPage(1);
  const vw = first.getViewport({ scale: 1 });
  S.page1 = { w: vw.width, h: vw.height };
  S.scale = zoomScale();
  showZoom();

  await buildPageShells();  // MUST await: it fills S.pages (one getPage per page); translateAll's
  buildTrShells();          // producer reads S.pages[i], so it has to be fully built first.
  showPageNum();
  // Don't wait for the whole document's layout, or even for the whole first page. Reveal as soon
  // as (a) the FIRST translated sentence is painted and (b) a few pages of layout are buffered so
  // scrolling right away stays smooth. Translation (parallel pool) and layout prewarm both keep
  // running in the background after the reveal.
  const token = S.docId;
  const setBar = (f) => {
    const p = Math.round(100 * Math.max(0, Math.min(1, f)));
    $("prep-fill").style.width = p + "%";
    $("prep-text").textContent = p + "%";
  };
  setBar(0.1);
  const autotr = $("autotr").checked && !!(S.aiConfig && S.aiConfig.backend);
  // Reveal once the FIRST translated sentence is painted (not the whole first page). paintFrontier
  // fires S.onFirstPaint on the first paint; if auto-translate is off, don't gate on it.
  S.onFirstPaint = null;
  const firstSentence = autotr
    ? new Promise((resolve) => { S.onFirstPaint = () => { setBar(0.7); resolve(); }; })
    : Promise.resolve();
  if (autotr) translateAll();  // pool starts; page 0 is the frontier → first paint fires onFirstPaint
  if (S.aiConfig && S.aiConfig.backend) {
    genSummary();   // auto-generate the summary in the background
    loadAnatomy();  // auto-generate the Research Anatomy in the background
    loadChat();     // this paper's saved Chat History
  }
  await Promise.all([firstSentence, waitLayout(Math.min(3, S.numPages))]);
  if (S.docId !== token) return;
  setBar(1);
  await sleep(150);
  $("prep-overlay").classList.add("hidden");
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Matches a segment that is a bullet-list item (same glyphs the server splits on in pdf_utils).
const BULLET_RE = /^\s*[•◦▪▫●○‣⁃∙]/;

// Resolve once the server's background layout prewarm has at least `n` pages ready, so the first
// screens scroll smoothly. Bails out if the user opens another doc. Usually near-instant, because
// translating the first page already took longer than laying out a few pages.
async function waitLayout(n) {
  const token = S.docId;
  for (;;) {
    if (S.docId !== token) return;
    const s = await fetch(`/api/status/${token}`).then((r) => r.json()).catch(() => null);
    if (!s || !s.num_pages || s.finished || s.done >= n) return;
    await sleep(300);
  }
}

function resetObserver() {
  if (S.pageObs) S.pageObs.disconnect();
  if (S.dropObs) S.dropObs.disconnect();
  S.pageObs = new IntersectionObserver(onPageVisible, {
    root: $("pdfview"), rootMargin: "800px 0px", threshold: 0.01,
  });
  // At Retina density and a high zoom one page canvas holds tens of MB, so pages this far away
  // give their drawing back and are drawn again when they come near.
  S.dropObs = new IntersectionObserver(onPageFar, { root: $("pdfview"), rootMargin: "3000px 0px" });
}

async function buildPageShells() {
  const view = $("pdfview");
  view.innerHTML = "";
  S.pages = [];
  resetObserver();
  for (let i = 0; i < S.numPages; i++) {
    const page = await S.pdf.getPage(i + 1);
    const vp = page.getViewport({ scale: 1 });  // in points; setScale() resizes the shell
    const el = document.createElement("div");
    el.className = "pdfpage";
    el.style.width = vp.width * S.scale + "px";
    el.style.height = vp.height * S.scale + "px";
    el.dataset.page = i;
    el.innerHTML = '<div class="ph">Page ' + (i + 1) + "</div>";
    const hlLayer = document.createElement("div");
    hlLayer.className = "hllayer";          // exists from the start -> highlight works pre-render
    el.appendChild(hlLayer);
    view.appendChild(el);
    S.pages.push({ el, hllayer: hlLayer, w1: vp.width, h1: vp.height, gen: 0, rendered: false,
      layoutLoaded: false, segs: null, translated: false, trLoading: false });
    S.pageObs.observe(el);
    S.dropObs.observe(el);
  }
}

function onPageVisible(entries) {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    ensureRendered(+e.target.dataset.page);
  }
}

function onPageFar(entries) {
  for (const e of entries) {
    if (!e.isIntersecting) stopRender(S.pages[+e.target.dataset.page], true);
  }
}

// Stop a page's drawing: cancel a render in flight and drop the text layer (its spans are placed
// for the old scale). The canvas stays, stretched to the page's new size, until the redraw
// replaces it, so a zoom does not flash blank pages; `release` frees it instead.
function stopRender(P, release) {
  P.gen += 1;  // a render already past its last await sees this and does not land
  if (P.renderTask) { P.renderTask.cancel(); P.renderTask = null; }
  P.rendered = false;
  if (P.textEl) { P.textEl.remove(); P.textEl = null; }
  if (!P.canvas) return;
  if (release) {
    P.canvas.width = P.canvas.height = 0;  // free the pixels now, not at the next GC
    P.canvas.remove(); P.canvas = null;
  } else {
    P.canvas.style.width = "100%"; P.canvas.style.height = "100%";
  }
}

// Watch every page anew: observe() reports each page's current state at once, so the pages now
// near the view are drawn and the far ones released.
function observePages() {
  for (const obs of [S.pageObs, S.dropObs]) {
    for (const P of S.pages) { obs.unobserve(P.el); obs.observe(P.el); }
  }
}

// Fetch word geometry + sentences for a page (no canvas needed). Rects are in PDF points.
// Caches the in-flight promise so concurrent callers (render + translateAll) share one fetch.
function ensureLayout(i) {
  const P = S.pages[i];
  if (!P) return Promise.resolve(null);
  if (P.layoutPromise) return P.layoutPromise;
  P.layoutPromise = (async () => {
    try {
      const L = await fetch(`/api/layout/${S.docId}/${i}`).then((r) => r.json());
      P.layout = L;
      P.segs = L.sentences.map((s) => ({
        text: s.text,
        tex: s.tex,  // the text with ^{...}/_{...} script marks, sent for translation only
        para: s.para,
        size: s.size,
        heading: s.heading,
        eqno: s.eqno || null,  // equation number "(1)" printed beside a display equation
        rects: lineRects(s.words.map((wi) => L.words[wi]), !s.equation),  // in points
        // A sentence that wraps to the next page carries that page's word boxes too, so the
        // highlight can span both pages.
        rectsNext: s.words_next ? lineRects(s.words_next) : null,
        nextPage: s.cont_next_page ?? null,
      }));
    } catch (err) {
      P.segs = [];
    }
    return P.segs;
  })();
  return P.layoutPromise;
}

// Largest page canvas in pixels. A high zoom on a Retina display would go past it, and a bigger
// canvas costs too much memory or is not drawn at all, so such a page is drawn at a lower density.
const MAX_CANVAS_PX = 2 ** 24;

async function ensureRendered(i) {
  const P = S.pages[i];
  if (!P || P.rendered) return;
  P.rendered = true;
  const gen = P.gen;  // stopRender() bumps it when a zoom or the distance makes this render moot
  const page = await S.pdf.getPage(i + 1);
  if (P.gen !== gen) return;
  const vp = page.getViewport({ scale: S.scale });

  // Draw at the screen's pixel density: on a Retina display (2 device pixels per CSS pixel) a
  // CSS-sized canvas is upscaled and the page looks soft. The CSS size is the backing size over
  // dpr, not vp's fractional size, so each canvas pixel lands on exactly one device pixel.
  const dpr = Math.min(window.devicePixelRatio || 1, Math.sqrt(MAX_CANVAS_PX / (vp.width * vp.height)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(vp.width * dpr); canvas.height = Math.floor(vp.height * dpr);
  canvas.style.width = canvas.width / dpr + "px"; canvas.style.height = canvas.height / dpr + "px";
  const textEl = document.createElement("div");
  textEl.className = "textlayer";

  P.renderTask = page.render({
    canvasContext: canvas.getContext("2d"), viewport: vp,
    transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
  });
  try { await P.renderTask.promise; } catch (err) { return; }  // cancelled by stopRender()
  if (P.gen !== gen) return;
  P.renderTask = null;
  // Swap the drawing in; it replaces the stretched one a zoom left behind.
  const ph = P.el.querySelector(".ph");
  if (ph) ph.remove();
  if (P.canvas) { P.canvas.width = P.canvas.height = 0; P.canvas.remove(); }
  P.el.insertBefore(canvas, P.hllayer);  // order: canvas, hllayer, textlayer
  P.canvas = canvas;

  await ensureLayout(i);
  if (P.gen !== gen) return;
  const L = P.layout;
  if (L) {
    const sc = vp.width / L.width; // px per point
    const wordSpans = [];
    const frag = document.createDocumentFragment();
    for (const w of L.words) {
      const span = document.createElement("span");
      span.textContent = w.t + " ";  // trailing space so copied/selected text isn't crammed
      const h = (w.y1 - w.y0) * sc;
      span.style.left = (w.x0 * sc) + "px";
      span.style.top = (w.y0 * sc) + "px";
      // Clip the span to its word box (overflow: hidden in .textlayer span). The fallback font
      // draws ~1.35x wider than the PDF font, and a rotated word (the arXiv side stamp) gets a
      // font-size of its whole height, so an unclipped span covers its neighbours and steals
      // their hover.
      span.style.width = ((w.x1 - w.x0) * sc) + "px";
      span.style.fontSize = h + "px";
      span.style.lineHeight = h + "px";
      frag.appendChild(span);
      wordSpans.push(span);
    }
    textEl.appendChild(frag);
    L.sentences.forEach((s, k) => s.words.forEach((wi) => { if (wordSpans[wi]) wordSpans[wi]._seg = k; }));
    // The top of this page may finish a sentence that the previous page owns: layout() moves the
    // continuation there (as words_next) and drops it here, so its words have no sentence on this
    // page. Point them at that sentence, so hovering them highlights it on both pages.
    const prev = i > 0 ? S.pages[i - 1] : null;
    if (prev && (await ensureLayout(i - 1)) && prev.layout) {
      const at = new Map(L.words.map((w, wi) => [`${w.x0},${w.y0},${w.x1},${w.y1}`, wi]));
      prev.layout.sentences.forEach((s, k) => {
        if (s.cont_next_page !== i || !s.words_next) return;
        for (const w of s.words_next) {
          const sp = wordSpans[at.get(`${w.x0},${w.y0},${w.x1},${w.y1}`)];
          if (sp && sp._seg == null) { sp._seg = k; sp._segPage = i - 1; }
        }
      });
    }
  }
  if (P.gen !== gen) return;
  P.el.appendChild(textEl);
  P.textEl = textEl;
  attachPdfHover(i, textEl);
}

// Translate the whole document with PARAGRAPH-level parallelism: the unit of work is a paragraph
// batch (not a page), and a pool of trPool() workers translates batches concurrently — so even one
// page keeps the pool busy. A producer walks pages in order, lays each out, builds its (empty)
// DOM, and enqueues its batches; workers drain the queue. Display stays strictly in order via the
// sentence-level frontier (see paintFrontier). Equation vision is a second pass after.
const trPool = () => (S.aiConfig && S.aiConfig.backend ? S.aiConfig.concurrency[S.aiConfig.backend] : 1);
async function translateAll(force) {
  if (!S.aiConfig || !S.aiConfig.backend) return;
  if (!S.docId || !$("autotr").checked) return;
  const token = ++S.trToken;
  S.rateLimited = null; hide($("ai-banner"));
  const queue = [];   // pending {page, batch} tasks, in document order
  let produced = false;

  (async function produce() {
    for (let i = 0; i < S.numPages; i++) {
      if (token !== S.trToken) break;
      await ensureLayout(i);
      if (token !== S.trToken) break;
      const batches = preparePage(i);   // builds the page's empty DOM, returns its batches
      if (!batches.length) paintFrontier();            // empty page: let the frontier pass it
      for (const b of batches) queue.push({ page: i, batch: b });
    }
    produced = true;
  })();

  async function worker() {
    for (;;) {
      if (token !== S.trToken) return;
      if (queue.length === 0) {
        if (produced) return;
        await sleep(40);                                // wait for the producer to enqueue more
        continue;
      }
      const t = queue.shift();
      if (S.rateLimited === token) { fillSource(t.page, t.batch, token); continue; }  // no more requests
      await translateBatch(t.page, t.batch, force, token);
    }
  }
  await Promise.all(Array.from({ length: trPool() }, worker));

  for (let i = 0; i < S.numPages; i++) {      // second pass: upgrade display equations
    if (token !== S.trToken || S.rateLimited === token) return;
    await fetchMath(i);
  }
}

// Merge a sentence's word boxes into one rectangle per visual line (in PDF points).
// A word joins the current line when its top is within 0.7 x its own height of the line's top.
// Sub/superscript glyphs (x_d, k_{q,t}, m_t) sit on a shifted baseline, so the smaller ones fail
// that test and would open a second, overlapping rect (a doubled highlight). Such a glyph is
// folded into the current line when (a) it is script-size (<= 0.8 x the sentence's text height),
// (b) its vertical center lies in the line's text band (median top..median bottom of its
// regular-size words, +-25%), (c) it is horizontally next to a word already in the line, and
// (d) its center is nearer this line than the next text line. A folded glyph does not move the
// line's reference top, so it cannot pull the next line in, and tall math boxes never widen the
// band. Words that pass the first test are handled as before, so a sentence without script
// glyphs gets the same rects. Display equations (foldScripts = false) keep the plain rule: their
// fractions and matrices are 2D, not lines.
function lineRects(words, foldScripts = true) {
  const ws = words
    .map((w) => ({ x0: w.x0, y0: w.y0, x1: w.x1, y1: w.y1 }))
    .sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0);
  if (!ws.length) return [];
  const hs = ws.map((w) => w.y1 - w.y0).sort((a, b) => a - b);
  const href = hs[Math.min(hs.length - 1, Math.floor(0.75 * hs.length))];  // full text height
  // TeX math-font glyphs (CMSY10 x, -, F, H; CMEX10 hats, radicals, sums) get a word box that
  // hangs 0.6-2 x the text height below the line (PyMuPDF sizes it from the font's descender),
  // and a hat accent's box also starts above the line. Grouped like other words, such a box
  // draws its line's rect down over the next line and, via the 0.7 x h test, joins the line
  // above. So prose lines are built without them (they are < 25% of the words, as href is the
  // upper quartile) and each is then attached, x-extent only, to the line rect that holds the
  // centre of its top text-height slice (where the glyph is drawn).
  const isTall = (w) => foldScripts && w.y1 - w.y0 > 1.3 * href;
  const tall = ws.filter(isTall);
  const body = tall.length ? ws.filter((w) => !isTall(w)) : ws;
  const isScript = (h) => h <= 0.8 * href;
  const isRegular = (h) => h > 0.8 * href && h <= 1.3 * href;
  const median = (xs) => {
    const s = [...xs].sort((a, b) => a - b), n = s.length;
    return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
  };
  const rects = [];
  let cur = null;  // {x0,y0,x1,y1, top: first-test reference, xs: x-ranges, tops/bots: band}
  const grow = (w) => {
    cur.x0 = Math.min(cur.x0, w.x0); cur.y0 = Math.min(cur.y0, w.y0);
    cur.x1 = Math.max(cur.x1, w.x1); cur.y1 = Math.max(cur.y1, w.y1);
    cur.xs.push([w.x0, w.x1]);
  };
  const foldsInto = (w, i) => {
    const t = median(cur.tops), b = median(cur.bots), pad = (b - t) * 0.25;
    const cy = (w.y0 + w.y1) / 2;
    if (!(cy > t - pad && cy < b + pad)) return false;
    if (!cur.xs.some(([a, c]) => Math.max(a, w.x0) - Math.min(c, w.x1) <= href)) return false;
    // The next text line starts at the first later regular word the first test would not join.
    for (let j = i + 1; j < body.length; j++) {
      const u = body[j], uh = u.y1 - u.y0;
      if (isRegular(uh) && Math.abs(u.y0 - cur.top) > uh * 0.7) {
        return Math.abs(cy - (t + b) / 2) < Math.abs(cy - (u.y0 + u.y1) / 2);
      }
    }
    return true;
  };
  body.forEach((w, i) => {
    const h = w.y1 - w.y0;
    if (cur && Math.abs(w.y0 - cur.top) <= h * 0.7) {
      grow(w);
      cur.top = Math.min(cur.top, w.y0);
      if (isRegular(h)) { cur.tops.push(w.y0); cur.bots.push(w.y1); }
      return;
    }
    if (foldScripts && cur && cur.tops.length && isScript(h) && foldsInto(w, i)) {
      grow(w);
      return;
    }
    if (cur) rects.push(cur);
    cur = { x0: w.x0, y0: w.y0, x1: w.x1, y1: w.y1, top: w.y0, xs: [[w.x0, w.x1]], tops: [], bots: [] };
    if (isRegular(h)) { cur.tops.push(w.y0); cur.bots.push(w.y1); }
  });
  rects.push(cur);
  for (const w of tall) {
    const cy = w.y0 + href / 2, mid = (r) => Math.abs(cy - (r.y0 + r.y1) / 2);
    let best = null;
    for (const r of rects) {
      const near = Math.max(r.x0 - w.x1, w.x0 - r.x1) <= href;
      if (near && cy >= r.y0 && cy <= r.y1 && (!best || mid(r) < mid(best))) best = r;
    }
    if (best) { best.x0 = Math.min(best.x0, w.x0); best.x1 = Math.max(best.x1, w.x1); }
    else rects.push({ x0: w.x0, y0: w.y0, x1: w.x1, y1: w.y0 + href });
  }
  return rects.map((r) => ({ left: r.x0, top: r.y0, w: r.x1 - r.x0, h: r.y1 - r.y0 }));
}

// ================= Translation =================
function buildTrShells() {
  const list = $("trlist");
  list.innerHTML = "";
  S.frontier = 0; // earliest page not yet fully displayed (strict top-to-bottom reveal)
  for (let i = 0; i < S.numPages; i++) {
    const wrap = document.createElement("div");
    wrap.className = "trpage"; wrap.dataset.page = i;
    wrap.innerHTML = '<div class="ph"></div>';
    list.appendChild(wrap);
  }
}

// Stream a translation for one batch of segment texts, calling write(pageGlobalIndex, text) for
// each result. The server numbers results from 0 within the batch, so we add the batch's start.
async function streamTranslate(page, offset, texts, force, write) {
  const res = await fetch("/api/translate_stream", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id: S.docId, page, target_lang: lang(), segments: texts, force: !!force }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body.rate_limited ? new RateLimited(body.detail) : new Error(body.detail || res.statusText);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
      if (!line.trim()) continue;
      let obj; try { obj = JSON.parse(line); } catch { continue; }
      if (obj.error) throw obj.rate_limited ? new RateLimited(obj.error) : new Error(obj.error);
      write(offset + obj.i, obj.tr);
    }
  }
}

// Strict top-to-bottom display. Paragraph batches translate in parallel and land in per-page
// buffers out of order, but a sentence is PAINTED only when every earlier sentence is painted.
// S.frontier is the earliest page with an unpainted sentence; P.frontK is the next sentence to
// paint within it. We paint the contiguous run of already-buffered sentences and stop at the
// first gap (a sentence still being translated), so nothing ever appears out of order.
function paintFrontier() {
  while (S.frontier < S.numPages) {
    const P = S.pages[S.frontier];
    if (!P || !P.spanByK) break;                 // this page not prepared yet → wait
    while (P.frontK < P.spanByK.length && P.trBuf[P.frontK] != null) {
      if (P.spanByK[P.frontK]) P.spanByK[P.frontK].textContent = P.trBuf[P.frontK];
      P.frontK += 1;
      if (S.onFirstPaint) { const cb = S.onFirstPaint; S.onFirstPaint = null; cb(); }
    }
    if (P.frontK < P.spanByK.length) break;      // waiting for a sentence still in flight
    if (P.loadingEl) { P.loadingEl.remove(); P.loadingEl = null; }
    const wrap = $("trlist").children[S.frontier];
    if (wrap) renderMath(wrap);
    P.translated = true;
    S.frontier += 1;
  }
  // Every page is displayed (or the whole document has no translatable sentences, e.g. a scanned
  // PDF): release the loading cover even though nothing was painted, so it can never hang open.
  if (S.frontier >= S.numPages && S.onFirstPaint) {
    const cb = S.onFirstPaint; S.onFirstPaint = null; cb();
  }
}

// Split a page's segments into paragraph-aligned batches. Each paragraph is its own batch, but a
// batch keeps absorbing segments until it reaches MIN_BATCH, so a lone heading or a one-line
// paragraph merges with its neighbour instead of costing a whole CLI call. Returns [{start,texts}].
function paragraphBatches(segs) {
  const MIN_BATCH = 3;
  const batches = [];
  let cur = null, prevPara = null;
  segs.forEach((seg, k) => {
    if (cur && seg.para !== prevPara && cur.texts.length >= MIN_BATCH) {
      batches.push(cur); cur = null;
    }
    if (!cur) cur = { start: k, texts: [] };
    cur.texts.push(seg.tex || seg.text);
    prevPara = seg.para;
  });
  if (cur) batches.push(cur);
  return batches;
}

// Build one page's (empty) translation DOM and return its paragraph batches. Stores P.spanByK
// (spans by segment index), P.trBuf (translations as they arrive), P.frontK (next sentence to
// paint), and P.loadingEl. Called once per page by the producer, in document order.
function preparePage(i) {
  const P = S.pages[i];
  if (!P) return [];
  const wrap = $("trlist").children[i];
  if (wrap) wrap.innerHTML = "";
  P.spanByK = []; P.trBuf = []; P.frontK = 0; P.loadingEl = null;
  if (!P.segs || !P.segs.length) return [];
  // Paragraph structure: sentences of the same block flow inline in one <p>. Paragraph font-size
  // follows the source (headings bigger), scaled to the body size.
  const sizes = P.segs.map((s) => s.size).filter((x) => x > 0).sort((a, b) => a - b);
  const bodySize = sizes.length ? sizes[Math.floor(sizes.length / 2)] : 10;
  let para = null, curPara = null;
  P.segs.forEach((seg, k) => {
    const isBullet = BULLET_RE.test(seg.text);
    if (!para || seg.para !== curPara) {
      para = document.createElement("div"); para.className = "trpara";
      const ratio = seg.size > 0 ? seg.size / bodySize : 1;
      para.style.fontSize = Math.max(12, Math.min(26, 13.5 * ratio)) + "px";
      if (seg.heading || ratio >= 1.22) { para.style.fontWeight = "700"; para.classList.add("trhead"); }
      wrap.appendChild(para); curPara = seg.para;
    } else if (isBullet) {
      // A bullet item is its own line even inside the same block, so a list reads as a list.
      para.appendChild(document.createElement("br"));
    }
    const sp = makeTrSeg(i, k, "");
    if (seg.eqno) sp.dataset.eqno = seg.eqno;
    if (isBullet) sp.classList.add("trbullet");
    para.appendChild(sp);
    if (seg.eqno) {
      // Shown beside the translated equation until the image-read LaTeX takes it as \tag.
      const no = document.createElement("span");
      no.className = "treqno"; no.textContent = "(" + seg.eqno + ")";
      para.appendChild(no);
    }
    para.appendChild(document.createTextNode(" "));
    P.spanByK[k] = sp;
  });
  const loading = document.createElement("div");
  loading.className = "ph"; loading.textContent = "Translating…";
  wrap.appendChild(loading);
  P.loadingEl = loading;
  return paragraphBatches(P.segs);
}

// Translate one paragraph batch and buffer its sentences; paintFrontier() then shows whatever
// contiguous prefix is now ready. On failure, fall back to the source text for that batch so the
// frontier is never permanently stalled on a missing sentence.
// Fill a batch's still-missing sentences with the source text and repaint. Used when a batch
// gives up, and for every batch still queued after a rate limit, so the frontier never stalls.
function fillSource(page, b, token) {
  const P = S.pages[page];
  if (!P || token !== S.trToken || !P.trBuf) return;
  for (let k = b.start; k < b.start + b.texts.length; k++) {
    if (P.trBuf[k] == null) P.trBuf[k] = P.segs[k] ? P.segs[k].text : "";
  }
  paintFrontier();
}

async function translateBatch(page, b, force, token) {
  const P = S.pages[page];
  if (!P) return;
  const end = b.start + b.texts.length;
  // A retranslate (lang/model change) bumps S.trToken and nulls P.trBuf; guard every buffer touch
  // so a lingering old-run worker can't throw on a null buffer or paint stale text into a new run.
  const live = () => token === S.trToken && P.trBuf;
  const write = (k, tr) => { if (!live()) return; P.trBuf[k] = tr; paintFrontier(); };
  const hasGap = () => { if (!live()) return false; for (let k = b.start; k < end; k++) if (P.trBuf[k] == null) return true; return false; };
  // Retry a batch that FAILED (e.g. a 502 under heavy parallelism) OR that came back with a gap
  // (the model occasionally drops a numbered line). Retries force a fresh call so they don't just
  // replay the cached partial. This is what keeps the odd sentence from staying untranslated.
  for (let attempt = 0; attempt < 3; attempt++) {
    if (!live() || S.rateLimited === token) break;
    try {
      await streamTranslate(page, b.start, b.texts, force || attempt > 0, write);
    } catch (err) {
      if (err instanceof RateLimited) { S.rateLimited = token; showRateLimit(); break; }
    }
    if (!hasGap()) break;
    if (attempt < 2) await sleep(400);
  }
  // Last resort: fill any segment still missing with the source text, so a null gap can never
  // permanently stall the strict-order frontier (every later sentence would otherwise stay hidden).
  fillSource(page, b, token);
}

// Display equations come out of the PDF text layer scrambled; the server re-reads each from
// the rendered crop (a vision call) and returns clean LaTeX. Runs as a post-translation pass.
async function fetchMath(i, wrap) {
  wrap = wrap || $("trlist").children[i];
  if (!wrap) return;
  try {
    const r = await fetch(`/api/math/${S.docId}/${i}`);
    if (!r.ok) return;
    const d = await r.json();
    if (!d.equations || !d.equations.length) return;
    for (const eq of d.equations) {
      const sp = wrap.querySelector(`.trseg[data-i="${eq.i}"]`);
      if (!sp || !eq.latex) continue;
      // Keep the translation's own math if KaTeX cannot render the image-read LaTeX.
      if (window.katex) {
        try { window.katex.renderToString(eq.latex, { displayMode: true, throwOnError: true }); }
        catch { continue; }
      }
      const tag = sp.dataset && sp.dataset.eqno ? " \\tag{" + sp.dataset.eqno + "}" : "";
      sp.textContent = "\\[ " + eq.latex + tag + " \\]";
      const no = sp.nextElementSibling;
      if (tag && no && no.classList.contains("treqno")) no.remove();
    }
    renderMath(wrap);
  } catch { /* best-effort: keep the original text if math fetch fails */ }
}

function makeTrSeg(i, k, tr) {
  const d = document.createElement("span");
  d.className = "trseg"; d.dataset.page = i; d.dataset.i = k;
  d.textContent = tr;
  // Hover only highlights (both sides); the PDF is scrolled on CLICK, not on hover, so the
  // eyes are not yanked around while skimming the translation.
  d.addEventListener("mouseenter", () => setHover(i, k, true));
  d.addEventListener("mouseleave", () => setHover(i, k, false));
  d.addEventListener("click", () => {
    // A second click on the pinned sentence releases it (so does Esc, below).
    if (d.classList.contains("pinned")) { d.classList.remove("pinned"); return; }
    unpinTr();
    d.classList.add("pinned");
    highlightSource(i, k, true, "center");
  });
  return d;
}

// Release the translation sentence pinned by a click.
function unpinTr() {
  document.querySelectorAll(".trseg.pinned").forEach((x) => x.classList.remove("pinned"));
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") unpinTr(); });

// Unified: highlight one sentence on BOTH sides — a continuous bar over its lines on
// the PDF, and its translation segment in the panel.
function drawHlRects(P, rects, on) {
  if (!P || !P.hllayer) return;
  P.hllayer.innerHTML = "";
  if (!on) return;
  const sc = S.scale;  // rects are in PDF points; scale to pixels
  // The layer holds one sentence and is translucent as a whole (styles.css), so where its
  // rects overlap (line boxes taller than the line spacing) the colour does not stack into
  // a darker seam. Outlines go in first; the fills drawn after them cover every outline
  // part that falls inside the highlight, leaving one outline around the whole shape.
  for (const cls of ["hlrect hlring", "hlrect"]) {
    for (const r of rects) {
      const d = document.createElement("div");
      d.className = cls;
      d.style.left = (r.left * sc) + "px"; d.style.top = (r.top * sc) + "px";
      d.style.width = (r.w * sc) + "px"; d.style.height = (r.h * sc) + "px";
      P.hllayer.appendChild(d);
    }
  }
}

function setHover(page, k, on) {
  const P = S.pages[page];
  const seg = P && P.segs && P.segs[k];
  if (!seg) return;
  drawHlRects(P, seg.rects, on);
  // A sentence that wraps across the page break highlights on the next page too.
  if (seg.nextPage != null && seg.rectsNext) {
    drawHlRects(S.pages[seg.nextPage], seg.rectsNext, on);
  }
  const t = $("trlist").querySelector(`.trseg[data-page="${page}"][data-i="${k}"]`);
  if (t) t.classList.toggle("hl", on);
}

// From the translation side (click): highlight both + scroll the PDF to the source.
function highlightSource(page, k, on, scroll) {
  setHover(page, k, on);
  if (on && scroll) scrollPdfToSeg(page, k, scroll === "center");
}

// A sentence's highlight as separate blocks: one per page, and within a page one per column (or
// per run of lines that a figure interrupts). Each block keeps its page, its extent and the
// area-weighted sum of its line rects' centres, all in PDF points. Cached on the segment.
function segParts(page, k) {
  const seg = S.pages[page].segs[k];
  if (seg.parts) return seg.parts;
  const parts = [];
  const split = (pg, rects) => {
    if (!rects || !rects.length) return;
    const hs = rects.map((r) => r.h).sort((a, b) => a - b), h = hs[hs.length >> 1];
    let cur = null;
    for (const r of [...rects].sort((a, b) => a.top - b.top)) {
      // A line joins the block above it unless a gap of more than 3 lines parts them. Sorted top
      // to bottom, a sentence that goes on in the next column jumps from that column's top to the
      // previous one's foot. Not an x-overlap test: a sentence that starts at the end of a line
      // has a first rect that does not overlap its second.
      if (!cur || r.top - cur.bot > 3 * h) {
        cur = { page: pg, left: r.left, right: r.left + r.w, top: r.top, bot: r.top + r.h, area: 0, sum: 0 };
        parts.push(cur);
      }
      cur.left = Math.min(cur.left, r.left); cur.right = Math.max(cur.right, r.left + r.w);
      cur.bot = Math.max(cur.bot, r.top + r.h);
      const a = r.w * r.h;
      cur.area += a; cur.sum += a * (r.top + r.h / 2);
    }
  };
  split(page, seg.rects);
  if (seg.nextPage != null) split(seg.nextPage, seg.rectsNext);
  return (seg.parts = parts);
}

// Screen y of a block's middle.
function partY(p) {
  const mid = p.area > 0 ? p.sum / p.area : (p.top + p.bot) / 2;
  return S.pages[p.page].el.getBoundingClientRect().top + mid * S.scale;
}

// Screen y of a sentence's middle: the area-weighted centre of all its blocks. Not the centre of
// its bounding box: for a sentence that ends a column and goes on at the top of the next one,
// that falls in the gap between the two.
function segMidY(page, k) {
  const P = S.pages[page];
  if (!P || !P.segs || !P.segs[k]) return null;
  let area = 0, sum = 0;
  for (const p of segParts(page, k)) { area += p.area; sum += p.area * partY(p); }
  return area > 0 ? sum / area : null;
}

// Index of the block of sentence (pg, k) nearest the pointer (x, y) on page `page`.
function partAt(pg, k, page, x, y) {
  const b = S.pages[page].el.getBoundingClientRect();
  const px = (x - b.left) / S.scale, py = (y - b.top) / S.scale;  // pointer in PDF points
  let best = -1, bestD = Infinity;
  segParts(pg, k).forEach((p, j) => {
    if (p.page !== page) return;
    const dx = Math.max(p.left - px, 0, px - p.right), dy = Math.max(p.top - py, 0, py - p.bot);
    if (dx * dx + dy * dy < bestD) { bestD = dx * dx + dy * dy; best = j; }
  });
  return best;
}

// Scroll the PDF so the highlighted sentence's middle sits at the vertical center of the view.
function scrollPdfToSeg(page, k, center) {
  const y = segMidY(page, k);
  if (y == null) return;
  const view = $("pdfview");
  const yInView = y - view.getBoundingClientRect().top;
  if (center || yInView < 60 || yInView > view.clientHeight - 60) {
    view.scrollTo({ top: view.scrollTop + yInView - view.clientHeight * 0.5, behavior: "smooth" });
  }
}

// From the PDF side: hovering source text highlights that sentence (both sides).
let pdfHoverCur = null;
function attachPdfHover(page, textEl) {
  const P = S.pages[page];
  if (!P.segs) return;
  textEl.addEventListener("mousemove", (e) => {
    const sp = e.target && e.target.closest ? e.target.closest("span") : null;
    const k = sp ? sp._seg : undefined;
    if (k == null) return; // over a gap between words — keep current highlight (no flicker)
    const pg = sp._segPage ?? page;  // a continuation word belongs to the previous page's sentence
    // The translation sits beside the block under the pointer, and follows it when the pointer
    // moves on to the sentence's other column or page.
    const part = partAt(pg, k, page, e.clientX, e.clientY);
    const same = pdfHoverCur && pdfHoverCur.page === pg && pdfHoverCur.k === k;
    if (same && pdfHoverCur.part === part) return;
    if (!same) {
      clearPdfHover();
      setHover(pg, k, true);
    }
    pdfHoverCur = { page: pg, k, part };
    const t = $("trlist").querySelector(`.trseg[data-page="${pg}"][data-i="${k}"]`);
    if (t && $("tab-translation").classList.contains("active")) {
      scrollTrTo(t, part >= 0 ? partY(segParts(pg, k)[part]) : segMidY(pg, k));
    }
  });
  textEl.addEventListener("mouseleave", clearPdfHover);
}
function clearPdfHover() {
  if (pdfHoverCur) { setHover(pdfHoverCur.page, pdfHoverCur.k, false); pdfHoverCur = null; }
}

// Scroll the translation panel so the middle of translation `t` is level with screen y `mid`
// (the middle of the highlighted block under the pointer), so the eyes move straight across.
// Instant, not smooth: sweeping the mouse over the PDF changes the target several times a
// second, and a smooth scroll would keep restarting and trail behind the pointer.
function scrollTrTo(t, mid) {
  if (mid == null) return;
  const list = $("trlist");  // the panel's scroller (flex child of #tab-translation)
  const lb = list.getBoundingClientRect(), tb = t.getBoundingClientRect();
  let y = mid - tb.height / 2;  // where the translation's top goes
  // Near the panel's edges, keep the whole translation visible instead.
  y = Math.max(lb.top, Math.min(y, lb.bottom - tb.height));
  // #trlist is zoomed by trScale, so its scrollTop is in unzoomed px.
  const max = list.scrollHeight - list.clientHeight;
  list.scrollTo({ top: Math.max(0, Math.min(max, list.scrollTop + (tb.top - y) / trScale)), behavior: "instant" });
}

// Auto-translate toggle / lang / model changes -> re-run the whole-document translation
$("autotr").addEventListener("change", () => { if ($("autotr").checked) translateAll(); });
$("lang").addEventListener("change", () => retranslateAll());
$("retranslate").addEventListener("click", () => retranslateAll(true));

// Translation panel text-size control (dynamic, persisted)
let trScale = parseFloat(localStorage.getItem("trScale") || "1") || 1;
function applyTrScale() { try { $("trlist").style.zoom = trScale; } catch (e) {} }
function bumpTrScale(d) {
  trScale = Math.max(0.7, Math.min(2.0, Math.round((trScale + d) * 10) / 10));
  try { localStorage.setItem("trScale", String(trScale)); } catch (e) {}
  applyTrScale();
}
$("tr-smaller").addEventListener("click", () => bumpTrScale(-0.1));
$("tr-bigger").addEventListener("click", () => bumpTrScale(0.1));
applyTrScale();

// Markdown -> HTML without breaking LaTeX. marked treats "\(" as an escaped "(" and "_x_" as
// emphasis, which destroys math before KaTeX sees it. Shield math spans with placeholders,
// run marked, then put the math back (HTML-escaped) for renderMath() to typeset.
function mdMath(text) {
  const spans = [];
  const shielded = (text || "").replace(/\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$\$[\s\S]+?\$\$/g, (m) => {
    spans.push(m);
    return "@@MATH" + (spans.length - 1) + "@@";
  });
  const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return marked.parse(shielded).replace(/@@MATH(\d+)@@/g, (_, k) => escape(spans[Number(k)]));
}

// copy-tex writes LaTeX to text/plain but keeps rendered math in text/html.
// Register after deferred copy-tex has loaded; only postprocess its HTML payload.
document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("copy", (e) => {
    const data = e.clipboardData;
    if (!data || !e.defaultPrevented) return;
    const box = document.createElement("div");
    box.innerHTML = data.getData("text/html");
    const formulas = box.querySelectorAll(".katex");
    if (!formulas.length) return;
    formulas.forEach((k) => {
      const source = k.querySelector('annotation[encoding="application/x-tex"]');
      if (!source) return;
      const display = k.closest(".katex-display");
      const delimiter = display ? "$$" : "$";
      (display || k).replaceWith(document.createTextNode(delimiter + source.textContent + delimiter));
    });
    data.setData("text/html", box.innerHTML);
  });
});

function renderMath(el) {
  if (!window.renderMathInElement) return;
  try {
    renderMathInElement(el, {
      delimiters: [
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
      ],
      throwOnError: false,
    });
  } catch (e) { /* leave raw TeX if KaTeX fails */ }
}

function retranslateAll(force) {
  if (!S.docId) return;
  buildTrShells();
  S.pages.forEach((P) => {
    P.translated = false; P.trLoading = false;
    P.trBuf = null; P.spanByK = null; P.frontK = 0; P.loadingEl = null;
  });
  translateAll(force);
}

// ================= Tabs =================
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tabpane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
    // Generate lazily on first view so they don't compete with translation on open.
    if (btn.dataset.tab === "anatomy") loadAnatomy();
    if (btn.dataset.tab === "summary" && S.docId && !S.summaryLoaded) genSummary();
  });
});

// ================= Research Anatomy =================
const ANAT = [
  ["problem", "Problem", "이 논문은 어떤 문제를 해결하려 하는가?"],
  ["gap", "Gap", "기존 연구에서 무엇이 부족한가?"],
  ["claim", "Main Claim", "저자가 주장하는 핵심은?"],
  ["method", "Method", "무엇을 제안·변경했는가?"],
  ["evidence", "Evidence", "어떤 근거가 주장을 뒷받침하는가?"],
  ["limitations", "Limitations", "결론은 어디까지 성립하는가?"],
];
const EV_SUB = [["dataset_test", "Dataset/Test"], ["metric", "Metric"], ["baseline", "Baseline"], ["ablation", "Ablation"]];

async function loadAnatomy(force) {
  if (!S.docId) return;
  if (!force && S.anatomyLoaded) return;
  if (S.anatomyLoading) return;  // already in flight (open + a tab click would double-fire)
  S.anatomyLoading = true;
  const box = $("anatomy");
  box.innerHTML = '<p class="loading" style="padding:6px">Analyzing structure… (grounded in the source, ~10-30s)</p>';
  try {
    const d = await postJSON("/api/anatomy", { doc_id: S.docId, force: !!force });
    renderAnatomy(d); S.anatomyLoaded = true;
  } catch (err) {
    box.innerHTML = '<p class="loading" style="padding:6px">Error: ' + err.message + "</p>";
  } finally {
    S.anatomyLoading = false;
    updateReviewBtn();
  }
}
$("re-anatomy").addEventListener("click", () => { S.anatomyLoaded = false; loadAnatomy(true); });

function renderAnatomy(d) {
  const box = $("anatomy"); box.innerHTML = "";
  for (const [key, name, q] of ANAT) {
    box.appendChild(key === "evidence" ? evidenceCard(d.evidence || {}, name, q) : cardEl(name, q, d[key]));
  }
}
function cardShell(name, q) {
  const el = document.createElement("div"); el.className = "anat-card";
  el.innerHTML = `<div class="anat-head"><span class="name">${esc(name)}</span><span class="q">${esc(q)}</span></div>`;
  const body = document.createElement("div"); body.className = "anat-body";
  el.appendChild(body);
  return { el, body };
}
function cardEl(name, q, card) {
  const { el, body } = cardShell(name, q);
  body.appendChild(pointsEl(card));
  return el;
}
function evidenceCard(ev, name, q) {
  const { el, body } = cardShell(name, q);
  for (const [sk, sname] of EV_SUB) {
    const h = document.createElement("div"); h.className = "anat-sub"; h.textContent = sname;
    body.appendChild(h);
    body.appendChild(pointsEl(ev[sk]));
  }
  return el;
}
function pointsEl(card) {
  const wrap = document.createElement("div");
  const pts = (card && card.points) || [];
  if (!pts.length) {
    const n = document.createElement("div"); n.className = "anat-none";
    n.textContent = card && card.status === "not_present" ? "해당 없음" : "논문에서 명확히 확인되지 않음";
    wrap.appendChild(n); return wrap;
  }
  const KIND = { author_stated: ["author", "저자 명시"], synthesis: ["synthesis", "종합"], ai_interpretation: ["ai", "AI 해석"] };
  for (const p of pts) {
    const d = document.createElement("div"); d.className = "anat-pt";
    const kind = KIND[p.kind] || KIND.ai_interpretation;
    const vb = p.quote ? (p.verified ? '<span class="badge ok">근거확인</span>' : '<span class="badge warn">근거불명</span>') : "";
    d.innerHTML = `<span class="anat-badges"><span class="badge ${kind[0]}">${kind[1]}</span>${vb}</span><span class="ptx">${esc(p.text || "")}</span>`;
    if (p.quote) {
      const qd = document.createElement("span"); qd.className = "anat-quote"; qd.textContent = "“" + p.quote + "”";
      qd.title = "Click to highlight in the PDF";
      qd.addEventListener("click", () => highlightQuote(p.quote));
      d.appendChild(qd);
    }
    wrap.appendChild(d);
  }
  return wrap;
}

// click an anatomy quote -> find & highlight its source sentence in the (rendered) PDF
function highlightQuote(quote) {
  const nq = quote.replace(/\s+/g, " ").trim().toLowerCase();
  S.pages.forEach((P) => { if (P.hllayer) P.hllayer.innerHTML = ""; });
  for (let pi = 0; pi < S.pages.length; pi++) {
    const P = S.pages[pi];
    if (!P.segs) continue;
    for (let k = 0; k < P.segs.length; k++) {
      if (P.segs[k].text.replace(/\s+/g, " ").toLowerCase().includes(nq)) {
        setHover(pi, k, true);
        scrollPdfToSeg(pi, k, true);
        return true;
      }
    }
  }
  return false;
}

// ================= Summary =================
function resetSummary() { if (S.docId) { S.summaryLoaded = false; $("summary").innerHTML = ""; } }
async function genSummary() {
  if (!S.docId || S.summaryLoaded) return;
  S.summaryLoaded = true;
  $("summary").innerHTML = '<p class="loading">Summarizing… (first run can take a bit)</p>';
  try {
    const r = await postJSON("/api/summarize", { doc_id: S.docId });
    $("summary").innerHTML = mdMath(r.summary);
    renderMath($("summary"));
  } catch (err) {
    S.summaryLoaded = false;
    $("summary").innerHTML = '<p class="loading">Error: ' + err.message + "</p>";
  }
  updateReviewBtn();
}
$("gen-summary").addEventListener("click", () => { S.summaryLoaded = false; genSummary(); });

// The review combines summary + anatomy (+ chat), so enable the button only once both are
// generated — in the normal flow their caches are then guaranteed to exist.
function updateReviewBtn() {
  const btn = $("export-review");
  const ready = S.summaryLoaded && S.anatomyLoaded;
  btn.disabled = !ready;
  btn.title = ready
    ? "요약·구조분석·채팅을 종합한 리뷰 노트를 zip으로 내려받기"
    : "요약과 구조분석(Anatomy)을 먼저 생성하면 활성화됩니다";
}

$("export-review").addEventListener("click", async () => {
  if (!S.docId) return;
  const btn = $("export-review"), label = btn.textContent;
  btn.disabled = true; btn.textContent = "생성 중…";
  try {
    const res = await fetch(`/api/review/${S.docId}`, { method: "POST" });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (d.rate_limited) showRateLimit();
      throw new Error(d.detail || res.statusText);
    }
    const blob = await res.blob();
    const m = (res.headers.get("Content-Disposition") || "").match(/filename="?([^"]+)"?/);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : "review.zip";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    alert("리뷰 생성 실패: " + e.message);
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
});

async function loadChat() {
  $("chatlog").innerHTML = "";
  try {
    const r = await (await fetch(`/api/chat/${S.docId}`)).json();
    for (const t of r.turns) {
      const div = addMsg(t.role === "user" ? "user" : "bot", t.text);
      if (t.role !== "user") { div.innerHTML = mdMath(t.text); renderMath(div); }
    }
  } catch { /* empty chat on failure */ }
}
$("chat-reset").addEventListener("click", async () => {
  if (!S.docId) return;
  await postJSON(`/api/chat/${S.docId}/reset`, {});
  $("chatlog").innerHTML = "";
});

// ================= Chat =================
let chatCite = null;
function setChatCite(text) {
  chatCite = text;
  $("chatcite-text").textContent = "“" + text + "”";
  $("chatcite").classList.remove("hidden");
}
function clearChatCite() { chatCite = null; $("chatcite-text").textContent = ""; $("chatcite").classList.add("hidden"); }
$("chatcite-x").addEventListener("click", clearChatCite);

// Enter adds a line; Cmd/Ctrl+Enter sends. Not while an IME (e.g. Korean) is still composing,
// or the Enter that commits the last syllable would send a half-typed question.
$("chatinput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.isComposing) {
    e.preventDefault();
    $("chatform").requestSubmit();
  }
});
$("chatinput").addEventListener("input", fitChatInput);
// Grow the box with its text; CSS max-height caps it and it scrolls from there.
function fitChatInput() {
  const el = $("chatinput");
  el.style.height = "auto";
  el.style.height = el.scrollHeight + (el.offsetHeight - el.clientHeight) + "px";
}

$("chatform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("chatinput");
  const q = input.value.trim();
  const cite = chatCite;
  if ((!q && !cite) || !S.docId) return;
  input.value = "";
  fitChatInput();
  clearChatCite();

  const userMsg = addMsg("user", q || "(선택한 부분에 대해)");
  if (cite) {
    const cd = document.createElement("div");
    cd.className = "msg-cite"; cd.textContent = "“" + cite + "”";
    userMsg.insertBefore(cd, userMsg.firstChild);
  }
  const bot = addMsg("bot", "…");
  const question = cite
    ? `논문의 다음 부분에 대한 질문입니다:\n"${cite}"\n\n${q || "이 부분을 설명해줘."}`
    : q;
  try {
    const r = await postJSON("/api/chat", { doc_id: S.docId, question });
    bot.innerHTML = mdMath(r.answer);
    renderMath(bot);
  } catch (err) { bot.textContent = "Error: " + err.message; }
  $("chatlog").scrollTop = $("chatlog").scrollHeight;
});
function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role; div.textContent = text;
  $("chatlog").appendChild(div);
  $("chatlog").scrollTop = $("chatlog").scrollHeight;
  return div;
}

// ================= Selection -> Ask in chat =================
let lastSelection = "";
$("pdfview").addEventListener("mouseup", () => {
  setTimeout(() => {
    const sel = window.getSelection();
    const text = sel ? sel.toString().replace(/\s+/g, " ").trim() : "";
    if (text.length < 3) { hide($("selpop")); return; }
    lastSelection = text;
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const pop = $("selpop");
    pop.style.left = Math.max(8, rect.left + rect.width / 2 - 70) + "px";
    pop.style.top = (rect.top - 42) + "px";
    show(pop);
  }, 10);
});
document.addEventListener("mousedown", (e) => {
  if (!$("selpop").contains(e.target)) hide($("selpop"));
});
$("btn-ask").addEventListener("click", () => {
  hide($("selpop"));
  if (!lastSelection) return;
  setChatCite(lastSelection);                               // show as a citation block
  document.querySelector('.tab[data-tab="chat"]').click();  // auto-switch to Chat
  $("chatinput").value = "";
  fitChatInput();
  $("chatinput").focus();
});

// ================= helpers =================
function setTitle(t) { $("title").textContent = t; }
function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }
function fmtSize(b) { return b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(0) + " KB" : (b / 1048576).toFixed(1) + " MB"; }
function fmtDate(e) { return new Date(e * 1000).toLocaleDateString(); }
function esc(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
async function postJSON(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    if (data.rate_limited) showRateLimit();
    throw new Error(data.detail || res.statusText);
  }
  return res.json();
}
async function postForm(url, fd) {
  const res = await fetch(url, { method: "POST", body: fd });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

// ================= PDF zoom =================
// S.zoom.mode is "width" (fit the page width, the default), "page" (fit a whole page) or "pct"
// (a fixed S.zoom.pct). The fit modes refit when the pane changes size; a percentage stays.
// 100% is the paper's real size: pdf.js scale 1 is 1 CSS px per point, and CSS has 96 px per
// inch against 72 points.
const PT_PX = 96 / 72;
const ZOOM_STEPS = [50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200, 250, 300];
try {
  const z = JSON.parse(localStorage.getItem("pdfZoom"));
  if (z && ["width", "page", "pct"].includes(z.mode) && z.pct > 0) S.zoom = z;
} catch (e) {}

// The scale the zoom choice gives for the pane's current size.
function zoomScale() {
  const view = $("pdfview");
  let s = S.zoom.pct / 100 * PT_PX;
  if (S.zoom.mode !== "pct") {
    s = (view.clientWidth - 48) / S.page1.w;  // 24 px of dark margin each side
    // a whole page plus the gaps above and below it
    if (S.zoom.mode === "page") s = Math.min(s, (view.clientHeight - 40) / S.page1.h);
  }
  const lo = ZOOM_STEPS[0] / 100 * PT_PX, hi = ZOOM_STEPS[ZOOM_STEPS.length - 1] / 100 * PT_PX;
  return Math.min(hi, Math.max(lo, s));
}

function showZoom() {
  $("zoom-pct").textContent = Math.round(S.scale / PT_PX * 100) + "%";
  $("fit-width").classList.toggle("on", S.zoom.mode === "width");
  $("fit-page").classList.toggle("on", S.zoom.mode === "page");
}

function setZoom(z, anchor) {
  S.zoom = z;
  try { localStorage.setItem("pdfZoom", JSON.stringify(z)); } catch (e) {}
  if (S.pdf && S.pages.length) setScale(zoomScale(), anchor);
  showZoom();
}

// One step up or down from the current zoom, which a fit mode may have left between steps.
function stepZoom(dir) {
  const cur = S.scale / PT_PX * 100;
  const next = dir > 0
    ? ZOOM_STEPS.find((p) => p > cur + 0.5)
    : [...ZOOM_STEPS].reverse().find((p) => p < cur - 0.5);
  if (next) setZoom({ mode: "pct", pct: next });
}

// "3 / 16" in the PDF bar: the page at the middle of the view, of all pages. The pages are
// stacked top to bottom, so a binary search measures only a few of them per scroll frame.
function showPageNum() {
  if (!S.pages.length) { $("page-num").textContent = ""; return; }
  const view = $("pdfview"), vb = view.getBoundingClientRect();
  const mid = vb.top + view.clientHeight / 2;
  let lo = 0, hi = S.pages.length - 1;  // the last page whose top is above the middle
  while (lo < hi) {
    const m = (lo + hi + 1) >> 1;
    if (S.pages[m].el.getBoundingClientRect().top <= mid) lo = m; else hi = m - 1;
  }
  $("page-num").textContent = `${lo + 1} / ${S.pages.length}`;
}
let pageNumFrame = 0;
$("pdfview").addEventListener("scroll", () => {
  if (!pageNumFrame) pageNumFrame = requestAnimationFrame(() => { pageNumFrame = 0; showPageNum(); });
}, { passive: true });

$("zoom-in").addEventListener("click", () => stepZoom(1));
$("zoom-out").addEventListener("click", () => stepZoom(-1));
$("fit-width").addEventListener("click", () => setZoom({ mode: "width", pct: S.zoom.pct }));
$("fit-page").addEventListener("click", () => setZoom({ mode: "page", pct: S.zoom.pct }));

// Only the PDF pane consumes Ctrl+wheel (including trackpad pinch). Everywhere else the
// browser keeps its normal whole-app zoom. Coalesce high-frequency input into one redraw.
let wheelZoomFrame = 0, wheelZoomDelta = 0, wheelZoomAnchor;
$("pdfview").addEventListener("wheel", (e) => {
  if (!e.ctrlKey || !e.cancelable || !S.pdf || !S.pages.length || !e.deltaY) return;
  e.preventDefault();  // also at the zoom limits: never fall through to browser zoom here
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? $("pdfview").clientHeight : 1;
  wheelZoomDelta += e.deltaY * unit;
  wheelZoomAnchor = { x: e.clientX, y: e.clientY };
  if (wheelZoomFrame) return;
  wheelZoomFrame = requestAnimationFrame(() => {
    wheelZoomFrame = 0;
    const delta = Math.max(-100, Math.min(100, wheelZoomDelta));
    wheelZoomDelta = 0;
    if (!S.pdf || !S.pages.length) return;
    // setScale may skip a tiny redraw; keep accumulating from the requested zoom.
    const currentPct = S.zoom.mode === "pct" ? S.zoom.pct : S.scale / PT_PX * 100;
    const pct = Math.max(ZOOM_STEPS[0], Math.min(ZOOM_STEPS[ZOOM_STEPS.length - 1],
      currentPct * Math.exp(-delta * 0.002)));
    setZoom({ mode: "pct", pct }, wheelZoomAnchor);
  });
}, { passive: false });

// Resize every page to scale `ns` in place and keep the point under the pointer (or the
// middle of the view for buttons/refitting) where it is. The pages keep their layout,
// sentences and translation state (rebuilding S.pages would orphan a translation still in
// progress); only their drawing is redone, near the view first.
function setScale(ns, anchor) {
  if (Math.abs(ns - S.scale) < 0.001) return;
  const view = $("pdfview"), vb = view.getBoundingClientRect();
  const ax = anchor ? anchor.x : vb.left + view.clientWidth / 2;
  const ay = anchor ? anchor.y : vb.top + view.clientHeight / 2;
  // the page under the anchor (or the last one above it) and the anchor's place on it, in points
  let at = 0;
  for (let i = 1; i < S.pages.length && S.pages[i].el.getBoundingClientRect().top <= ay; i++) at = i;
  const r0 = S.pages[at].el.getBoundingClientRect();
  const fx = (ax - r0.left) / S.scale, fy = (ay - r0.top) / S.scale;
  clearPdfHover();
  S.scale = ns;
  for (const P of S.pages) {
    P.el.style.width = P.w1 * ns + "px";
    P.el.style.height = P.h1 * ns + "px";
    P.hllayer.innerHTML = "";  // drawn for the old scale
    stopRender(P, false);
  }
  const r1 = S.pages[at].el.getBoundingClientRect();
  view.scrollLeft += r1.left + fx * ns - ax;
  view.scrollTop += r1.top + fy * ns - ay;
  observePages();
}

// ================= Resizable panels =================
// The pane changed size: a fit mode refits, a fixed percentage stays.
function refitPdf() {
  if (!S.pdf || !S.pages.length || S.zoom.mode === "pct") return;
  const ns = zoomScale();
  if (Math.abs(ns - S.scale) < 0.03) return;
  setScale(ns);
  showZoom();
}

(function initResizers() {
  const ws = document.querySelector(".workspace");
  const c1 = localStorage.getItem("c1"), c3 = localStorage.getItem("c3");
  try { if (c1) ws.style.setProperty("--c1", c1); if (c3) ws.style.setProperty("--c3", c3); } catch (e) {}

  function drag(handle, varName, fromRight) {
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault(); handle.setPointerCapture(e.pointerId); handle.classList.add("active");
      const rect = ws.getBoundingClientRect();
      const move = (ev) => {
        let w = fromRight ? rect.right - ev.clientX : ev.clientX - rect.left;
        w = Math.max(150, Math.min(rect.width * 0.6, w));
        ws.style.setProperty(varName, w + "px");
      };
      const up = () => {
        handle.classList.remove("active");
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        try { localStorage.setItem(varName === "--c1" ? "c1" : "c3", ws.style.getPropertyValue(varName)); } catch (e) {}
        refitPdf();
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }
  drag($("rz1"), "--c1", false);
  drag($("rz2"), "--c3", true);
})();

let resizeT;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(refitPdf, 300); });

window.S = S; // expose for debugging
loadConfig().finally(() => setLibSource(libSource));
