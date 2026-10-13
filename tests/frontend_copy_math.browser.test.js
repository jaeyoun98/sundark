// Full static app + native clipboard test; no AI backend or external content writes.
// npm install --prefix /tmp/sundark-browser --save-exact playwright katex@0.16.11 marked@12.0.2 pdfjs-dist@3.11.174
// NODE_PATH=/tmp/sundark-browser/node_modules node tests/frontend_copy_math.browser.test.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");
const root = path.join(__dirname, "../web");
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
const start = app.indexOf("// copy-tex writes LaTeX"),
  end = app.indexOf("function renderMath(el)", start);
assert(start >= 0 && end > start);
const baseline = app.slice(0, start) + app.slice(end);
const sample =
  "우리 알고리즘은 \\(O(\\sqrt{d})\\)회의 병렬 반복으로 \\(\\pi\\)에 가까운 표본을 생성하며 …";
const expected =
  "우리 알고리즘은 $O(\\sqrt{d})$회의 병렬 반복으로 $\\pi$에 가까운 표본을 생성하며 …";
const assets = {
  "/ajax/libs/pdf.js/3.11.174/pdf.min.js": require.resolve("pdfjs-dist/build/pdf.min.js"),
  "/ajax/libs/marked/12.0.2/marked.min.js": require.resolve("marked/marked.min.js"),
};
const katexRoot = path.dirname(require.resolve("katex/package.json"));
assert.equal(require("katex/package.json").version, "0.16.11");

(async () => {
  const server = http.createServer((req, res) => {
    if (req.url === "/api/config")
      return res.end(
        JSON.stringify({ backend: "claude", backends: [{ id: "claude", label: "Test" }] }),
      );
    if (req.url === "/api/papers") return res.end(JSON.stringify({ dir: "test", papers: [] }));
    if (req.url === "/paste")
      return res.end(
        '<!doctype html><meta charset="utf-8"><div id="rich" contenteditable="true"></div>',
      );
    const file = { "/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css" }[req.url];
    if (!file) {
      res.writeHead(404);
      return res.end();
    }
    res.setHeader(
      "Content-Type",
      file.endsWith("js")
        ? "application/javascript"
        : file.endsWith("css")
          ? "text/css"
          : "text/html; charset=utf-8",
    );
    res.end(fs.readFileSync(path.join(root, file)));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || "chrome" });
    const context = await browser.newContext({
      permissions: ["clipboard-read", "clipboard-write"],
    });
    const origin = `http://127.0.0.1:${server.address().port}`;
    // Serve exact production CDN versions locally; reject every other external request.
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin === origin) return route.continue();
      const prefix = "/ajax/libs/KaTeX/0.16.11/";
      const file =
        assets[url.pathname] ||
        (url.pathname.startsWith(prefix) &&
          path.join(katexRoot, "dist", url.pathname.slice(prefix.length)));
      if (url.hostname !== "cdnjs.cloudflare.com" || !file) return route.abort();
      await route.fulfill({ path: file });
    });
    const page = await context.newPage(),
      paste = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await paste.goto(origin + "/paste");
    async function load(old = false) {
      if (old)
        await page.route("**/app.js", (route) =>
          route.fulfill({ contentType: "application/javascript", body: baseline }),
        );
      else await page.unroute("**/app.js");
      await page.goto(origin);
      await page.waitForFunction(() => window.S && window.renderMathInElement);
      await page.evaluate((text) => {
        document.getElementById("aipanel").classList.remove("hidden");
        for (const id of ["trlist", "summary", "chatlog", "anatomy"]) {
          const el = document.getElementById(id);
          el.innerHTML = "<p><strong>" + text + "</strong><br>다음 줄</p><p>\\[x^2\\]</p>";
          renderMath(el); // production renderer, not a synthetic KaTeX DOM
        }
      }, sample);
    }
    async function copy(pane, mode) {
      await page.bringToFront();
      await page.evaluate(
        ({ pane, mode }) => {
          const el = document.getElementById(pane),
            r = document.createRange();
          r.selectNodeContents(el.querySelector("strong"));
          if (mode === "formatted") r.selectNodeContents(el.firstElementChild);
          if (mode === "display") r.selectNode(el.querySelector(".katex-display"));
          if (["partial", "head", "tail"].includes(mode)) {
            const glyph = el.querySelector(".katex-html .mord").firstChild;
            if (mode !== "tail") r.setStart(glyph, 0);
            if (mode !== "head") r.setEnd(glyph, 1);
          }
          getSelection().removeAllRanges();
          getSelection().addRange(r);
          if (!document.execCommand("copy")) throw Error("Native copy failed");
        },
        { pane, mode },
      );
      return page.evaluate(async () => {
        const item = (await navigator.clipboard.read())[0];
        return {
          plain: await (await item.getType("text/plain")).text(),
          html: await (await item.getType("text/html")).text(),
        };
      });
    }
    await load(true);
    const old = await copy("trlist", "whole");
    assert.equal(old.plain, expected);
    assert(old.html.includes("katex-html"));
    await load();
    let checks = 0;
    for (const pane of ["trlist", "summary", "chatlog", "anatomy"]) {
      await page
        .locator(
          `[data-tab="${{ trlist: "translation", summary: "summary", chatlog: "chat", anatomy: "anatomy" }[pane]}"]`,
        )
        .click();
      for (const mode of ["whole", "partial", "head", "tail", "display", "formatted"]) {
        const data = await copy(pane, mode);
        const tex = mode === "display" ? "$$x^2$$" : "$O(\\sqrt{d})$";
        assert(!data.html.includes("katex"));
        assert.equal(data.html.split(tex).length - 1, 1);
        assert.equal(data.plain.split(tex).length - 1, 1);
        if (mode === "whole") assert.equal(data.plain, expected);
        if (mode === "partial") assert.equal(data.plain, tex);
        if (mode === "formatted")
          assert(data.html.includes("<strong>") && data.html.includes("<br>"));
        await paste.bringToFront();
        await paste.locator("#rich").fill("");
        await paste.locator("#rich").press(process.platform === "darwin" ? "Meta+v" : "Control+v");
        const text = await paste.locator("#rich").innerText();
        assert.equal(text.split(tex).length - 1, 1);
        if (mode === "whole") assert.equal(text, expected);
        if (mode === "formatted") assert(text.includes("\n다음 줄"));
        checks++;
      }
    }
    // Repeat after reloading the full app: deferred copy-tex must still register first.
    await load();
    assert.equal((await copy("trlist", "whole")).plain, expected);
    const repeated = await copy("trlist", "whole");
    assert(!repeated.html.includes("katex"));
    // A text input copy remains native and must not reuse a previous math payload.
    await page.locator('[data-tab="chat"]').click();
    await page.locator("#chatinput").fill("plain input");
    await page.locator("#chatinput").press(process.platform === "darwin" ? "Meta+a" : "Control+a");
    await page.evaluate(() => document.execCommand("copy"));
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "plain input");
    await page.locator('[data-tab="translation"]').click();
    assert.equal((await copy("trlist", "whole")).plain, expected);
    assert.deepEqual(errors, []);
    console.log(
      `PASS: original HTML failure reproduced; ${checks} full-app copy/rich-paste cases, reload/repeat and native input copy`,
    );
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
