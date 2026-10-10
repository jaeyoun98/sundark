"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const pick = (name, end) => src.slice(src.indexOf(name), src.indexOf(end, src.indexOf(name)));
const line = (name) => src.slice(src.indexOf(name), src.indexOf("\n", src.indexOf(name)));
const code = [
  line("class RateLimited"),
  pick("async function streamTranslate(", "\n}\n") + "\n}\n",
  pick("function fillSource(", "\n}\n") + "\n}\n",
  pick("async function translateBatch(", "\n}\n") + "\n}\n",
].join("\n");

(async () => {
  let calls = 0;
  const page = { segs: [{ text: "S0" }, { text: "S1" }], trBuf: [], spanByK: [] };
  const context = {
    S: { docId: "d", trToken: 1, pages: [page], rateLimited: null },
    lang: () => "Korean", sleep: async () => {}, paintFrontier: () => {}, showRateLimit: () => {},
    TextDecoder, JSON, Error,
    fetch: async () => {
      calls++;
      return new Response('{"i":0,"tr":"가"}\n{"error":"limit","rate_limited":true}\n');
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  await context.translateBatch(0, { start: 0, texts: ["S0", "S1"] }, false, 1);
  assert.equal(calls, 1, "a rate-limited batch must not be retried");
  assert.deepEqual(page.trBuf, ["가", "S1"]);
  assert.equal(context.S.rateLimited, 1);
  console.log("rate-limit frontend check PASS");
})().catch((e) => { console.error(e); process.exitCode = 1; });
