const { test, expect } = require("@playwright/test");
const path = require("node:path");
const fs = require("node:fs");
const { execFileSync } = require("node:child_process");

// Use production-pinned assets without relying on CDN availability.
test.beforeEach(async ({ page }) => {
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1")
      throw new Error(`Unexpected external request: ${url.hostname}`);
    return route.continue();
  });
  await page.route("https://cdnjs.cloudflare.com/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    let file;
    if (pathname.includes("/pdf.js/3.11.174/"))
      file = path.join(
        path.dirname(require.resolve("pdfjs-dist/package.json")),
        "build",
        path.basename(pathname),
      );
    if (pathname.includes("/marked/12.0.2/"))
      file = path.join(
        path.dirname(require.resolve("marked/package.json")),
        path.basename(pathname),
      );
    if (pathname.includes("/KaTeX/0.16.11/"))
      file = path.join(
        path.dirname(require.resolve("katex/package.json")),
        "dist",
        pathname.split("/KaTeX/0.16.11/")[1],
      );
    if (!file || !fs.existsSync(file)) throw new Error(`Unmapped CDN asset: ${pathname}`);
    await route.fulfill({ path: file });
  });
});

async function setLanguage(page, language) {
  const reopen = await page.locator("#pdfcol").isVisible();
  await page.locator("#ai-badge").click();
  await expect(page.locator("#settings")).toBeVisible();
  await page.getByLabel("Output language").selectOption(language);
  await Promise.all([page.waitForEvent("load"), page.locator("#settings-save").click()]);
  await expect(page.locator("#settings")).not.toBeVisible();
  if (reopen) await page.locator("#paperlist li").filter({ hasText: "Browser Study" }).click();
}

async function translation(page, prose) {
  await page.locator('[data-tab="translation"]').click();
  await expect(page.locator("#trlist .trseg")).toHaveCount(9);
  await expect
    .poll(async () =>
      page
        .locator("#trlist .trseg")
        .evaluateAll(
          (nodes, expected) => nodes.every((n) => n.textContent.includes(expected)),
          prose,
        ),
    )
    .toBe(true);
}

async function outputs(page, prose) {
  await translation(page, prose);
  await page.locator('[data-tab="summary"]').click();
  await expect(page.locator("#summary")).toContainText(prose);
  await page.locator('[data-tab="anatomy"]').click();
  await expect(page.locator("#anatomy")).toContainText(prose);
  await page.locator('[data-tab="chat"]').click();
  const bots = page.locator("#chatlog .bot");
  const previous = await bots.count();
  await page.locator("#chatinput").fill("What does this study compare?");
  await page.locator("#chatinput").press("Control+Enter");
  await expect(bots).toHaveCount(previous + 1);
  await expect(bots.last()).toContainText(prose);
}

test("upload, render, generate, switch language, reload and reopen", async ({ page, request }) => {
  const errors = [];
  page.on("requestfailed", (req) => {
    if (req.failure().errorText !== "net::ERR_ABORTED")
      errors.push(`${req.failure().errorText} ${req.url()}`);
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  await page.goto("/");
  await setLanguage(page, "Japanese");
  const pdf = await request.get("/__e2e/sample.pdf");
  await page.locator("#file").setInputFiles({
    name: "browser-study.pdf",
    mimeType: "application/pdf",
    buffer: await pdf.body(),
  });
  await expect(page.locator("#pdfview canvas").first()).toBeVisible();
  await expect
    .poll(() =>
      page
        .locator("#pdfview canvas")
        .first()
        .evaluate((canvas) => {
          const pixels = canvas
            .getContext("2d")
            .getImageData(0, 0, canvas.width, canvas.height).data;
          let ink = 0;
          for (let i = 0; i < pixels.length; i += 4)
            if (pixels[i] < 100 && pixels[i + 3] > 0) ink++;
          return ink;
        }),
    )
    .toBeGreaterThan(100);
  await outputs(page, "研究の結果を説明する");
  const japaneseCalls = await (await request.get("/__e2e/calls")).json();
  expect(japaneseCalls["translate:Japanese"]).toBeGreaterThan(1);
  for (const task of ["summary", "anatomy", "brief", "chat"]) {
    expect(japaneseCalls[`${task}:Japanese`]).toBe(1);
  }
  await setLanguage(page, "Korean");
  await outputs(page, "연구 결과를 설명한다");
  await expect(page.locator("#summary")).not.toContainText("研究の結果を説明する");
  await expect(page.locator("#chatlog")).toContainText("研究の結果を説明する");
  await setLanguage(page, "Japanese");
  await translation(page, "研究の結果を説明する");
  await page.locator('[data-tab="summary"]').click();
  await expect(page.locator("#summary")).toContainText("研究の結果を説明する");
  await page.locator('[data-tab="anatomy"]').click();
  await expect(page.locator("#anatomy")).toContainText("研究の結果を説明する");
  const cachedCalls = await (await request.get("/__e2e/calls")).json();
  for (const task of ["translate", "summary", "anatomy", "brief"]) {
    expect(cachedCalls[`${task}:Japanese`]).toBe(japaneseCalls[`${task}:Japanese`]);
  }
  await setLanguage(page, "Korean");
  await page.reload();
  await page.locator("#ai-badge").click();
  await expect(page.getByLabel("Output language")).toHaveValue("Korean");
  await page.locator("#settings-cancel").click();
  await page.locator("#paperlist li").first().click();
  await expect(page.locator("#pdfview canvas").first()).toBeVisible();
  await outputs(page, "연구 결과를 설명한다");
  await page.locator('[data-tab="summary"]').click();
  await expect(page.locator("#export-review")).toBeEnabled();
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#export-review").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  expect(await download.failure()).toBeNull();
  const markdown = execFileSync(
    "uv",
    [
      "run",
      "--no-sync",
      "python",
      "-c",
      'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(z.read(next(n for n in z.namelist() if n.endswith("/index.md"))).decode())',
      await download.path(),
    ],
    { encoding: "utf8" },
  );
  expect(markdown).toContain("연구 결과를 설명한다");
  expect(markdown).not.toContain("研究の結果を説明する");
  expect(errors).toEqual([]);
});
