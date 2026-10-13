"use strict";
// fetchMath must not replace a segment with LaTeX KaTeX cannot render; the original (the
// translation's own math) stays.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const i = src.indexOf("async function fetchMath(");
const code = src.slice(i, src.indexOf("\n}\n", i) + 3);
const label = { removed: false, classList: { contains: (c) => c === "treqno" }, remove() { this.removed = true; } };
const spans = { 3: { textContent: "\\(x\\) 번역", dataset: {} }, 5: { textContent: "\\(y\\) 번역", dataset: { eqno: "2" }, nextElementSibling: label } };
const wrap = { querySelector: (sel) => spans[Number(/data-i="(\d+)"/.exec(sel)[1])] };
const ctx = {
  S: { docId: "d" },
  $: () => ({ children: [wrap] }),
  renderMath: () => {},
  window: { katex: { renderToString: (tex) => { if (tex.includes("\\end{final")) throw new Error("KaTeX parse error"); return "<span/>"; } } },
  fetch: async () => ({ ok: true, json: async () => ({ equations: [
    { i: 3, latex: "\\mathbf{x}_n = A\\mathbf{x}_{n-1},\\end{final" },
    { i: 5, latex: "y = 1" },
  ] }) }),
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
(async () => {
  await ctx.fetchMath(0);
  assert.equal(spans[3].textContent, "\\(x\\) 번역", "unrenderable LaTeX is not swapped in");
  assert.equal(spans[5].textContent, "\\[ y = 1 \\tag{2} \\]", "valid LaTeX replaces the segment, keeping its number");
  assert.ok(label.removed, "the interim number label gives way to \\tag");
  console.log("math guard PASS");
})().catch((e) => { console.error(e); process.exitCode = 1; });
