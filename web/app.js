"use strict";

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const $ = (id) => document.getElementById(id);
const model = () => $("model").value;
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
};

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

  buildPageShells();
  buildTrShells();
  genSummary();   // auto-generate the summary in the background
  loadAnatomy();  // auto-generate the Research Anatomy in the background
  translateAll(); // translate the whole document, top to bottom
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

// Translate the whole document top to bottom, one page at a time. Sequential because the
// subscription `claude` CLI isn't safe to invoke concurrently; the panel fills as it goes.
async function translateAll(force) {
  if (!S.docId || !$("autotr").checked) return;
  const token = ++S.trToken;
  for (let i = 0; i < S.numPages; i++) {
    if (token !== S.trToken) return;        // a newer doc/retranslate superseded us
    await ensureLayout(i);
    await translatePage(i, force);
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
  for (let i = 0; i < S.numPages; i++) {
    const wrap = document.createElement("div");
    wrap.className = "trpage"; wrap.dataset.page = i;
    wrap.innerHTML = '<div class="ph"></div>';
    list.appendChild(wrap);
  }
}

async function translatePage(i, force) {
  const P = S.pages[i];
  if (!P || P.translated || P.trLoading) return;
  await ensureLayout(i);
  if (!P.segs || !P.segs.length) { markTrEmpty(i); P.translated = true; return; }
  P.trLoading = true;

  const wrap = $("trlist").children[i];
  wrap.innerHTML = "";
  // Build paragraph structure: sentences of the same block flow inline in one <p>.
  // Paragraph font-size follows the source (headings bigger), scaled to the body size.
  const sizes = P.segs.map((s) => s.size).filter((x) => x > 0).sort((a, b) => a - b);
  const bodySize = sizes.length ? sizes[Math.floor(sizes.length / 2)] : 10;
  const spanByK = [];
  let para = null, curPara = null;
  P.segs.forEach((seg, k) => {
    if (!para || seg.para !== curPara) {
      para = document.createElement("div"); para.className = "trpara";
      const ratio = seg.size > 0 ? seg.size / bodySize : 1;
      para.style.fontSize = Math.max(12, Math.min(26, 13.5 * ratio)) + "px";
      if (seg.heading || ratio >= 1.22) { para.style.fontWeight = "700"; para.classList.add("trhead"); }
      wrap.appendChild(para); curPara = seg.para;
    }
    const sp = makeTrSeg(i, k, "");
    para.appendChild(sp);
    para.appendChild(document.createTextNode(" "));
    spanByK[k] = sp;
  });
  const loading = document.createElement("div");
  loading.className = "ph"; loading.textContent = "번역 중…";
  wrap.appendChild(loading);

  try {
    const segs = P.segs.map((x) => x.text);
    const res = await fetch("/api/translate_stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id: S.docId, page: i, target_lang: lang(), model: model(), segments: segs, force: !!force }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);

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
        if (spanByK[obj.i]) spanByK[obj.i].textContent = obj.tr;
      }
    }
    loading.remove();
    renderMath(wrap); // render any \( \) / \[ \] math via KaTeX
    P.translated = true;
  } catch (err) {
    loading.textContent = "Error: " + err.message;
  } finally {
    P.trLoading = false;
  }
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
function markTrEmpty(i) {
  const wrap = $("trlist").children[i];
  if (wrap) wrap.innerHTML = "";
}

// Unified: highlight one sentence on BOTH sides — a continuous bar over its lines on
// the PDF, and its translation segment in the panel.
function setHover(page, k, on) {
  const P = S.pages[page];
  const seg = P && P.segs && P.segs[k];
  if (!seg) return;
  if (P.hllayer) {
    P.hllayer.innerHTML = "";
    if (on) {
      const sc = S.scale;  // rects are in PDF points; scale to pixels
      for (const r of seg.rects) {
        const d = document.createElement("div");
        d.className = "hlrect";
        d.style.left = (r.left * sc) + "px"; d.style.top = (r.top * sc) + "px";
        d.style.width = (r.w * sc) + "px"; d.style.height = (r.h * sc) + "px";
        P.hllayer.appendChild(d);
      }
    }
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
$("model").addEventListener("change", () => {
  resetSummary(); genSummary();
  retranslateAll();
});
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
  S.pages.forEach((P) => { P.translated = false; });
  translateAll(force);
}

// ================= Tabs =================
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tabpane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "anatomy") loadAnatomy();
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
  if (!force && S.anatomyLoaded && S.anatomyModel === model()) return;
  const box = $("anatomy");
  box.innerHTML = '<p class="loading" style="padding:6px">Analyzing structure… (grounded in the source, ~10-30s)</p>';
  try {
    const d = await postJSON("/api/anatomy", { doc_id: S.docId, model: model(), force: !!force });
    renderAnatomy(d); S.anatomyLoaded = true; S.anatomyModel = model();
  } catch (err) {
    box.innerHTML = '<p class="loading" style="padding:6px">Error: ' + err.message + "</p>";
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
    const r = await postJSON("/api/summarize", { doc_id: S.docId, model: model() });
    $("summary").innerHTML = marked.parse(r.summary);
  } catch (err) {
    S.summaryLoaded = false;
    $("summary").innerHTML = '<p class="loading">Error: ' + err.message + "</p>";
  }
}
$("gen-summary").addEventListener("click", () => { S.summaryLoaded = false; genSummary(); });

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
    const r = await postJSON("/api/chat", { doc_id: S.docId, question, model: model() });
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
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
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
loadLibrary();
