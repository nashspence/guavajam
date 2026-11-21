// Browser qualification only. No script is included in any generated site.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
import axe from 'axe-core';

const [fixturePath, output] = process.argv.slice(2);
assert(fixturePath && output, 'Use tests/run_catalog_browser.py or make test-browser');
const fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
await fs.mkdir(output, {recursive: true});
const root = path.resolve(fixture.root);
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    assert(url.pathname.startsWith('/collection/'));
    const relative = decodeURIComponent(url.pathname.slice('/collection/'.length));
    const filename = path.resolve(root, relative.endsWith('/') || !relative ? relative + 'index.html' : relative);
    assert(filename.startsWith(root + path.sep));
    const content = await fs.readFile(filename);
    const types = {'.html': 'text/html; charset=utf-8', '.json': 'application/json',
      '.xml': 'application/xml', '.gfm': 'text/plain; charset=utf-8'};
    res.writeHead(200, {'Content-Type': types[path.extname(filename)] ?? 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff', 'Content-Length': content.length});
    res.end(content);
  } catch { res.writeHead(404); res.end('Not found'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}/collection/`;
const revision = fixture.revisions[1];
const edition = `editions/${revision}/`;
const first = `editions/${fixture.revisions[0]}/`;
const pages = ['index.html', 'editions/index.html', edition + 'index.html', edition + 'read/index.html',
  edition + 'read/documents.html', edition + 'read/supporting/note.html',
  first + 'index.html', first + 'read/index.html'];
const results = [];
try {
  for (const [name, engine] of Object.entries({chromium, firefox, webkit})) {
    const browser = await engine.launch();
    try {
      const context = await browser.newContext({javaScriptEnabled: false, acceptDownloads: true,
        viewport: {width: 360, height: 900}, reducedMotion: 'reduce'});
      const unexpected = [];
      context.on('request', request => { if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin) unexpected.push(request.url()); });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      await page.goto(base);
      await page.keyboard.press('Tab');
      assert.equal(await page.locator(':focus').textContent(), 'Skip to content');
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(':focus').evaluate(n => n.tagName), 'MAIN');
      await page.getByRole('link', {name: 'Previous editions', exact: true}).click();
      assert(page.url().endsWith('/editions/index.html'));
      assert.equal(await page.getByRole('link', {name: 'Edition 1', exact: true}).count(), 1);
      await page.goto(base);
      await page.getByRole('link', {name: 'Edition 2 record', exact: true}).click();
      assert(page.url().includes(edition));
      const downloaded = page.waitForEvent('download');
      await page.getByRole('link', {name: 'Download', exact: true}).click();
      const download = await downloaded;
      assert.equal(download.suggestedFilename(), 'source.gfm');
      assert.deepEqual(await fs.readFile(await download.path()), await fs.readFile(path.join(root, edition, 'read/source.gfm')));
      await page.getByRole('link', {name: 'Read', exact: true}).click();
      assert(page.url().endsWith('/read/index.html'));
      await page.getByRole('link', {name: 'the supporting claim', exact: true}).click();
      assert(page.url().endsWith('/supporting/note.html#claim'));
      assert((await page.locator('#claim').textContent()).includes('exact independently citable'));
      for (const filename of pages) {
        for (const [width, scheme, enlarge] of [[320, 'light', false], [1280, 'dark', false], [360, 'light', true]]) {
          await page.setViewportSize({width, height: 900});
          await page.emulateMedia({media: 'screen', colorScheme: scheme});
          await page.goto(base + filename);
          if (enlarge) await page.evaluate(() => { document.documentElement.style.fontSize = '34px'; document.documentElement.style.lineHeight = '1.8'; });
          const shape = await page.evaluate(() => ({scripts: document.scripts.length,
            main: document.querySelectorAll('main').length, h1: document.querySelectorAll('h1').length,
            width: document.documentElement.scrollWidth, viewport: innerWidth,
            ids: [...document.querySelectorAll('[id]')].map(n => n.id),
            csp: document.querySelector('meta[http-equiv="Content-Security-Policy"]').content}));
          assert.equal(shape.scripts, 0); assert.equal(shape.main, 1); assert.equal(shape.h1, 1);
          assert(shape.width <= shape.viewport + 1, `${name} ${filename} ${width} enlarged=${enlarge} overflow`);
          assert.equal(shape.ids.length, new Set(shape.ids).size);
          assert(shape.csp.includes("script-src 'none'"));
          results.push({engine: name, filename, width, scheme, enlarge, overflow: false});
        }
      }
      await page.goto(base + first + 'index.html');
      assert.equal(await page.getByRole('link', {name: 'Read', exact: true}).count(), 1);
      await page.getByRole('link', {name: 'Read', exact: true}).click();
      assert((await page.locator('main').textContent()).includes('The first observation.'));
      await page.emulateMedia({media: 'print', colorScheme: 'light'});
      assert.equal(await page.locator('main').isVisible(), true);
      assert.deepEqual(unexpected, [], `${name}: unexpected external requests`);
      await context.close();
      // axe runs in a separate instrumentation context; CSP is not bypassed.
      const auditContext = await browser.newContext({bypassCSP: false, viewport: {width: 360, height: 900}});
      const review = await auditContext.newPage();
      for (const filename of pages) {
        await review.goto(base + filename);
        await review.evaluate(axe.source);
        const audit = await review.evaluate(() => axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}}));
        assert.deepEqual(audit.violations.map(v => ({id: v.id, targets: v.nodes.map(n => n.target)})), [], `${name}: accessibility ${filename}`);
      }
      await review.goto(base);
      await review.screenshot({path: path.join(output, name + '-catalog.png'), fullPage: true});
      await auditContext.close();
      console.log(name + ': edition history, source citation, downloads, reflow, print and accessibility passed');
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
await fs.writeFile(path.join(output, 'catalog-browser-report.json'), JSON.stringify({checks: results}, null, 2) + '\n');
console.log(`${results.length} script-disabled presentation checks passed.`);
