"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const fn = (name) => { const i = src.indexOf(name); assert.ok(i >= 0, name); return src.slice(i, src.indexOf("\n}\n", i) + 3); };

(async () => {
  let language = "Japanese";
  const received = [];
  const context = {
    S: { aiConfig: { backend: "claude" }, docId: "doc", trToken: 0, numPages: 3, pages: [{}, {}, {}] },
    $: () => ({ checked: true }), hide() {}, lang: () => language,
    ensureLayout: async () => { language = "English"; },
    preparePage: () => [{ start: 0, texts: ["source"] }],
    paintFrontier() {}, fetchMath: async () => {}, trPool: () => 1,
    sleep: () => new Promise((resolve) => setImmediate(resolve)),
    translateBatch: async (page, batch, force, token, target) => { received.push(target); },
  };
  vm.createContext(context);
  vm.runInContext(fn("async function translateAll("), context);
  await context.translateAll();
  assert.deepEqual(received, ["Japanese", "Japanese", "Japanese"], "every queued batch uses the run's original language");

  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, { value: "", textContent: "", innerHTML: "", appendChild() {}, replaceChildren(...children) { this.children = children; }, showModal() {} });
    return nodes.get(id);
  };
  const settings = {
    S: { aiConfig: { backend: "claude", output_language: "French", output_languages: ["Korean", "French"], backends: [{ id: "claude", label: "Claude", installed: true }] } },
    $: node, document: { createElement: () => ({}) }, lang: () => "French", fillModels() {}, hide() {}, show() {}, refreshChatGPT() {},
  };
  vm.createContext(settings);
  vm.runInContext(fn("function openSettings("), settings);
  settings.openSettings();
  assert.deepEqual(node("set-language").children.map((option) => option.value), ["Korean", "French"]);
  assert.equal(node("set-language").value, "French", "reopening settings restores the persisted choice");
  let submit;
  let saved;
  let reloaded = false;
  node("settings-form").addEventListener = (event, handler) => { assert.equal(event, "submit"); submit = handler; };
  node("set-language").value = "French";
  node("set-backend").value = "claude";
  node("set-conc").value = "4";
  node("set-models").querySelectorAll = () => [];
  settings.postJSON = async (url, body) => { assert.equal(url, "/api/settings"); saved = body; };
  settings.location = { reload: () => { reloaded = true; } };
  const submitStart = src.indexOf('$("settings-form").addEventListener("submit"');
  vm.runInContext(src.slice(submitStart, src.indexOf("\n});", submitStart) + 4), settings);
  await submit({ preventDefault() {} });
  assert.equal(saved.output_language, "French", "the sole control persists the chosen output language");
  assert.equal(reloaded, true, "successful save reloads before a new language run");
  console.log("unified output-language settings and translation snapshot PASS");
})().catch((error) => { console.error(error); process.exitCode = 1; });
