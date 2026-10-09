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
  pages: [],      // per page: {el, canvas, textEl, rendered, segs, translated, trLoading}
  pageObs: null,  // IntersectionObserver for rendering/translating
  anatomyLoaded: false,
  anatomyModel: null,
  summaryLoaded: false,
  trToken: 0,
  aiConfig: null,
  rateLimited: null,
};

class RateLimited extends Error {}

const TASK_LABELS = { translate: "Translation", summary: "Summary", anatomy: "Anatomy",
  chat: "Chat", explain: "Explain", equation: "Equations", brief: "Glossary" };

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

function fillModels(backend) {
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
}

$("ai-badge").addEventListener("click", openSettings);
$("set-backend").addEventListener("change", (e) => fillModels(e.target.value));
$("settings-cancel").addEventListener("click", () => $("settings").close());
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
async function loadLibrary() {
  const list = $("paperlist");
  list.innerHTML = '<li class="emptylib">Loading…</li>';
  try {
    const r = await fetch("/api/papers").then((x) => x.json());
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
    list.innerHTML = '<li class="emptylib">Error: ' + err.message + "</li>";
  }
}
$("refresh").addEventListener("click", loadLibrary);

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
  } catch (err) { setTitle("Upload failed: " + err.message); }
});

// ================= Open document =================
async function openDoc(meta) {
  S.docId = meta.doc_id;
  S.numPages = meta.num_pages;
  setTitle(meta.title + "  ·  " + meta.num_pages + " pages");

  // Cover the whole workspace with the loading screen the instant the paper is opened, before
  // any await, so the PDF never flashes into view before it is ready. The warm-up below keeps it
  // up until the first page is translated and a few pages of layout are buffered, then hides it.
  const ov = $("prep-overlay");
  $("prep-fill").style.width = "0%";
  $("prep-text").textContent = "0%";
  ov.classList.remove("hidden");

  $("empty").classList.add("hidden");
  $("pdfview").classList.remove("hidden");
  $("aipanel").classList.remove("hidden");
  $("summary").innerHTML = "";
  $("chatlog").innerHTML = "";
  S.summaryLoaded = false;
  S.anatomyLoaded = false; S.anatomyModel = null;
  $("anatomy").innerHTML = '<p class="hint" style="padding:6px">Open the Anatomy tab to analyze the paper (grounded in the source).</p>';

  S.pdf = await pdfjsLib.getDocument("/api/pdf/" + meta.doc_id).promise;

  // compute scale from the center column width using page 1
  const first = await S.pdf.getPage(1);
  const vw = first.getViewport({ scale: 1 });
  const avail = $("pdfview").clientWidth - 48;
  S.scale = Math.min(2.0, Math.max(0.8, avail / vw.width));

  await buildPageShells();  // MUST await: it fills S.pages (one getPage per page); translateAll's
  buildTrShells();          // producer reads S.pages[i], so it has to be fully built first.
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
  S.pageObs = new IntersectionObserver(onPageVisible, {
    root: $("pdfview"), rootMargin: "800px 0px", threshold: 0.01,
  });
}

async function buildPageShells() {
  const view = $("pdfview");
  view.innerHTML = "";
  S.pages = [];
  resetObserver();
  for (let i = 0; i < S.numPages; i++) {
    const page = await S.pdf.getPage(i + 1);
    const vp = page.getViewport({ scale: S.scale });
    const el = document.createElement("div");
    el.className = "pdfpage";
    el.style.width = vp.width + "px";
    el.style.height = vp.height + "px";
    el.dataset.page = i;
    el.innerHTML = '<div class="ph">Page ' + (i + 1) + "</div>";
    const hlLayer = document.createElement("div");
    hlLayer.className = "hllayer";          // exists from the start -> highlight works pre-render
    el.appendChild(hlLayer);
    view.appendChild(el);
    S.pages.push({ el, hllayer: hlLayer, rendered: false, layoutLoaded: false, segs: null, translated: false, trLoading: false });
    S.pageObs.observe(el);
  }
}

function onPageVisible(entries) {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    ensureRendered(+e.target.dataset.page);
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
        para: s.para,
        size: s.size,
        heading: s.heading,
        rects: lineRects(s.words.map((wi) => L.words[wi])),  // in points
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

async function ensureRendered(i) {
  const P = S.pages[i];
  if (!P || P.rendered) return;
  P.rendered = true;
  const page = await S.pdf.getPage(i + 1);
  const vp = page.getViewport({ scale: S.scale });

  const canvas = document.createElement("canvas");
  canvas.width = vp.width; canvas.height = vp.height;
  const textEl = document.createElement("div");
  textEl.className = "textlayer";
  const ph = P.el.querySelector(".ph");
  if (ph) ph.remove();
  P.el.insertBefore(canvas, P.hllayer);  // order: canvas, hllayer, textlayer
  P.el.appendChild(textEl);

  await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;

  await ensureLayout(i);
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
      span.style.fontSize = h + "px";
      span.style.lineHeight = h + "px";
      frag.appendChild(span);
      wordSpans.push(span);
    }
    textEl.appendChild(frag);
    L.sentences.forEach((s, k) => s.words.forEach((wi) => { if (wordSpans[wi]) wordSpans[wi]._seg = k; }));
  }
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
      if (S.rateLimited === token) return;
      if (token !== S.trToken) return;
      if (queue.length === 0) {
        if (produced) return;
        await sleep(40);                                // wait for the producer to enqueue more
        continue;
      }
      const t = queue.shift();
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
function lineRects(words) {
  const ws = words
    .map((w) => ({ x0: w.x0, y0: w.y0, x1: w.x1, y1: w.y1 }))
    .sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0);
  const rects = [];
  let cur = null;
  for (const w of ws) {
    const h = w.y1 - w.y0;
    if (cur && Math.abs(w.y0 - cur.y0) <= h * 0.7) {
      cur.x0 = Math.min(cur.x0, w.x0); cur.y0 = Math.min(cur.y0, w.y0);
      cur.x1 = Math.max(cur.x1, w.x1); cur.y1 = Math.max(cur.y1, w.y1);
    } else {
      if (cur) rects.push(cur);
      cur = { ...w };
    }
  }
  if (cur) rects.push(cur);
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
    cur.texts.push(seg.text);
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
    if (isBullet) sp.classList.add("trbullet");
    para.appendChild(sp);
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
  if (!live()) return;
  for (let k = b.start; k < end; k++) {
    if (P.trBuf[k] == null) P.trBuf[k] = P.segs[k] ? P.segs[k].text : "";
  }
  paintFrontier();
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
      if (sp && eq.latex) sp.textContent = "\\[ " + eq.latex + " \\]";
    }
    renderMath(wrap);
  } catch { /* best-effort: keep the original text if math fetch fails */ }
}

function makeTrSeg(i, k, tr) {
  const d = document.createElement("span");
  d.className = "trseg"; d.dataset.page = i; d.dataset.i = k;
  d.textContent = tr;
  d.addEventListener("mouseenter", () => highlightSource(i, k, true, "near"));
  d.addEventListener("mouseleave", () => highlightSource(i, k, false));
  d.addEventListener("click", () => {
    document.querySelectorAll(".trseg.pinned").forEach((x) => x.classList.remove("pinned"));
    d.classList.add("pinned");
    highlightSource(i, k, true, "center");
  });
  return d;
}

// Unified: highlight one sentence on BOTH sides — a continuous bar over its lines on
// the PDF, and its translation segment in the panel.
function drawHlRects(P, rects, on) {
  if (!P || !P.hllayer) return;
  P.hllayer.innerHTML = "";
  if (!on) return;
  const sc = S.scale;  // rects are in PDF points; scale to pixels
  for (const r of rects) {
    const d = document.createElement("div");
    d.className = "hlrect";
    d.style.left = (r.left * sc) + "px"; d.style.top = (r.top * sc) + "px";
    d.style.width = (r.w * sc) + "px"; d.style.height = (r.h * sc) + "px";
    P.hllayer.appendChild(d);
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

// From the translation side (hover/click): highlight both + scroll the PDF to the source.
function highlightSource(page, k, on, scroll) {
  setHover(page, k, on);
  if (on && scroll) scrollPdfToSeg(page, k, scroll === "center");
}

// Scroll the PDF so the highlighted sentence sits at the vertical center of the view.
function scrollPdfToSeg(page, k, center) {
  const P = S.pages[page];
  const seg = P && P.segs && P.segs[k];
  if (!seg || !seg.rects.length) return;
  const view = $("pdfview");
  const topInView = P.el.getBoundingClientRect().top - view.getBoundingClientRect().top + seg.rects[0].top * S.scale;
  if (center || topInView < 60 || topInView > view.clientHeight - 60) {
    view.scrollTo({ top: view.scrollTop + topInView - view.clientHeight * 0.5, behavior: "smooth" });
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
    if (!pdfHoverCur || pdfHoverCur.page !== page || pdfHoverCur.k !== k) {
      clearPdfHover();
      pdfHoverCur = { page, k };
      setHover(page, k, true);
      const t = $("trlist").querySelector(`.trseg[data-page="${page}"][data-i="${k}"]`);
      if (t && $("tab-translation").classList.contains("active")) t.scrollIntoView({ block: "nearest" });
    }
  });
  textEl.addEventListener("mouseleave", clearPdfHover);
}
function clearPdfHover() {
  if (pdfHoverCur) { setHover(pdfHoverCur.page, pdfHoverCur.k, false); pdfHoverCur = null; }
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
    $("summary").innerHTML = marked.parse(r.summary);
  } catch (err) {
    S.summaryLoaded = false;
    $("summary").innerHTML = '<p class="loading">Error: ' + err.message + "</p>";
  }
}
$("gen-summary").addEventListener("click", () => { S.summaryLoaded = false; genSummary(); });

async function loadChat() {
  $("chatlog").innerHTML = "";
  try {
    const r = await (await fetch(`/api/chat/${S.docId}`)).json();
    for (const t of r.turns) {
      const div = addMsg(t.role === "user" ? "user" : "bot", t.text);
      if (t.role !== "user") { div.innerHTML = marked.parse(t.text); renderMath(div); }
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

$("chatform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("chatinput");
  const q = input.value.trim();
  const cite = chatCite;
  if ((!q && !cite) || !S.docId) return;
  input.value = "";
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
    bot.innerHTML = marked.parse(r.answer);
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

// ================= Resizable panels =================
async function refitPdf() {
  if (!S.pdf) return;
  const first = await S.pdf.getPage(1);
  const vw = first.getViewport({ scale: 1 });
  const avail = $("pdfview").clientWidth - 48;
  const ns = Math.min(2.0, Math.max(0.8, avail / vw.width));
  if (Math.abs(ns - S.scale) < 0.03) return;
  S.scale = ns;
  await buildPageShells(); // re-render at new width; translations (cached) re-fill
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
loadConfig().finally(loadLibrary);
