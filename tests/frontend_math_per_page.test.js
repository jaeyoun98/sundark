"use strict";
// Display equations are upgraded as soon as their page is painted, not after the whole document
// is translated; pages without equations make no request, and no page is fetched twice.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const i = src.indexOf("function paintFrontier(");
const code = src.slice(i, src.indexOf("\n}\n", i) + 3);
const calls = [];
const span = () => ({ textContent: "" });
const page = (equation) => ({ segs: [{ text: "s", equation }], spanByK: [span()], trBuf: ["t"], frontK: 0, loadingEl: null });
const ctx = {
  S: { frontier: 0, numPages: 3, trToken: 7, onFirstPaint: null, pages: [page(true), page(false), page(true)] },
  $: () => ({ children: [{}, {}, {}] }),
  renderMath: () => {},
  fetchMath: (k) => { calls.push(k); return Promise.resolve(); },
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
ctx.S.pages[2].trBuf = [null];          // page 3 still translating
ctx.paintFrontier();
assert.deepEqual(calls, [0], "page 1 (has an equation) upgraded right away; page 2 has none");
ctx.S.pages[2].trBuf = ["t"];
ctx.paintFrontier();
ctx.paintFrontier();
assert.deepEqual(calls, [0, 2], "page 3 upgraded once when it completes");
console.log("per-page math upgrade PASS");
