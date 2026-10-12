"use strict";
// Markdown must not eat LaTeX: marked turns "\(" into "(" and "_" into <em>, so math is shielded
// from the markdown pass and restored (HTML-escaped) for KaTeX to render afterwards.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const i = src.indexOf("function mdMath(");
assert.ok(i >= 0, "mdMath() exists");
const code = src.slice(i, src.indexOf("\n}\n", i) + 3);
// A stand-in for marked with the two behaviours that break math.
const fakeMarked = { parse: (s) => "<p>" + s.replace(/\\([()[\]])/g, "$1").replace(/_(\w+)_/g, "<em>$1</em>").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</p>" };
const ctx = { marked: fakeMarked };
vm.createContext(ctx);
vm.runInContext(code, ctx);
const out = ctx.mdMath("**핵심**: \\(n\\approx6\\) 이고 \\([A^m]_{ij}_x_\\) 이며 a_b_c.\n\n\\[\nx<y \\quad \\text{for } i\n\\]\n$$E=mc^2$$");
assert.ok(out.includes("<strong>핵심</strong>"), "markdown still applies: " + out);
assert.ok(out.includes("\\(n\\approx6\\)"), "inline math kept: " + out);
assert.ok(out.includes("\\([A^m]_{ij}_x_\\)"), "underscores inside math untouched: " + out);
assert.ok(out.includes("a<em>b</em>c"), "underscores outside math still markdown: " + out);
assert.ok(out.includes("\\[\nx&lt;y \\quad \\text{for } i\n\\]"), "display math kept and HTML-escaped: " + out);
assert.ok(out.includes("$$E=mc^2$$"), "$$ math kept: " + out);
console.log("markdown math shielding PASS");
