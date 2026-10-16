"use strict";
// Exercise the production wheel listener and page geometry without a PDF/AI request.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const code = src.slice(
  src.indexOf("const PT_PX ="),
  src.indexOf("// ================= Resizable panels"),
);

function fixture() {
  const listeners = new Map(),
    frames = [],
    saved = new Map();
  const view = {
    clientWidth: 600,
    clientHeight: 500,
    scrollLeft: 180,
    scrollTop: 1400,
    getBoundingClientRect: () => ({ left: 100, top: 80 }),
  };
  const elements = new Map([["pdfview", view]]);
  const S = { pdf: {}, scale: 2, zoom: { mode: "pct", pct: 150 }, page1: { w: 600, h: 800 } };
  S.pages = Array.from({ length: 3 }, (_, i) => {
    const P = {
      w1: 600,
      h1: 800,
      segs: [{ text: "keep" }],
      trBuf: ["translation"],
      hllayer: { innerHTML: "highlight" },
      el: { style: { width: "1200px", height: "1600px" } },
    };
    P.el.getBoundingClientRect = () => ({
      left:
        100 + Math.max(0, (view.clientWidth - parseFloat(P.el.style.width)) / 2) - view.scrollLeft,
      top:
        80 +
        22 +
        S.pages.slice(0, i).reduce((y, page) => y + parseFloat(page.el.style.height) + 18, 0) -
        view.scrollTop,
    });
    return P;
  });
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, { classList: { toggle() {} } });
    const el = elements.get(id);
    el.addEventListener = (event, fn, options) => listeners.set(`${id}:${event}`, { fn, options });
    return el;
  };
  const context = {
    S,
    $,
    clearPdfHover() {},
    stopRender() {},
    observePages() {},
    localStorage: {
      getItem: (key) => saved.get(key),
      setItem: (key, value) => saved.set(key, value),
    },
    requestAnimationFrame: (fn) => {
      frames.push(fn);
      return frames.length;
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  function wheel(overrides = {}) {
    const e = {
      ctrlKey: true,
      cancelable: true,
      deltaY: -40,
      deltaMode: 0,
      clientX: 310,
      clientY: 360,
      defaultPrevented: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
      ...overrides,
    };
    listeners.get("pdfview:wheel").fn(e);
    return e;
  }
  return {
    context,
    S,
    view,
    listeners,
    saved,
    wheel,
    flush: () => {
      while (frames.length) frames.shift()();
    },
  };
}

function point(f, page, x, y) {
  const rect = f.S.pages[page].el.getBoundingClientRect();
  return { x: (x - rect.left) / f.S.scale, y: (y - rect.top) / f.S.scale };
}
function close(a, b) {
  assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);
}

// Input stays local to the PDF; plain scrolling, empty PDFs and noncancelable events pass through.
{
  const f = fixture();
  assert.equal(f.listeners.get("pdfview:wheel").options.passive, false);
  assert.equal(
    [...f.listeners.keys()].filter((key) => key.endsWith(":wheel")).join(),
    "pdfview:wheel",
  );
  for (const overrides of [{ ctrlKey: false }, { cancelable: false }, { deltaY: 0 }]) {
    assert.equal(f.wheel(overrides).defaultPrevented, false);
  }
  f.S.pdf = null;
  assert.equal(f.wheel().defaultPrevented, false);
  f.flush();
  assert.equal(f.S.scale, 2);
}

// Pointer anchoring works on later pages too, and translating pages keep their object identity.
{
  const f = fixture(),
    pages = f.S.pages,
    page = pages[1],
    segments = page.segs,
    buffer = page.trBuf;
  const before = point(f, 1, 310, 360);
  assert.equal(f.wheel().defaultPrevented, true);
  f.flush();
  assert.ok(f.S.scale > 2);
  const after = point(f, 1, 310, 360);
  close(before.x, after.x);
  close(before.y, after.y);
  assert.equal(f.S.pages, pages);
  assert.equal(f.S.pages[1], page);
  assert.equal(page.segs, segments);
  assert.equal(page.trBuf, buffer);
  assert.equal(JSON.parse(f.saved.get("pdfZoom")).mode, "pct");
  const enlarged = f.S.scale;
  f.wheel({ deltaY: 40 });
  f.flush();
  assert.ok(f.S.scale < enlarged);
  close(f.S.scale, 2);
}

// Pixel/line/page deltas are normalized; bursts are redrawn once, not once per event.
{
  const f = fixture();
  f.wheel({ deltaY: -1, deltaMode: 1 });
  f.flush();
  const pixels = fixture();
  pixels.wheel({ deltaY: -16 });
  pixels.flush();
  close(f.S.scale, pixels.S.scale);
  const burst = fixture();
  let redraws = 0;
  burst.context.observePages = () => redraws++;
  burst.wheel({ deltaY: -20 });
  burst.wheel({ deltaY: -20 });
  burst.flush();
  assert.equal(redraws, 1);
  close(burst.S.scale, 2 * Math.exp(40 * 0.002));
  const page = fixture();
  page.wheel({ deltaY: -1, deltaMode: 2 });
  page.flush();
  close(page.S.scale, 2 * Math.exp(100 * 0.002));
}

// Slow pinches must accumulate across frames even when each change is too small to redraw.
for (const deltaY of [-0.1, 0.1]) {
  const f = fixture(),
    initial = f.S.scale;
  const before = point(f, 1, 310, 360);
  f.wheel({ deltaY });
  f.flush();
  assert.equal(f.S.scale, initial, "one tiny delta stays below the redraw threshold");
  for (let i = 1; i < 100; i++) {
    assert.equal(f.wheel({ deltaY }).defaultPrevented, true);
    f.flush();
  }
  const expected = initial * Math.exp(-100 * deltaY * 0.002);
  close((f.S.zoom.pct / 100) * (96 / 72), expected);
  assert.ok(
    Math.abs(f.S.scale - expected) < 0.001,
    "sub-threshold changes accumulate into redraws",
  );
  const after = point(f, 1, 310, 360);
  close(before.x, after.x);
  close(before.y, after.y);
}

// A fit mode starts from the displayed scale, not its stale saved percentage.
{
  const f = fixture();
  f.context.setZoom({ mode: "width", pct: 250 });
  const initial = f.S.scale;
  for (let i = 0; i < 100; i++) {
    f.wheel({ deltaY: 0.1 });
    f.flush();
  }
  close((f.S.zoom.pct / 100) * (96 / 72), initial * Math.exp(-0.02));
}

// At both limits Ctrl+wheel still cannot trigger whole-app zoom. Buttons retain center anchoring.
{
  const f = fixture();
  for (const pct of [50, 300]) {
    f.context.setZoom({ mode: "pct", pct });
    assert.equal(f.wheel({ deltaY: pct === 50 ? 100 : -100 }).defaultPrevented, true);
    f.flush();
    close(f.S.scale, (pct / 100) * (96 / 72));
  }
  const buttons = fixture(),
    before = point(buttons, 1, 400, 330);
  buttons.listeners.get("zoom-in:click").fn();
  const after = point(buttons, 1, 400, 330);
  close(before.x, after.x);
  close(before.y, after.y);
  assert.equal(buttons.S.zoom.pct, 175);
}
console.log("PDF wheel zoom, pointer anchor and browser-zoom boundaries PASS");
