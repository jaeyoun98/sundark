// Real-DOM/clipboard regression test. Test-only dependencies can live outside the repo:
// npm install --prefix /tmp/sundark-browser playwright katex@0.16.11
// NODE_PATH=/tmp/sundark-browser/node_modules node tests/frontend_copy_math.browser.test.js
// Uses installed Chrome; set BROWSER_CHANNEL for another Playwright browser channel.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const { chromium } = require('playwright');
const katex = require('katex');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const handler = source.slice(source.indexOf('// Copy math as LaTeX'), source.indexOf('function renderMath(el)'));
assert(handler.includes('document.addEventListener("copy"'));
const formula = katex.renderToString('O(\\sqrt{d})');
const pi = katex.renderToString('\\pi');
const display = katex.renderToString('x^2', { displayMode: true });
const fixture = ['trlist', 'summary', 'chatlog', 'anatomy', 'pdfview'].map(id =>
  `<div id="${id}"><p>우리 알고리즘은 ${formula}회의 반복으로 ${pi}에 가까운 표본.</p><p>다음<br>줄 ${display}</p><p>일반 텍스트</p></div>`
).join('') + '<textarea id="input"></textarea><div id="paste" contenteditable="true"></div>';

(async () => {
  const server = http.createServer((req, res) => { res.end('<!doctype html><meta charset="utf-8">'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome' });
    const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.setContent(fixture);
    // Reproduce the old rich-text failure using the exact production KaTeX version.
    await page.addScriptTag({ path: require.resolve('katex/dist/contrib/copy-tex.min.js') });
    const baseline = await page.evaluate(() => {
      const r = document.createRange(); r.selectNodeContents(document.querySelector('#trlist p'));
      getSelection().removeAllRanges(); getSelection().addRange(r);
      const data = new DataTransfer();
      document.dispatchEvent(new ClipboardEvent('copy', { clipboardData: data, cancelable: true }));
      return { plain: data.getData('text/plain'), html: data.getData('text/html') };
    });
    assert(baseline.html.includes('katex-html'), 'old HTML flavor retains glyph markup');
    assert(baseline.plain.includes('\\sqrt{d}'));
    await page.reload();
    await page.setContent(fixture);
    await page.addScriptTag({ content: handler });
    let checks = 0;
    for (const pane of ['trlist', 'summary', 'chatlog', 'anatomy']) {
      for (const mode of ['whole', 'partial', 'head', 'tail', 'display', 'paragraphs', 'spaces', 'plain', 'collapsed', 'cancelled']) {
        const result = await page.evaluate(({ pane, mode }) => {
          const root = document.getElementById(pane), p = root.querySelector('p');
          const glyph = p.querySelector('.katex-html .mord').firstChild;
          const r = document.createRange(); r.selectNodeContents(p);
          if (mode === 'partial') { r.setStart(glyph, 0); r.setEnd(glyph, 1); }
          if (mode === 'head') r.setStart(glyph, 0);
          if (mode === 'tail') r.setEnd(glyph, 1);
          if (mode === 'spaces') { p.prepend('  '); p.append('  '); r.selectNodeContents(p); }
          if (mode === 'display') r.selectNode(root.querySelector('.katex-display'));
          if (mode === 'paragraphs') r.selectNodeContents(root);
          if (mode === 'plain') r.selectNodeContents(root.lastElementChild);
          if (mode === 'collapsed') r.collapse(true);
          getSelection().removeAllRanges(); getSelection().addRange(r);
          const before = root.innerHTML, selected = getSelection().toString();
          const data = new DataTransfer();
          const event = new ClipboardEvent('copy', { clipboardData: data, cancelable: true });
          if (mode === 'cancelled') event.preventDefault();
          document.dispatchEvent(event);
          return { plain: data.getData('text/plain'), html: data.getData('text/html'),
            unchanged: before === root.innerHTML && selected === getSelection().toString() };
        }, { pane, mode });
        assert(result.unchanged, `${pane}/${mode}: selection and live DOM preserved`);
        if (['plain', 'collapsed', 'cancelled'].includes(mode)) {
          assert.equal(result.plain, ''); assert.equal(result.html, '');
        } else {
          assert(!result.html.includes('katex'), `${pane}/${mode}: no rendered math markup`);
          const expected = mode === 'display' ? '\\[x^2\\]' : '$O(\\sqrt{d})$';
          assert.equal(result.plain.split(expected).length - 1, 1, `${pane}/${mode}: formula once`);
          if (mode === 'partial') assert.equal(result.plain, expected);
          if (mode === 'spaces') assert(result.plain.startsWith('  ') && result.plain.endsWith('  '));
          if (mode === 'paragraphs') {
            assert(result.html.includes('<br>')); assert(result.plain.includes('다음\n줄'));
            assert(result.plain.includes('\\[x^2\\]'));
          }
        }
        checks++;
      }
    }
    // Scoped away from PDF and cross-pane selections; inputs retain native copy.
    for (const mode of ['pdf', 'cross-pane', 'input']) {
      const intercepted = await page.evaluate(mode => {
        const r = document.createRange();
        r.selectNodeContents(document.getElementById(mode === 'pdf' ? 'pdfview' : 'trlist'));
        if (mode === 'cross-pane') r.setEndAfter(document.getElementById('summary'));
        getSelection().removeAllRanges(); getSelection().addRange(r);
        if (mode === 'input') { const input = document.getElementById('input'); input.value = 'raw $x$'; input.focus(); input.select(); }
        const event = new ClipboardEvent('copy', { clipboardData: new DataTransfer(), cancelable: true });
        document.dispatchEvent(event); document.activeElement.blur(); return event.defaultPrevented;
      }, mode);
      assert.equal(intercepted, false, mode); checks++;
    }
    await page.evaluate(html => { document.body.innerHTML = html; }, fixture);
    // Actual browser clipboard and rich-text paste, repeated without mutating source.
    for (let i = 0; i < 2; i++) {
      await page.evaluate(() => {
        const r = document.createRange(); r.selectNodeContents(document.querySelector('#trlist p'));
        getSelection().removeAllRanges(); getSelection().addRange(r);
        if (!document.execCommand('copy')) throw Error('Native copy failed');
      });
      const clipboard = await page.evaluate(async () => {
        const item = (await navigator.clipboard.read())[0];
        return { plain: await (await item.getType('text/plain')).text(), html: await (await item.getType('text/html')).text() };
      });
      assert.equal(clipboard.plain, '우리 알고리즘은 $O(\\sqrt{d})$회의 반복으로 $\\pi$에 가까운 표본.');
      assert(!clipboard.html.includes('katex'));
      await page.locator('#paste').fill('');
      await page.locator('#paste').focus();
      await page.keyboard.press('Meta+v');
      assert.equal(await page.locator('#paste').innerText(), clipboard.plain);
      await page.locator('#paste').blur();
      checks++;
    }
    console.log(`PASS: reproduced original HTML failure; ${checks} Chrome copy/paste regression checks`);
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
