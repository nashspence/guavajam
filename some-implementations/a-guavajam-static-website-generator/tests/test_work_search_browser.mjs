// The one edition-wide Passages directory uses the existing SQLite WASM runtime.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
import axe from 'axe-core';
import {instrumentSearch, openSearchFilters} from './interaction_contract.mjs';

const [fixturePath, output] = process.argv.slice(2);
assert(fixturePath && output, 'Use some-implementations/a-guavajam-static-website-generator/tests/run_work_search_browser.py');
const fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
await fs.mkdir(output, {recursive: true});
const root = path.resolve(fixture.root);
const requests = [];
const redirects = [];
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    assert(url.pathname.startsWith('/collection/'));
    const relative = decodeURIComponent(url.pathname.slice('/collection/'.length));
    const filename = path.resolve(root, relative.endsWith('/') || !relative ? relative + 'index.html' : relative);
    assert(filename.startsWith(root + path.sep));
    let content = await fs.readFile(filename);
    requests.push(relative);
    if (/^work\.[a-f0-9]{64}\.sqlite\.gz$/.test(path.basename(filename)) && url.searchParams.get('redirect') === '1') {
      redirects.push(url.pathname);
      res.writeHead(302, {Location: url.pathname, 'Cache-Control': 'no-store'});
      res.end();
      return;
    }
    if (url.searchParams.get('fault') === 'truncated') content = content.subarray(0, content.length - 8);
    if (url.searchParams.get('fault') === 'corrupt') {
      content = Buffer.from(content); content[content.length - 8] ^= 255;
    }
    const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
      '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.sqlite': 'application/vnd.sqlite3',
      '.gz': 'application/gzip'};
    res.writeHead(200, {'Content-Type': types[path.extname(filename)] ?? 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff', 'Content-Length': content.length});
    res.end(content);
  } catch { res.writeHead(404); res.end('Not found'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}/collection/`;
const documentsURL = base + fixture.work + `editions/${fixture.latest}/read/documents.html`;
const passagesURL = base + fixture.work + `editions/${fixture.latest}/read/passages.html`;
const engines = {chromium, firefox, webkit};
const selected = (process.env.GUAVAJAM_BROWSER_ENGINES || 'chromium,firefox,webkit').split(',');
assert(selected.length && selected.every(name => Object.hasOwn(engines, name)), 'Unknown browser engine');
const reports = [];
try {
  for (const name of selected) {
    const executablePath = name === 'chromium' ? process.env.GUAVAJAM_CHROMIUM_EXECUTABLE : undefined;
    const browser = await engines[name].launch(executablePath ? {executablePath} : {});
    try {
      const context = await browser.newContext({viewport: {width: 390, height: 900}});
      const page = await context.newPage();
      await instrumentSearch(page);
      page.setDefaultTimeout(15000);
      const errors = [], external = [], loaded = [];
      page.on('pageerror', error => errors.push(error.message));
      context.on('request', request => {
        loaded.push(request.url());
        if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin)
          external.push(request.url());
      });
      const label = {editions: 'edition(s)', documents: 'document(s)', passages: 'section(s) / passage(s)'};
      const waitTotal = async (total, kind) => {
        try {
          await page.waitForFunction(expected => !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') &&
            document.querySelector('#guavajam-search-status').textContent === expected,
            `${total} ${label[kind]} found`);
        } catch (error) {
          throw new Error(`${name}: expected ${total} ${kind}; status=${await page.locator('#guavajam-search-status').textContent()}; errors=${errors.join('; ')}`, {cause: error});
        }
      };
      const enable = async (total, kind) => {
        await page.locator('#guavajam-index-search > summary').click();
        await waitTotal(total, kind);
      };
      const query = async (term, total, kind) => {
        if (!await page.locator('#guavajam-index-search').evaluate(node => node.open))
          await page.locator('#guavajam-index-search > summary').click();
        await page.locator('input[name=q]').fill(term);
        await page.locator('#guavajam-search button[type=submit]').click();
        await waitTotal(total, kind);
      };
      const runtimeLoads = () => loaded.filter(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)).length;
      await page.goto(base + fixture.work);
      assert.equal(await page.locator('#guavajam-index-search').count(), 1);
      assert.equal((await page.locator('a[href="browse/index.html"]').count()), 0);
      assert.equal(await page.locator('.guavajam-navigation h1').textContent(), 'Archive work');
      assert.equal(await page.locator('.guavajam-editions > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-status').textContent(), 'Showing static page 1 of 2.');
      assert.equal(runtimeLoads(), 0, 'Opening a directory never initializes SQLite');
      await page.locator('.guavajam-static-pages a[rel=next]').first().click();
      assert.equal(await page.locator('.guavajam-editions > li').count(), 3);
      await enable(fixture.editions, 'editions');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Search results');
      await openSearchFilters(page);
      await page.locator('select[name=sort]').selectOption('oldest');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results h3')?.textContent === 'v1');
      await query('v23', 1, 'editions');
      assert.equal(await page.locator('#guavajam-search-results h3 mark').textContent(), 'v23');
      await query('Shorelinechronicle', 1, 'editions');
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'v2');
      assert.equal(await page.locator('.guavajam-message-detail[open] mark').textContent(), 'Shorelinechronicle');
      await query('Palaeotidal', 1, 'editions');
      assert.equal(await page.getByRole('link', {name: 'View matching edition text'}).getAttribute('href'),
        base + fixture.historical);

      await page.goto(documentsURL);
      assert.equal(await page.locator('#guavajam-static-section .guavajam-document-list > li').count(), 20);
      await page.locator('.guavajam-static-pages a[rel=next]').first().click();
      assert.equal(await page.locator('#guavajam-static-section .guavajam-document-list > li').count(), 5);
      await enable(fixture.documents, 'documents');
      await page.locator('.guavajam-search-filters summary').click();
      await page.locator('select[name=sort]').selectOption('title');
      await waitTotal(fixture.documents, 'documents');
      await query('Appendix 23', 1, 'documents');
      assert.equal((await page.locator('#guavajam-search-results h3 mark').allTextContents()).join(' '), 'Appendix 23');
      await query('Abyssalappendixselector', 1, 'documents');
      assert((await page.locator('#guavajam-search-results h3').textContent()).includes('Appendix 23'));

      await page.goto(passagesURL);
      assert.equal(await page.locator('#guavajam-static-section .guavajam-citation-entry').count(), 20);
      assert.equal(await page.locator('select[name=supporting]').inputValue(), 'all');
      const canonicalFirst = await page.locator('#guavajam-static-section .guavajam-citation-entry').first().evaluate(row => ({
        text: row.querySelector('.guavajam-citation').textContent,
        download: row.querySelector('.guavajam-citation a[download]').href,
        context: row.querySelector('.guavajam-meta').textContent
      }));
      await enable(fixture.versionPassages, 'passages');
      assert.deepEqual(await page.locator('#guavajam-search-results > li').first().evaluate(row => ({
        text: row.querySelector('.guavajam-citation').textContent,
        download: row.querySelector('.guavajam-citation a[download]').href,
        context: row.querySelector('.guavajam-meta').textContent
      })), canonicalFirst, 'enhancement retains the full canonical citation and its download');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-page').textContent(),
        `Page 1 of ${Math.ceil(fixture.versionPassages / 20)}`);
      let interactiveTotal = 20;
      for (let number = 2; number <= Math.ceil(fixture.versionPassages / 20); number++) {
        await page.locator('#guavajam-search-next').click();
        await page.waitForFunction(n => !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') &&
          document.querySelector('#guavajam-search-page').textContent.startsWith(`Page ${n} of`), number);
        const count = await page.locator('#guavajam-search-results > li').count();
        assert(count <= 20); interactiveTotal += count;
      }
      assert.equal(interactiveTotal, fixture.versionPassages);
      assert.equal(await page.locator('#guavajam-search-next').isDisabled(), true);
      await page.locator('.guavajam-search-filters').evaluate(node => { node.open = true; });
      await page.locator('select[name=supporting]').selectOption('manuscript');
      await waitTotal(fixture.manuscriptPassages, 'passages');
      await page.locator('select[name=supporting]').selectOption('all');
      await page.waitForFunction(expected => {
        const found = document.querySelector('#guavajam-search-status').textContent.match(/^(\d+) section\(s\)/);
        return found && Number(found[1]) > expected;
      }, fixture.manuscriptPassages);
      await query('Bathymetric ecology', 1, 'passages');
      const result = page.locator('#guavajam-search-results > li');
      const passageHref = await result.locator('h3 a').getAttribute('href');
      assert(passageHref.includes('/read/passages/page/2.html#citation:'));
      const quote = result.locator('.guavajam-passage-excerpt');
      assert.equal(await quote.count(), 1);
      assert((await quote.textContent()).length <= 321, 'source quote is bounded');
      assert((await quote.textContent()).toLowerCase().includes('bathymetric'));
      assert(await quote.locator('mark').count() > 0, 'the FTS match is highlighted');
      assert.equal(await result.getByRole('link', {name: 'Read this passage'}).getAttribute('href'), base + fixture.section);
      await result.locator('h3 a').click();
      assert.equal(page.url(), passageHref);
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), true);
      const origin = await page.evaluate(() => performance.timeOrigin);
      await query('Bathymetric ecology', 1, 'passages');
      await page.locator('#guavajam-search-results h3 a').click();
      assert.equal(await page.evaluate(() => performance.timeOrigin), origin, 'same-page citation does not require a redirect');
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), true);
      assert.equal(await page.evaluate(() => document.activeElement.id), new URL(passageHref).hash.slice(1));
      await page.locator('[id="' + new URL(passageHref).hash.slice(1) + '"]').getByRole('link', {name: 'Read this passage'}).click();
      assert.equal(page.url(), base + fixture.section);
      assert.equal(await page.locator('script').count(), 0);

      await page.goto(passagesURL);
      await query('Bathyal', 2, 'passages');
      const hits = await page.locator('#guavajam-search-results h3 a').allTextContents();
      assert.equal(hits.length, 2);
      // Only the manuscript section and the actual supporting passage match.
      assert.deepEqual((await page.getByRole('link', {name: 'Read this passage'}).evaluateAll(
        links => links.map(link => link.href))).sort(), [base + fixture.lastSection,
        base + fixture.work + `editions/${fixture.latest}/read/supporting/unlinked-23.html#support-44`].sort());

      await page.locator('.guavajam-search-filters').evaluate(node => { node.open = true; });
      await page.locator('select[name=supporting]').selectOption('manuscript');
      await waitTotal(1, 'passages');
      assert(!(await page.locator('#guavajam-search-results').textContent()).includes('unlinked-23'));
      await page.locator('select[name=supporting]').selectOption('all');
      await waitTotal(2, 'passages');
      await page.locator('select[name=sort]').selectOption('relevance');
      await waitTotal(2, 'passages');
      await query('Quotientwave', 3, 'passages');
      assert((await page.locator('#guavajam-search-results').textContent()).includes('Quoted passage'));
      assert((await page.locator('#guavajam-search-results').textContent()).includes('content/supporting/correspondence.md'));

      // A match on signed metadata must show the native FTS highlight and
      // identify that context, rather than quoting an unrelated passage body.
      await query('Café studies', fixture.versionPassages, 'passages');
      assert.equal(await page.locator('#guavajam-search-results .guavajam-excerpt-label').first().textContent(), 'Metadata match');
      for (const row of await page.locator('#guavajam-search-results > li').all()) {
        assert(await row.locator('.guavajam-passage-excerpt mark').count() > 0);
        assert((await row.locator('.guavajam-passage-excerpt').textContent()).includes('Café'));
      }
      await query('Example', fixture.versionPassages, 'passages');
      for (const excerpt of await page.locator('.guavajam-passage-excerpt').all()) {
        assert.equal(await excerpt.textContent(), 'Example Author', 'prefer readable author text over raw provenance JSON');
        assert(await excerpt.locator('mark').count() > 0);
      }
      await query('Stereophotometric', 1, 'passages');
      const longQuote = page.locator('.guavajam-passage-excerpt');
      assert.equal(await page.locator('.guavajam-excerpt-label').textContent(), 'Passage match');
      assert((await longQuote.textContent()).startsWith('…'));
      assert([...await longQuote.textContent()].length <= 322);
      assert.equal(await longQuote.locator('mark').textContent(), 'Stereophotometric');
      await query('Café Stereophotometric', 1, 'passages');
      assert.equal(await page.locator('.guavajam-passage-excerpt mark').textContent(), 'Stereophotometric');

      for (const width of [320, 390, 1280]) {
        await page.setViewportSize({width, height: 900});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false,
          `${name}: Passages overflow at ${width}px`);
      }
      await page.evaluate(axe.source);
      const audit = await page.evaluate(() => axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}
      }));
      assert.deepEqual(audit.violations.map(v => ({id: v.id, targets: v.nodes.map(n => n.target)})), [],
        `${name}: Passages search accessibility`);
      assert.deepEqual(errors, [], `${name}: browser errors`);
      assert.deepEqual(external, [], `${name}: external requests`);
      await context.close();

      // Script-free navigation reaches every supporting passage by ordinary
      // pagination, without fetching a database or creating fake search links.
      const disabled = await browser.newContext({javaScriptEnabled: false});
      const plain = await disabled.newPage();
      await plain.goto(passagesURL);
      let total = 0, pageCount = 0;
      while (true) {
        const count = await plain.locator('#guavajam-static-section .guavajam-citation-entry').count();
        assert(count <= 20);
        total += count; pageCount++;
        if (!(await plain.locator('.guavajam-static-pages a[rel=next]').count())) break;
        await plain.locator('.guavajam-static-pages a[rel=next]').first().click();
        assert(pageCount < 50, 'bounded test corpus');
      }
      assert.equal(total, fixture.versionPassages, 'every manuscript and supporting passage remains discoverable without JS');
      await plain.goto(passagesURL);
      assert.equal(await plain.locator('#guavajam-index-search').isVisible(), false);
      await disabled.close();

      for (const broken of ['sqlite3.wasm', 'database', 'search-worker.js', 'redirect', 'truncated', 'corrupt', 'wrong-size', 'decompression']) {
        const failure = await browser.newContext();
        const unexpected = [];
        const priorRedirects = redirects.length;
        failure.on('request', request => {
          if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin) unexpected.push(request.url());
        });
        const databaseRoute = /\/work\.[a-f0-9]{64}\.sqlite\.gz$/;
        if (broken === 'decompression') {
          await failure.route('**/search-download.js', async route => {
            const response = await route.fetch();
            await route.fulfill({response, body: 'globalThis.DecompressionStream = undefined;\n' + await response.text()});
          });
        } else if (['sqlite3.wasm', 'database', 'search-worker.js'].includes(broken)) {
          await failure.route(broken === 'database' ? databaseRoute : '**/' + broken, route => route.abort());
        }
        const fallback = await failure.newPage();
        await fallback.goto(passagesURL);
        const originalDatabase = await fallback.locator('#guavajam-search').getAttribute('data-database');
        if (['redirect', 'truncated', 'corrupt'].includes(broken)) {
          await fallback.locator('#guavajam-search').evaluate((form, kind) => {
            form.dataset.database += kind === 'redirect' ? '?redirect=1' : '?fault=' + kind;
          }, broken);
        } else if (broken === 'wrong-size') {
          await fallback.locator('#guavajam-search').evaluate(form => { form.dataset.databaseSize = String(Number(form.dataset.databaseSize) + 1); });
        }
        await fallback.locator('#guavajam-index-search > summary').click();
        await fallback.waitForFunction(() => document.querySelector('#guavajam-search-status').textContent.includes('unavailable'));
        assert.equal(await fallback.locator('.guavajam-listing').isVisible(), true);
        assert.equal(await fallback.locator('#guavajam-search-status').isVisible(), true);
        assert.equal(await fallback.locator('#guavajam-static-section .guavajam-citation-entry').count(), 20);
        assert.equal(await fallback.locator('#guavajam-static-section').isVisible(), true);
        assert((await fallback.locator('#guavajam-index-search > summary').textContent()).includes('Indexed Search'));
        assert.equal(await fallback.locator('#guavajam-index-search').getAttribute('open'), '');
        assert.equal(await fallback.locator('#guavajam-search-results').isVisible(), false);
        assert.deepEqual(unexpected, [], `${name}: failure must not make external requests`);
        if (broken === 'redirect') {
          assert.equal(redirects.length, priorRedirects + 1, 'the server sent the rejected database redirect');
        }
        if (broken === 'database' || broken === 'redirect') {
          if (broken === 'database') await failure.unroute(databaseRoute);
          else await fallback.locator('#guavajam-search').evaluate((form, database) => { form.dataset.database = database; }, originalDatabase);
          await fallback.locator('#guavajam-index-search > summary').click(); // close
          await fallback.locator('#guavajam-index-search > summary').click(); // retry on reopen
          await fallback.waitForFunction(total => document.querySelector('#guavajam-search-status').textContent ===
            `${total} section(s) / passage(s) found`, fixture.versionPassages);
          const values = await fallback.locator('select[name=supporting] option').evaluateAll(nodes => nodes.map(node => node.value));
          assert.equal(new Set(values).size, values.length, 'a successful retry does not duplicate selector options');
        }
        await failure.close();
      }
      reports.push({engine: name, version: browser.version(), singlePassageIndex: true,
        completeStaticPaging: true, supportingFilter: true, boundedExcerpt: true,
        highlightedMatches: true, scriptDisabled: true, accessibility: true});
      console.log(name + ': SQLite-enhanced unified Passages, versions, documents and fallbacks passed');
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
assert(requests.includes('search-worker.js'));
assert(requests.includes('sqlite3.wasm'));
assert(requests.some(request => request.startsWith(fixture.work) && /\/work\.[a-f0-9]{64}\.sqlite\.gz$/.test(request)));
assert(!requests.includes(fixture.work + 'work.sqlite'));
await fs.writeFile(path.join(output, 'work-search-browser-report.json'), JSON.stringify({checks: reports}, null, 2) + '\n');
