import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node is only needed for JS checks")
class ChatGPTFrontendTests(unittest.TestCase):
    def test_refresh_preserves_custom_and_edited_models(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const start = src.indexOf("async function refreshChatGPT()");
const end = src.indexOf("\n}\n", start) + 3;
const code = src.slice(start, end);

async function run(values, edited, modelResponses) {
  const inputs = values.map(([task, value]) => ({
    value,
    dataset: { task, ...(edited.includes(task) ? { edited: "true" } : {}) },
  }));
  const elements = {
    "set-backend": { value: "chatgpt" },
    settings: { open: true },
    "set-models": { querySelectorAll: () => inputs },
    "suggest-chatgpt": { innerHTML: "", appendChild() {} },
  };
  let modelCall = 0;
  const context = {
    S: { aiConfig: { default_models: { chatgpt: { translate: "old-default" } } } },
    chatGPT: { busy: false, generation: 0, initialModels: true },
    $: (id) => elements[id],
    setChatGPTBusy() {}, hide() {}, renderChatGPTStatus() {}, chatGPTError(err) { throw err; },
    getChatGPTJSON: async (path) => path.endsWith("/status")
      ? { ready: true }
      : { models: modelResponses[modelCall++] },
    document: { createElement: () => ({}) },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  await context.refreshChatGPT();
  const afterFirst = inputs.map((input) => input.value);
  if (modelResponses.length > 1) await context.refreshChatGPT();
  return { afterFirst, afterSecond: inputs.map((input) => input.value) };
}

(async () => {
  const models = [[{ slug: "available-model" }]];
  const custom = await run([["translate", "my-custom-model"]], [], models);
  assert.deepEqual(custom.afterFirst, ["my-custom-model"], "saved custom models must remain selected");

  const fallback = await run([["translate", "old-default"]], [], models);
  assert.deepEqual(fallback.afterFirst, ["available-model"], "an unavailable backend default should fall back");

  const edited = await run([["translate", "old-default"]], ["translate"], models);
  assert.deepEqual(edited.afterFirst, ["old-default"], "an edited value must remain selected");

  const repeated = await run([["translate", "old-default"]], [], [
    [{ slug: "first-model" }], [{ slug: "second-model" }],
  ]);
  assert.deepEqual(repeated.afterFirst, ["first-model"]);
  assert.deepEqual(repeated.afterSecond, ["first-model"], "later refreshes must not replace the fallback");
})().catch((err) => { console.error(err); process.exitCode = 1; });
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_busy_state_restores_focus_without_stealing_it(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const start = src.indexOf("function setChatGPTBusy(");
const end = src.indexOf("\n}\n", start) + 3;
const context = {
  chatGPT: { busy: false, focusReturn: null },
  document: { body: {}, activeElement: null, hasFocus: () => true },
};
const controls = {};
for (const id of ["chatgpt-account", "chatgpt-login", "chatgpt-reconnect", "chatgpt-refresh", "chatgpt-logout"]) {
  let disabled = false;
  controls[id] = {
    isConnected: true,
    classList: { contains: () => false },
    get disabled() { return disabled; },
    set disabled(value) {
      disabled = value;
      if (value && context.document.activeElement === this) context.document.activeElement = context.document.body;
    },
    focusCalls: 0,
    focus() { this.focusCalls++; context.document.activeElement = this; },
  };
}
controls["chatgpt-settings"] = { setAttribute() {} };
context.$ = (id) => controls[id];
vm.createContext(context);
vm.runInContext(src.slice(start, end), context);

const initiating = controls["chatgpt-refresh"];
context.document.activeElement = initiating;
context.setChatGPTBusy(true);
assert.equal(controls["chatgpt-account"].disabled, true, "the account selector remains disabled while busy");
assert.equal(context.document.activeElement, context.document.body, "disabling the focused control moves focus to body");
context.setChatGPTBusy(false);
assert.equal(context.document.activeElement, initiating, "focus returns to the initiating control");
assert.equal(initiating.focusCalls, 1);

context.document.activeElement = initiating;
context.setChatGPTBusy(true);
const movedTo = {};
context.document.activeElement = movedTo;
context.setChatGPTBusy(false);
assert.equal(context.document.activeElement, movedTo, "a later user focus choice is preserved");
assert.equal(initiating.focusCalls, 1, "the previous control is not focused again");
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_add_account_action_requests_new_registration(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const start = src.indexOf("async function loginChatGPT(");
const end = src.indexOf("\n}\n", start) + 3;
const listener = src.slice(src.indexOf('$("chatgpt-login").addEventListener'),
                           src.indexOf("\n", src.indexOf('$("chatgpt-login").addEventListener')));
let click;
let payload;
const context = {
  chatGPT: { busy: false, generation: 0, status: { connected: true } },
  window: { open: () => null },
  URL,
  setChatGPTBusy() {}, clearChatGPTLink() {}, hide() {}, show() {},
  postJSON: async (_path, body) => { payload = body; return { auth_url: "https://auth.openai.com/" }; },
  $: (id) => id === "chatgpt-login"
    ? { addEventListener: (_event, callback) => { click = callback; } }
    : { href: "" },
};
vm.createContext(context);
vm.runInContext(src.slice(start, end) + "\n" + listener, context);

(async () => {
  await click();
  assert.equal(payload.new_account, true, "Add new ChatGPT account requests a new registration");
  context.chatGPT.status.connected = false;
  await click();
  assert.equal(payload.new_account, false, "Continue with ChatGPT reuses the current provisional registration");
})().catch((err) => { console.error(err); process.exitCode = 1; });
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pending_account_can_reconnect_without_becoming_active(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const extract = (signature) => {
  const start = src.indexOf(signature);
  return src.slice(start, src.indexOf("\n}\n", start) + 3);
};
const reconnectStart = src.indexOf('$("chatgpt-reconnect").addEventListener');
const reconnectListener = src.slice(reconnectStart, src.indexOf("\n});", reconnectStart) + 4);
let reconnectClick;
let loginArgs;
let selectCalls = 0;
const select = {
  innerHTML: "", value: "", options: [],
  appendChild(option) { this.options.push(option); },
};
const elements = {
  "chatgpt-status": {}, "chatgpt-account": select,
  "chatgpt-account-label": {}, "chatgpt-reconnect": {}, "chatgpt-logout": {},
  "chatgpt-login": {},
};
const context = {
  chatGPT: { status: null, pendingAccountId: null },
  document: { createElement: () => ({}) },
  $: (id) => id === "chatgpt-reconnect"
    ? { addEventListener: (_event, callback) => { reconnectClick = callback; } }
    : elements[id],
  show() {}, hide() {},
  changeChatGPTAccount() { selectCalls++; },
  loginChatGPT(accountId, newAccount = false) { loginArgs = [accountId, newAccount]; },
};
vm.createContext(context);
vm.runInContext(
  extract("function renderChatGPTStatus(") + "\n" +
  extract("function selectChatGPTAccount(") + "\n" + reconnectListener,
  context,
);

const status = {
  connected: true, active: "active-A", accounts: [
    { account_id: "active-A", email: "a@example.org", provisional: false },
    { account_id: "pending-B", email: "ChatGPT registration pending", provisional: true },
  ],
};
context.renderChatGPTStatus(status);
select.value = "pending-B";
context.selectChatGPTAccount(select.value);
assert.equal(selectCalls, 0, "choosing a pending registration must not call account selection");
assert.equal(context.chatGPT.status.active, "active-A", "the active account remains A");
context.renderChatGPTStatus(status);
assert.equal(select.value, "pending-B", "refresh preserves the locally selected pending registration");
reconnectClick();
assert.deepEqual(loginArgs, ["pending-B", false], "Reconnect targets B without requesting a new registration");
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
