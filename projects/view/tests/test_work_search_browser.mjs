// Work discovery uses the actual SQLite WASM runtime and independently signed editions.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
import axe from 'axe-core';

const [fixturePath, output] = process.argv.slice(2);
assert(fixturePath && output, 'Use projects/view/tests/run_work_search_browser.py');
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
      // Real HTTP redirects work in every browser engine. The destination is
      // the valid database, so following this redirect would fail the test.
      redirects.push(url.pathname);
      res.writeHead(302, {Location: url.pathname, 'Cache-Control': 'no-store'});
      res.end();
      return;
    }
    if (url.searchParams.get('fault') === 'truncated') content = content.subarray(0, content.length - 8);
    if (url.searchParams.get('fault') === 'corrupt') {
      content = Buffer.from(content);
      content[content.length - 8] ^= 255; // A wrong gzip CRC must fail native decompression.
    }
    const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
      '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.sqlite': 'application/vnd.sqlite3', '.gz': 'application/gzip'};
    res.writeHead(200, {'Content-Type': types[path.extname(filename)] ?? 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff', 'Content-Length': content.length});
    res.end(content);
  } catch { res.writeHead(404); res.end('Not found'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}/collection/`;
const browse = base + fixture.work + 'browse/index.html';
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
      page.setDefaultTimeout(15000);
      const errors = [], external = [], loaded = [];
      page.on('pageerror', error => errors.push(error.message));
      context.on('request', request => {
        loaded.push(request.url());
        if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin) external.push(request.url());
      });
      const label = {editions: 'edition(s)', documents: 'document(s)', passages: 'section(s) / passage(s)'};
      const waitTotal = async (total, kind) => {
        try {
          await page.waitForFunction(expected => !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') &&
            document.querySelector('#guavajam-search-status').textContent === expected, `${total} ${label[kind]} found`);
        } catch (error) {
          throw new Error(`${name}: expected ${total} ${kind}; status=${await page.locator('#guavajam-search-status').textContent()}; errors=${errors.join('; ')}`, {cause: error});
        }
      };
      const query = async (term, total, kind) => {
        await page.locator('input[name=q]').fill(term);
        await page.locator('#guavajam-search button[type=submit]').click();
        await waitTotal(total, kind);
      };
      const enable = async (total = fixture.documents, kind = 'documents') => {
        await page.locator('#guavajam-search-enable').click();
        await waitTotal(total, kind);
      };
      await page.goto(base + fixture.work);
      assert.equal(await page.locator('script').count(), 0, 'work homepage remains script-free');
      await page.goto(browse);
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), true);
      assert.equal(loaded.some(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)), false,
        'a small work must not download the runtime before search is enabled');
      await page.locator('#guavajam-search-enable').focus();
      await page.locator('#guavajam-search-enable').press('Enter');
      await waitTotal(fixture.documents, 'documents');
      assert.equal(await page.locator('input[name=q]').evaluate(node => node === document.activeElement), true,
        'focus remains useful after the enable button is hidden');
      assert.equal(await page.evaluate(() => crossOriginIsolated), false);
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), false);
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-results h3').first().textContent(), 'Archive work');
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 5);
      assert.equal(await page.locator('#guavajam-search-next').isDisabled(), true);

      await page.locator('.guavajam-search-filters summary').click();
      await page.locator('select[name=kind]').selectOption('editions');
      await waitTotal(fixture.editions, 'editions');
      assert.equal(await page.locator('select[name=edition]').inputValue(), 'all');
      assert.equal(await page.locator('#guavajam-search-page').textContent(), 'Page 1 of 2');
      assert.equal(await page.locator('#guavajam-search-results h3').first().textContent(), 'Edition 23');
      assert((await page.locator('#guavajam-search-results h3 a').first().getAttribute('href')).includes(fixture.latest));
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 3);
      await page.locator('select[name=sort]').selectOption('oldest');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results h3').textContent === 'Edition 1');
      assert.equal(await page.locator('#guavajam-search-page').textContent(), 'Page 1 of 2');
      await query('Palaeotidal', 1, 'editions');
      const historical = await page.getByRole('link', {name: 'View matching edition text'}).getAttribute('href');
      assert.equal(historical, base + fixture.historical);
      await page.getByRole('link', {name: 'View matching edition text'}).click();
      assert.equal(page.url(), historical);
      assert.equal(await page.locator('script').count(), 0);
      assert((await page.locator('article').textContent()).includes('Palaeotidal'));

      await page.goto(browse + '?kind=editions&q=Palaeotidal');
      await page.locator('#guavajam-search button[type=submit]').click();
      await waitTotal(1, 'editions');
      await page.locator('.guavajam-search-filters summary').click();
      await page.locator('select[name=edition]').selectOption('latest');
      await waitTotal(0, 'editions');
      await query('', 1, 'editions');
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'Edition 23');
      await page.locator('select[name=kind]').selectOption('documents');
      await waitTotal(fixture.documents, 'documents');
      await query('cafe Bathymetric ecology', 1, 'documents');
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'Archive work');
      await query("zznonexistent' OR 1=1 --", 0, 'documents');
      await query('!!!', fixture.documents, 'documents');
      await query('', fixture.documents, 'documents');
      await page.locator('select[name=document]').selectOption('content/supporting/archived.md');
      await waitTotal(0, 'documents');
      await page.locator('select[name=edition]').selectOption('all');
      await waitTotal(1, 'documents');
      assert((await page.locator('#guavajam-search-results h3 a').getAttribute('href')).endsWith(
        `/editions/${fixture.older}/read/supporting/archived.html`));

      await page.locator('select[name=kind]').selectOption('passages');
      await page.locator('select[name=edition]').selectOption('latest');
      await page.locator('select[name=document]').selectOption('content/manuscript.md');
      await waitTotal(fixture.manuscriptPassages, 'passages');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 5);
      await query('Bathymetric ecology', 1, 'passages');
      assert.equal(await page.locator('#guavajam-search-results h3 a').getAttribute('href'), base + fixture.section);
      await page.locator('#guavajam-search-results h3 a').click();
      assert.equal(page.url(), base + fixture.section);
      assert.equal(await page.locator('#section-24').count(), 1);
      assert.equal(await page.locator('script').count(), 0);

      await page.goto(browse + '?kind=passages&edition=' + fixture.latest + '&document=content/supporting/correspondence.md&q=Quotientwave');
      await page.locator('#guavajam-search-enable').click();
      await page.locator('.guavajam-search-filters summary').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results > li') &&
        !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy'));
      assert((await page.locator('#guavajam-search-results').textContent()).includes('Quoted passage'));
      const quoted = page.locator('#guavajam-search-results h3 a').filter({hasText: 'Quoted analysis'}).first();
      const quotedURL = await quoted.getAttribute('href');
      assert(quotedURL.includes(`/editions/${fixture.latest}/read/supporting/correspondence.html#`));
      await quoted.click();
      const quotedAnchor = new URL(page.url()).hash.slice(1);
      assert.equal(await page.locator('[id="' + quotedAnchor + '"]').count(), 1);
      assert.equal(await page.locator('script').count(), 0);

      // Directory pages are genuinely pre-paged HTML. Their interactive controls
      // use the SAME work SQLite worker rather than filtering the static DOM.
      const historyURL = base + fixture.work + 'editions/index.html';
      const documentsURL = base + fixture.work + `editions/${fixture.latest}/read/documents.html`;
      const downloadsBeforeDirectory = loaded.filter(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)).length;
      await page.goto(historyURL);
      assert.equal(await page.locator('#guavajam-static-section .guavajam-editions > li').count(), 20);
      assert.equal(await page.locator('#guavajam-directory-enhancement').isVisible(), true);
      assert.equal(await page.locator('#guavajam-search-results').isVisible(), false);
      assert.equal(loaded.filter(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)).length,
        downloadsBeforeDirectory, 'opening the static directory must not initialize SQLite');
      await page.locator('.guavajam-static-pages a[rel=next]').click();
      assert(page.url().endsWith('/editions/page/2.html'));
      assert.equal(await page.locator('#guavajam-static-section .guavajam-editions > li').count(), 2);
      await page.locator('select[name=sort]').selectOption('oldest');
      await waitTotal(fixture.editions - 1, 'editions');
      assert.equal(await page.locator('#guavajam-search-results h3').first().textContent(), 'Edition 1');
      assert((await page.locator('#guavajam-search-results').textContent()).includes('Observation 01'));
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      await query('Observation 01', 1, 'editions');
      // Publisher creates the initial signed edition before Observation 01.
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'Edition 2');
      assert((await page.locator('#guavajam-search-results .guavajam-message').textContent()).includes('Observation 01'));
      await query('', fixture.editions - 1, 'editions');
      await page.locator('select[name=sort]').selectOption('publication');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results h3').textContent === 'Edition 22');
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 2);

      await page.goto(documentsURL + '?kind=passages&edition=all&document=content/manuscript.md');
      assert.equal(await page.locator('input[name=kind]').inputValue(), 'documents');
      assert.equal(await page.locator('input[name=edition]').inputValue(), fixture.latest);
      await query('Appendix 23', 1, 'documents');
      await page.goto(documentsURL);
      assert.equal(await page.locator('#guavajam-static-section .guavajam-document-list > li').count(), 20);
      await page.locator('.guavajam-static-pages a[rel=next]').click();
      assert(page.url().endsWith('/read/documents/page/2.html'));
      assert.equal(await page.locator('#guavajam-static-section .guavajam-document-list > li').count(), 5);
      await page.locator('select[name=sort]').selectOption('title');
      await waitTotal(fixture.documents, 'documents');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 5);
      await query('Appendix 23', 1, 'documents');
      assert((await page.locator('#guavajam-search-results h3').textContent()).includes('Appendix 23'));

      await page.goto(browse);
      await enable();
      // Long source document titles/paths must not make filters wider than a phone.
      await page.locator('select[name=document]').evaluate(select => {
        const option = document.createElement('option');
        option.id = 'layout-stress-document';
        option.textContent = 'Long document ' + 'x'.repeat(220);
        option.value = 'layout-stress';
        select.append(option);
        select.value = option.value;
      });
      for (const width of [320, 390, 1280]) {
        await page.setViewportSize({width, height: 900});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false,
          `${name}: ${width}px work search overflow`);
      }
      await page.locator('#layout-stress-document').evaluate(option => option.remove());
      await page.evaluate(axe.source);
      const audit = await page.evaluate(() => axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}
      }));
      assert.deepEqual(audit.violations.map(v => ({id: v.id, targets: v.nodes.map(n => n.target)})), [],
        `${name}: work search accessibility`);
      assert.deepEqual(errors, [], `${name}: browser errors`);
      assert.deepEqual(external, [], `${name}: external requests`);
      for (const width of [390, 1100]) {
        await page.setViewportSize({width, height: 900});
        await page.screenshot({path: path.join(output, `${name}-work-search-${width}.png`), fullPage: true});
      }
      await page.goto(base + fixture.unpublished + 'browse/index.html');
      assert((await page.locator('#guavajam-static-section').textContent()).includes('No signed editions'));
      await enable(0, 'documents');
      assert.equal(await page.locator('#guavajam-search-page').textContent(), 'Page 1 of 1');
      assert.equal(await page.locator('#guavajam-search-next').isDisabled(), true);
      for (const kind of ['editions', 'passages']) {
        await page.locator('select[name=kind]').selectOption(kind);
        await waitTotal(0, kind);
      }
      await context.close();

      const disabled = await browser.newContext({javaScriptEnabled: false});
      const plain = await disabled.newPage();
      await plain.goto(browse);
      assert.equal(await plain.locator('#guavajam-static-section').isVisible(), true);
      assert((await plain.locator('#guavajam-static-section').textContent()).includes('Previous editions'));
      await plain.getByRole('link', {name: 'Previous editions', exact: true}).click();
      assert(plain.url().endsWith('/editions/index.html'));
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-editions > li').count(), 20);
      assert.equal(await plain.locator('#guavajam-directory-enhancement').isVisible(), false);
      await plain.locator('.guavajam-static-pages a[rel=next]').click();
      assert(plain.url().endsWith('/editions/page/2.html'));
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-editions > li').count(), 2);
      assert((await plain.locator('.guavajam-editions').textContent()).includes('Edition 1'));
      const directory = base + fixture.work + `editions/${fixture.latest}/read/documents.html`;
      await plain.goto(directory);
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-document-list > li').count(), 20);
      await plain.locator('.guavajam-static-pages a[rel=next]').click();
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-document-list > li').count(), 5);
      const unlinked = plain.getByRole('link', {name: 'Appendix 23', exact: true});
      assert.equal(await unlinked.count(), 1);
      await unlinked.click();
      assert(plain.url().endsWith(`/editions/${fixture.latest}/read/supporting/unlinked-23.html`));
      assert((await plain.locator('article').textContent()).includes('Unlinked supporting evidence 23'));
      assert.equal(await plain.locator('script').count(), 0);
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
        await fallback.goto(browse);
        const originalDatabase = await fallback.locator('#guavajam-search').getAttribute('data-database');
        if (['redirect', 'truncated', 'corrupt'].includes(broken)) {
          await fallback.locator('#guavajam-search').evaluate((form, kind) => {
            form.dataset.database += kind === 'redirect' ? '?redirect=1' : '?fault=' + kind;
          }, broken);
        } else if (broken === 'wrong-size') {
          await fallback.locator('#guavajam-search').evaluate(form => { form.dataset.databaseSize = String(Number(form.dataset.databaseSize) + 1); });
        }
        await fallback.locator('#guavajam-search-enable').click();
        await fallback.waitForFunction(() => document.querySelector('#guavajam-search-status').textContent.includes('unavailable'));
        assert.equal(await fallback.locator('#guavajam-static-section').isVisible(), true);
        assert.equal(await fallback.locator('#guavajam-search-results').isVisible(), false);
        assert.deepEqual(unexpected, [], `${name}: failure must not make external requests`);
        if (broken === 'redirect') {
          assert.equal(redirects.length, priorRedirects + 1, 'the server sent the rejected database redirect');
        }
        if (broken === 'database' || broken === 'redirect') {
          if (broken === 'database') await failure.unroute(databaseRoute);
          else await fallback.locator('#guavajam-search').evaluate((form, database) => { form.dataset.database = database; }, originalDatabase);
          await fallback.locator('#guavajam-search-enable').click();
          await fallback.waitForFunction(total => document.querySelector('#guavajam-search-status').textContent ===
            `${total} document(s) found`, fixture.documents);
          const values = await fallback.locator('select[name=edition] option').evaluateAll(nodes => nodes.map(node => node.value));
          assert.equal(new Set(values).size, values.length, 'a successful retry does not duplicate selector options');
        }
        await failure.close();
      }
      reports.push({engine: name, version: browser.version(), editions: fixture.editions,
        latestDocuments: fixture.documents, pagination: true, publicationOrder: true,
        exactHistoricalRouting: true, quotedSections: true, deferredRuntime: true,
        staticDirectory: true, fallback: true, accessibility: true});
      console.log(`${name}: work SQLite WASM search, edition/document/passage paging, exact routes and fallbacks passed`);
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
assert(requests.includes('search-worker.js'), 'nested works share the collection runtime');
assert(requests.includes('sqlite3.wasm'));
assert(requests.some(request => request.startsWith(fixture.work) && /\/work\.[a-f0-9]{64}\.sqlite\.gz$/.test(request)));
assert(!requests.includes(fixture.work + 'work.sqlite'));
assert(!requests.some(request => request.includes('opfs')));
await fs.writeFile(path.join(output, 'work-search-browser-report.json'), JSON.stringify({checks: reports}, null, 2) + '\n');
