// Real SQLite WASM, real signed inputs, and ordinary static HTTP without COOP/COEP.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
import axe from 'axe-core';

const [fixturePath, output] = process.argv.slice(2);
assert(fixturePath && output, 'Use projects/view/tests/run_search_browser.py');
const fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
await fs.mkdir(output, {recursive: true});
const root = path.resolve(fixture.root);
const requests = [];
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    assert(url.pathname.startsWith('/collection/'));
    const relative = decodeURIComponent(url.pathname.slice('/collection/'.length));
    const filename = path.resolve(root, relative.endsWith('/') || !relative ? relative + 'index.html' : relative);
    assert(filename.startsWith(root + path.sep));
    const content = await fs.readFile(filename);
    requests.push(relative);
    const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
      '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.sqlite': 'application/vnd.sqlite3'};
    res.writeHead(200, {'Content-Type': types[path.extname(filename)] ?? 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff', 'Content-Length': content.length});
    res.end(content);
  } catch { res.writeHead(404); res.end('Not found'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}/collection/`;
const report = [];
const engines = {chromium, firefox, webkit};
const selected = (process.env.GUAVAJAM_BROWSER_ENGINES || 'chromium,firefox,webkit').split(',');
assert(selected.length && selected.every(name => Object.hasOwn(engines, name)), 'Unknown browser engine');
try {
  for (const name of selected) {
    const engine = engines[name];
    const executablePath = name === 'chromium' ? process.env.GUAVAJAM_CHROMIUM_EXECUTABLE : undefined;
    const browser = await engine.launch(executablePath ? {executablePath} : {});
    try {
      const context = await browser.newContext({viewport: {width: 360, height: 900}});
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      const errors = [], external = [];
      page.on('pageerror', error => errors.push(error.message));
      context.on('request', request => {
        if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin) external.push(request.url());
      });
      const waitTotal = async total => {
        try {
          await page.waitForFunction(total => !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') &&
            document.querySelector('#guavajam-search-status').textContent === `${total} work(s) found`, total);
        } catch (error) {
          throw new Error(`${name}: expected ${total} matches; status=${await page.locator('#guavajam-search-status').textContent()}; browser errors=${errors.join('; ')}`, {cause: error});
        }
      };
      const query = async (term, total) => {
        await page.locator('input[name=q]').fill(term);
        await page.locator('#guavajam-search button[type=submit]').click();
        await waitTotal(total);
      };
      await page.goto(base);
      await waitTotal(fixture.works);
      assert.equal(await page.evaluate(() => crossOriginIsolated), false);
      assert.equal(await page.locator('#guavajam-static-list').isVisible(), false);
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-results h3').first().textContent(), 'Work 22');
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 2);
      assert.equal(await page.locator('#guavajam-search-next').isDisabled(), true);
      await page.locator('select[name=sort]').selectOption('title');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results h3').textContent === 'Work 01');
      assert.equal(await page.locator('#guavajam-search-page').textContent(), 'Page 1 of 2');
      await query('Palaeoriver', 1);
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'Work 01');
      assert.equal(await page.getByRole('link', {name: 'View matching edition text'}).getAttribute('href'), fixture.historical);
      await page.getByRole('link', {name: 'View matching edition text'}).click();
      assert(page.url().endsWith(fixture.historical));
      assert((await page.locator('article').textContent()).includes('Palaeoriver'));
      await page.goto(base);
      await waitTotal(fixture.works);
      await query('Glaciolacustrine', 1);
      const support = await page.getByRole('link', {name: 'View matching edition text'}).getAttribute('href');
      assert(support.startsWith('sources/work-02/editions/'));
      assert(support.endsWith('/read/supporting/evidence.html'));
      assert.equal((await page.request.get(base + support)).status(), 200);
      await query('cafe collection', 1); // metadata and body share the same search record
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'Work 01');
      await query("zznonexistent' OR 1=1 --", 0); // never SQL or an FTS expression
      await query('!!!', fixture.works);
      await query('', fixture.works);
      const options = await page.locator('select[name=subject] option').evaluateAll(nodes => nodes.slice(1).map(n => ({key: n.value, label: n.textContent})));
      assert.equal(options.length, 2);
      assert.equal(new Set(options.map(o => o.key)).size, 2);
      assert(options.every(o => o.label.includes('Java')));
      for (const option of options) {
        await page.locator('select[name=subject]').selectOption(option.key);
        await waitTotal(1);
        assert(['Work 01', 'Work 02'].includes(await page.locator('#guavajam-search-results h3').textContent()));
      }
      await page.locator('select[name=subject]').selectOption('');
      await page.locator('select[name=keyword]').selectOption('Café studies');
      await waitTotal(1);
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'Work 01');
      assert.equal(await page.locator('select[name=keyword] option').filter({hasText: 'Retired topic'}).count(), 0);
      await page.locator('select[name=keyword]').selectOption('');
      await waitTotal(fixture.works);
      // A permitted authority URI can be a long unbroken select option label.
      await page.locator('select[name=subject]').evaluate(select => {
        const option = document.createElement('option');
        option.id = 'layout-stress-subject';
        option.textContent = 'Subject — https://example.org/' + 'a'.repeat(200);
        option.value = 'layout-stress';
        select.append(option);
        select.value = option.value;
      });
      for (const width of [320, 390, 1280]) {
        await page.setViewportSize({width, height: 900});
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        assert.equal(overflow, false, `${name}: ${width}px overflow`);
      }
      await page.locator('#layout-stress-subject').evaluate(option => option.remove());
      await page.evaluate(axe.source);
      const audit = await page.evaluate(() => axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}}));
      assert.deepEqual(audit.violations.map(v => ({id: v.id, targets: v.nodes.map(n => n.target)})), [], `${name}: search accessibility`);
      assert.deepEqual(errors, [], `${name}: browser errors`);
      assert.deepEqual(external, [], `${name}: unexpected external requests`);
      for (const width of [390, 1100]) {
        await page.setViewportSize({width, height: 900});
        await page.screenshot({path: path.join(output, `${name}-search-${width}.png`), fullPage: true});
      }
      await context.close();

      const disabled = await browser.newContext({javaScriptEnabled: false});
      const plain = await disabled.newPage();
      await plain.goto(base);
      assert.equal(await plain.locator('#guavajam-static-list > li').count(), fixture.works);
      assert.equal(await plain.locator('#guavajam-static-list').isVisible(), true);
      await plain.locator('#guavajam-static-list h3 a').first().click();
      assert(plain.url().startsWith(base + 'sources/'));
      assert.equal(await plain.locator('h1').count(), 1);
      await disabled.close();

      for (const broken of ['sqlite3.wasm', 'index.sqlite']) {
        const failure = await browser.newContext();
        await failure.route('**/' + broken, route => route.abort());
        const fallback = await failure.newPage();
        await fallback.goto(base);
        await fallback.waitForFunction(() => document.querySelector('#guavajam-search-status').textContent.includes('unavailable'));
        assert.equal(await fallback.locator('#guavajam-static-list').isVisible(), true);
        assert.equal(await fallback.locator('#guavajam-static-list > li').count(), fixture.works);
        await failure.close();
      }
      report.push({engine: name, version: browser.version(), executablePath: executablePath ?? 'Playwright pinned',
        works: fixture.works, pagination: true, historicalRouting: true,
        supportingRouting: true, metadataSearch: true, facets: true, staticFallback: true, accessibility: true});
      console.log(`${name}: aggregate SQLite WASM search, routing, facets, pagination and fallbacks passed`);
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
assert(requests.includes('sqlite3.wasm'));
assert(requests.includes('index.sqlite'));
assert(!requests.some(request => request.includes('opfs')));
await fs.writeFile(path.join(output, 'search-browser-report.json'), JSON.stringify({checks: report}, null, 2) + '\n');
