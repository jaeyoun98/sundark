"use strict";
// After a rate limit, batches still waiting in the queue must be filled with source text so the
// strict-order frontier can move past them; otherwise the rest of the paper stays blank forever.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const fn = (name) => { const i = src.indexOf(name); assert.ok(i >= 0, name); return src.slice(i, src.indexOf("\n}\n", i) + 3); };
const line = (name) => src.slice(src.indexOf(name), src.indexOf("\n", src.indexOf(name)));
const code = [line("class RateLimited"), fn("async function streamTranslate("), fn("function fillSource("),
  fn("async function translateBatch("), fn("async function translateAll(")].join("\n");

(async () => {
  let calls = 0;
  const pages = [0, 1].map((p) => ({ segs: [{ text: `P${p}S0` }, { text: `P${p}S1` }], trBuf: [], spanByK: [] }));
  const el = { checked: true, classList: { add() {}, remove() {} } };
  const context = {
    S: { docId: "d", trToken: 0, numPages: 2, pages, rateLimited: null, aiConfig: { backend: "hermes", concurrency: { hermes: 1 } } },
    $: () => el, hide: () => {}, lang: () => "Korean", sleep: async () => {}, paintFrontier: () => {},
    showRateLimit: () => {}, ensureLayout: async () => {}, fetchMath: async () => {}, trPool: () => 1,
    preparePage: (i) => [{ start: 0, texts: pages[i].segs.map((s) => s.text) }],
    TextDecoder, JSON, Error, Array, Promise,
    fetch: async () => { calls++; return new Response('{"error":"limit","rate_limited":true}\n'); },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  await context.translateAll();
  assert.equal(calls, 1, "no request after the rate limit");
  assert.deepEqual(pages[1].trBuf, ["P1S0", "P1S1"], "queued batch filled with source text");
  console.log("rate-limit queue drain check PASS");
})().catch((e) => { console.error(e); process.exitCode = 1; });
