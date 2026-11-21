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
      '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.sqlite': 'application/vnd.sqlite3', '.gz': 'application/gzip'};
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
      const errors = [], external = [], loaded = [];
      page.on('pageerror', error => errors.push(error.message));
      context.on('request', request => {
        loaded.push(request.url());
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
      // The Works index is the layout reference for every lazy search form.
      // Compare actual computed controls at mobile and intermediate widths;
      // checking only root overflow would miss different breakpoints or gaps.
      const parityContext = await browser.newContext();
      const parityPage = await parityContext.newPage();
      const parityDownloads = [];
      parityContext.on('request', request => {
        if (/(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(request.url()))
          parityDownloads.push(request.url());
      });
      await parityPage.goto(base + 'sources/work-02/index.html');
      const latest = new URL(await parityPage.getByRole('link', {name: 'Latest', exact: true}).getAttribute('href'), parityPage.url()).href;
      const surfaces = [base + 'sources/work-02/browse/index.html',
        base + 'sources/work-02/editions/index.html',
        latest.replace('index.html', 'documents.html'), latest.replace('index.html', 'cite.html')];
      const controls = async () => parityPage.evaluate(() => {
        const properties = ['display', 'gridTemplateColumns', 'rowGap', 'columnGap',
          'fontSize', 'fontFamily', 'lineHeight', 'padding', 'margin', 'width', 'boxSizing'];
        return ['.guavajam-index-search', '#guavajam-search', '#guavajam-search input[name=q]',
          '#guavajam-search > button[type=submit]', '.guavajam-search-filters'].map(selector => {
          const style = getComputedStyle(document.querySelector(selector));
          return Object.fromEntries(properties.map(property => [property, style[property]]));
        });
      });
      for (const width of [320, 390, 500, 760]) {
        await parityPage.setViewportSize({width, height: 900});
        await parityPage.goto(base);
        const reference = await controls();
        for (const url of surfaces) {
          await parityPage.goto(url);
          assert.deepEqual(await controls(), reference, `${name}: ${width}px control parity at ${url}`);
          assert.equal(await parityPage.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
        }
      }
      assert.deepEqual(parityDownloads, [], 'layout parity must not activate SQLite');
      await parityContext.close();
      const query = async (term, total) => {
        await page.locator('input[name=q]').fill(term);
        await page.locator('#guavajam-search button[type=submit]').click();
        await waitTotal(total);
      };
      await page.goto(base);
      assert.equal(await page.locator('#guavajam-static-list > li').count(), 20);
      assert.equal(await page.locator('#guavajam-static-list').isVisible(), true);
      assert.equal(await page.locator('#guavajam-search-status').textContent(), 'Showing static page 1 of 2.');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Works');
      assert.equal(await page.locator('.guavajam-index-size').count(), 1);
      assert.equal(await page.locator('#guavajam-search-spinner').isVisible(), false);
      assert.equal(await page.locator('#guavajam-static-list .guavajam-facet').count() > 0, true);
      assert.equal(await page.locator('#guavajam-static-list button.guavajam-facet').count(), 0,
        'no inert metadata controls before SQLite is enabled');
      assert.equal(await page.locator('#guavajam-static-list').evaluate(node =>
        node.textContent.includes('Committed by')), false);
      assert.equal(loaded.some(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)), false,
        'the collection does not download its database or runtime before search is enabled');
      // Page two has real static links and can activate the same SQLite
      // worker, including relative URLs rooted one directory higher.
      await page.locator('.guavajam-static-pages a[rel=next]').click();
      assert(page.url().endsWith('/page/2.html'));
      assert.equal(await page.locator('#guavajam-static-list > li').count(), fixture.works - 20);
      assert.equal(await page.locator('form#guavajam-search').getAttribute('data-root'), '../');
      assert.equal(loaded.some(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)), false);
      await page.locator('#guavajam-search-enable').click();
      await waitTotal(fixture.works);
      assert.equal(await page.locator('#guavajam-search-spinner').isVisible(), false);
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Search results');
      assert.equal(await page.locator('#guavajam-search-results .guavajam-facet').count() > 0, true);
      const pagedResult = await page.locator('#guavajam-search-results h3 a').first().getAttribute('href');
      assert(pagedResult.startsWith('../sources/'));
      await query('Glaciolacustrine', 1);
      const pagedHit = await page.getByRole('link', {name: 'View matching edition text'}).getAttribute('href');
      assert(pagedHit.startsWith('../sources/'));
      assert((await page.request.get(new URL(pagedHit, page.url()).href)).ok());
      await page.goto(base);
      await page.locator('#guavajam-search-enable').focus();
      await page.locator('#guavajam-search-enable').press('Enter');
      await waitTotal(fixture.works);
      assert.equal(await page.locator('input[name=q]').evaluate(node => node === document.activeElement), true);
      assert.equal(await page.evaluate(() => crossOriginIsolated), false);
      assert.equal(await page.locator('#guavajam-static-list').isVisible(), false);
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-results h3').first().textContent(), 'Work 22');
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 2);
      assert.equal(await page.locator('#guavajam-search-next').isDisabled(), true);
      await page.locator('.guavajam-search-filters summary').click();
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
      await page.locator('#guavajam-search-enable').click();
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
      await page.locator('.guavajam-search-filters summary').click();
      const options = await page.locator('select[name=subject] option').evaluateAll(nodes => nodes.slice(1).map(n => ({key: n.value, label: n.textContent})));
      assert(options.filter(item => item.label.includes('Java · ')).length >= 2,
        'same-name subjects keep their distinct authority metadata');
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
      // Facet controls are native buttons only once SQLite is enabled.
      const fieldTag = page.locator('#guavajam-search-results button.guavajam-tag').first();
      assert.equal(await fieldTag.getAttribute('data-value'), 'Field observations');
      await fieldTag.click();
      await waitTotal(fixture.works - 1);
      assert.equal(await page.locator('select[name=keyword]').inputValue(), 'Field observations');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      await page.locator('#guavajam-search-results button.guavajam-tag').first().click();
      await waitTotal(fixture.works);
      assert.equal(await page.locator('select[name=keyword]').inputValue(), '');
      // A distinct subject authority filters by its exact SHA-derived key.
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      const subjectButton = page.locator('#guavajam-search-results button.guavajam-subject').first();
      const subjectKey = await subjectButton.getAttribute('data-value');
      await subjectButton.click();
      await waitTotal(1);
      assert.equal(await page.locator('select[name=subject]').inputValue(), subjectKey);
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 1);
      await page.locator('select[name=subject]').selectOption('');
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
      // Stress single-line titles, bylines and facets; the document must not
      // acquire horizontal overflow when an individual row can scroll instead.
      await page.locator('#guavajam-search-results h3 a').first().evaluate(a => {
        a.textContent = 'Very long work title '.repeat(35);
      });
      await page.locator('#guavajam-search-results .guavajam-byline').first().evaluate(p => {
        p.textContent = 'An exceptionally long bibliographic byline '.repeat(25);
      });
      for (const width of [320, 390, 1280]) {
        await page.setViewportSize({width, height: 900});
        const layout = await page.evaluate(() => {
          const title = document.querySelector('#guavajam-search-results h3');
          const byline = document.querySelector('#guavajam-search-results .guavajam-byline');
          const facets = document.querySelector('#guavajam-search-results .guavajam-facets');
          return {overflow: document.documentElement.scrollWidth > innerWidth + 1,
            titleScrollable: title.scrollWidth > title.clientWidth,
            bylineScrollable: byline.scrollWidth > byline.clientWidth,
            titleWrap: getComputedStyle(title).whiteSpace,
            bylineWrap: getComputedStyle(byline).whiteSpace,
            facetWrap: getComputedStyle(facets).whiteSpace};
        });
        assert.equal(layout.overflow, false, `${name}: ${width}px whole-page overflow`);
        assert.equal(layout.titleScrollable, true);
        assert.equal(layout.bylineScrollable, true);
        assert.deepEqual([layout.titleWrap, layout.bylineWrap, layout.facetWrap], ['nowrap', 'nowrap', 'nowrap']);
      }
      // Printed collection rows expose all metadata rather than clipping a
      // screen-only scroller or letting a nonshrinking flex item escape paper.
      await page.locator('#guavajam-search-results .guavajam-facet').first().evaluate(node => {
        node.append(document.createTextNode(' A long subject label '.repeat(25)));
      });
      await page.emulateMedia({media: 'print'});
      const printed = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        rows: [...document.querySelectorAll('#guavajam-search-results h3, #guavajam-search-results .guavajam-scrollline')]
          .map(node => ({wrap: getComputedStyle(node).whiteSpace, overflow: getComputedStyle(node).overflowX}))
      }));
      assert.equal(printed.overflow, false, `${name}: printed collection metadata leaves the page`);
      assert(printed.rows.every(row => row.wrap === 'normal' && row.overflow === 'visible'));
      await page.emulateMedia({media: 'screen'});
      await page.locator('#guavajam-search-results .guavajam-facet').first()
        .evaluate(node => node.lastChild.remove());
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

      const delayed = await browser.newContext();
      let releaseDatabase, signalDownload;
      const gate = new Promise(resolve => { releaseDatabase = resolve; });
      const signalled = new Promise(resolve => { signalDownload = resolve; });
      await delayed.route(/\/index\.[a-f0-9]{64}\.sqlite\.gz$/, async route => {
        signalDownload();
        await gate;
        await route.continue();
      });
      const waiting = await delayed.newPage();
      try {
        await waiting.goto(base);
        await waiting.locator('#guavajam-search-enable').click();
        await Promise.race([signalled, new Promise((_, reject) =>
          setTimeout(() => reject(new Error('Expected a deferred SQLite download')), 15000))]);
        assert.equal(await waiting.locator('#guavajam-search-spinner').isVisible(), true);
        assert.equal(await waiting.locator('#guavajam-search-status').textContent(), 'Loading local search…');
      } finally {
        releaseDatabase();
      }
      await waiting.waitForFunction(total => document.querySelector('#guavajam-search-status').textContent ===
        `${total} work(s) found`, fixture.works);
      assert.equal(await waiting.locator('#guavajam-search-spinner').isVisible(), false);
      await delayed.close();

      const disabled = await browser.newContext({javaScriptEnabled: false});
      const plain = await disabled.newPage();
      await plain.goto(base);
      assert.equal(await plain.locator('#guavajam-static-list > li').count(), 20);
      assert.equal(await plain.locator('#guavajam-static-list button.guavajam-facet').count(), 0);
      assert.equal(await plain.locator('#guavajam-static-list .guavajam-scrollline').count() > 0, true);
      await plain.locator('.guavajam-static-pages a[rel=next]').click();
      assert(plain.url().endsWith('/page/2.html'));
      assert.equal(await plain.locator('#guavajam-static-list > li').count(), fixture.works - 20);
      await plain.locator('.guavajam-static-pages a[rel=prev]').click();
      assert(plain.url().endsWith('/index.html'));
      assert.equal(await plain.locator('#guavajam-static-list > li').count(), 20);
      assert.equal(await plain.locator('#guavajam-static-list').isVisible(), true);
      await plain.locator('#guavajam-static-list h3 a').first().click();
      assert(plain.url().startsWith(base + 'sources/'));
      assert.equal(await plain.locator('h1').count(), 0);
      assert.equal(await plain.locator('.guavajam-citation').count(), 1);
      assert.equal(await plain.locator('.guavajam-citation cite').count(), 1);
      await disabled.close();

      for (const broken of ['sqlite3.wasm', 'database', 'decompression']) {
        const failure = await browser.newContext();
        if (broken === 'decompression') {
          await failure.route('**/search-download.js', async route => {
            const response = await route.fetch();
            await route.fulfill({response, body: 'globalThis.DecompressionStream = undefined;\n' + await response.text()});
          });
        } else {
          await failure.route(broken === 'database' ? /\/index\.[a-f0-9]{64}\.sqlite\.gz$/ : '**/' + broken,
            route => route.abort());
        }
        const fallback = await failure.newPage();
        await fallback.goto(base);
        await fallback.locator('#guavajam-search-enable').click();
        await fallback.waitForFunction(() => document.querySelector('#guavajam-search-status').textContent.includes('unavailable'));
        assert.equal(await fallback.locator('#guavajam-static-list').isVisible(), true);
        assert.equal(await fallback.locator('#guavajam-static-list > li').count(), 20);
        await failure.close();
      }
      // An open page may outlive an atomic deployment which removes its old
      // hashed artifact. Retrying that URL cannot help; reload gets fresh HTML.
      const recovery = await browser.newContext();
      const stale = await recovery.newPage();
      await stale.goto(base);
      const currentDatabase = await stale.locator('#guavajam-search').getAttribute('data-database');
      await stale.locator('#guavajam-search').evaluate(form => {
        form.dataset.database = './index.' + '0'.repeat(64) + '.sqlite.gz';
      });
      await stale.locator('#guavajam-search-enable').click();
      await stale.waitForFunction(() => document.querySelector('#guavajam-search-status').textContent.includes('unavailable'));
      assert.equal(await stale.locator('#guavajam-static-list').isVisible(), true);
      await Promise.all([stale.waitForNavigation(), stale.locator('#guavajam-search-reload').click()]);
      assert.equal(await stale.locator('#guavajam-search').getAttribute('data-database'), currentDatabase);
      await stale.locator('#guavajam-search-enable').click();
      await stale.waitForFunction(total => document.querySelector('#guavajam-search-status').textContent === `${total} work(s) found`, fixture.works);
      assert.equal(await stale.locator('#guavajam-search-reload').isVisible(), false);
      await recovery.close();
      report.push({engine: name, version: browser.version(), executablePath: executablePath ?? 'Playwright pinned',
        works: fixture.works, pagination: true, historicalRouting: true,
        supportingRouting: true, metadataSearch: true, facets: true, deferredRuntime: true,
        staticFallback: true, stalePageRecovery: true, accessibility: true});
      console.log(`${name}: aggregate SQLite WASM search, routing, facets, pagination and fallbacks passed`);
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
assert(requests.includes('sqlite3.wasm'));
assert(requests.some(request => /^index\.[a-f0-9]{64}\.sqlite\.gz$/.test(request)));
assert(!requests.includes('index.sqlite'), 'the canonical raw artifact is not downloaded by enhanced search');
assert(!requests.some(request => request.includes('opfs')));
await fs.writeFile(path.join(output, 'search-browser-report.json'), JSON.stringify({checks: report}, null, 2) + '\n');
