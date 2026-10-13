"use strict";
// Zotero collections come back flat with a parent key; the picker lists them as a tree.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const i = src.indexOf("function zoteroCollectionOptions(");
assert.ok(i >= 0, "zoteroCollectionOptions() exists");
const ctx = {};
vm.createContext(ctx);
vm.runInContext(src.slice(i, src.indexOf("\n}\n", i) + 3), ctx);
const out = ctx.zoteroCollectionOptions([
  { key: "B", name: "Beta", parent: null, count: 2 },
  { key: "A1", name: "Alpha child", parent: "A", count: 1 },
  { key: "A", name: "Alpha", parent: null, count: 5 },
  { key: "A1x", name: "Grandchild", parent: "A1", count: 0 },
  { key: "O", name: "Orphan", parent: "MISSING", count: 1 },
]);
assert.deepEqual(JSON.parse(JSON.stringify(out)), [
  { key: "A", label: "Alpha (5)" },
  { key: "A1", label: "  Alpha child (1)" },
  { key: "A1x", label: "    Grandchild (0)" },
  { key: "B", label: "Beta (2)" },
  { key: "O", label: "Orphan (1)" },
]);
console.log("zotero collection tree PASS");
