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
      const workActions = await page.locator('nav[aria-label="Work links"] a').allTextContents();
      assert.deepEqual(workActions, ['Latest', 'History']);
      assert.equal(await page.locator('main .guavajam-record-link').count(), 0);
      await page.goto(browse);
      assert.equal(await page.locator('#guavajam-static-section a').count(), 0);
      assert.equal(await page.locator('#guavajam-search-listing').isVisible(), false);
      assert.equal(await page.locator('.guavajam-navigation [aria-current=page]').textContent(), 'Search');
      // Search has the same fixed-column mobile bounds as every other index.
      // Only an explicitly scrollable breadcrumb or title may overflow.
      for (const width of [320, 390, 760]) {
        await page.setViewportSize({width, height: 900});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false,
          `${name}: ${width}px search page overflow before SQLite activation`);
      }
      await page.setViewportSize({width: 390, height: 900});
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
      assert.equal(await page.locator('#guavajam-search-results h3').first().textContent(), 'v23');
      assert((await page.locator('#guavajam-search-results h3 a').first().getAttribute('href')).includes(fixture.latest));
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 3);
      await page.locator('select[name=sort]').selectOption('oldest');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results h3').textContent === 'v1');
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
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'v23');
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
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 3');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 3 of 3');
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
      // Both a reviewed quotation and its nested heading retain independently
      // citable source identities, rather than being flattened into one hit.
      for (const anchor of new Set([quotedAnchor, 'm-reply'])) {
        await page.goto(quotedURL);
        await page.getByRole('link', {name: 'Cite committed source passage ' + anchor, exact: true}).click();
        assert(page.url().endsWith('/supporting/correspondence.cite.html#citation:' + anchor));
        const cited = page.locator('[id="citation:' + anchor + '"]');
        assert.equal(await cited.count(), 1);
        for (const identity of [fixture.latest, 'content/supporting/correspondence.md', '#' + anchor]) {
          assert((await cited.locator('blockquote').textContent()).includes(identity));
        }
        await cited.getByRole('link', {name: 'Read this passage', exact: true}).click();
        assert.equal(new URL(page.url()).hash, '#' + anchor);
        assert.equal(await page.locator('script').count(), 0);
      }

      // Directory pages are genuinely pre-paged HTML. Their interactive controls
      // use the SAME work SQLite worker rather than filtering the static DOM.
      const rowTypography = async locator => locator.evaluate(node => {
        const style = getComputedStyle(node);
        return Object.fromEntries(['fontSize', 'fontWeight', 'lineHeight', 'marginTop',
          'marginBottom', 'whiteSpace', 'overflowX'].map(property => [property, style[property]]));
      });
      const historyURL = base + fixture.work + 'editions/index.html';
      const documentsURL = base + fixture.work + `editions/${fixture.latest}/read/documents.html`;
      const downloadsBeforeDirectory = loaded.filter(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)).length;
      await page.goto(historyURL);
      assert.equal(await page.locator('#guavajam-static-section .guavajam-editions > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-status').textContent(), 'Showing static page 1 of 2.');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Versions');
      const historyTypography = await rowTypography(page.locator('#guavajam-static-section .guavajam-editions > li > article > h3').first());
      assert.equal(await page.locator('#guavajam-search-spinner').isVisible(), false);
      assert.equal(await page.locator('#guavajam-directory-enhancement').isVisible(), true);
      assert.equal(await page.getByRole('button', {name: 'Enable search'}).count(), 1);
      assert.equal(await page.locator('#guavajam-directory-enhancement .guavajam-search-intro').isVisible(), true);
      assert.equal(await page.locator('#guavajam-search-results').isVisible(), false);
      assert.equal(loaded.filter(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)).length,
        downloadsBeforeDirectory, 'opening the static directory must not initialize SQLite');
      await page.locator('.guavajam-static-pages a[rel=next]').click();
      assert(page.url().endsWith('/editions/page/2.html'));
      assert.equal(await page.locator('#guavajam-static-section .guavajam-editions > li').count(), 3);
      // Sorting in the static view must not silently download SQLite.
      await page.locator('.guavajam-search-filters summary').click();
      await page.locator('select[name=sort]').selectOption('oldest');
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), true);
      assert.equal(loaded.filter(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)).length,
        downloadsBeforeDirectory, 'changing sort alone must not enable SQLite');
      await page.locator('#guavajam-search-enable').click();
      await waitTotal(fixture.editions, 'editions');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Search results');
      assert.equal(await page.locator('#guavajam-search-spinner').isVisible(), false);
      assert.equal(await page.locator('#guavajam-search-results h3').first().textContent(), 'v1');
      assert.deepEqual(await rowTypography(page.locator('#guavajam-search-results h3').first()), historyTypography);
      assert((await page.locator('#guavajam-search-results').textContent()).includes('Observation 01'));
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      // A unique message token cannot collide with publisher date/time claims.
      await query('Shorelinechronicle', 1, 'editions');
      // Publisher creates the initial signed edition before Observation 01.
      assert.equal(await page.locator('#guavajam-search-results h3').textContent(), 'v2');
      assert((await page.locator('#guavajam-search-results .guavajam-message').allTextContents()).join(' ').includes('Observation 01'));
      await page.locator('#guavajam-search-results').getByText('Full commit message', {exact:true}).click();
      assert.equal(await page.locator('#guavajam-search-results').getByText('Shorelinechronicle records the historical review.', {exact:false}).isVisible(), true);
      await query('', fixture.editions, 'editions');
      await page.locator('select[name=sort]').selectOption('publication');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results h3').textContent === 'v23');
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 3);

      await page.goto(documentsURL + '?kind=passages&edition=all&document=content/manuscript.md');
      assert.equal(await page.locator('input[name=kind]').inputValue(), 'documents');
      assert.equal(await page.locator('input[name=edition]').inputValue(), fixture.latest);
      await query('Appendix 23', 1, 'documents');
      await page.goto(documentsURL);
      assert.equal(await page.locator('#guavajam-static-section .guavajam-document-list > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-status').textContent(), 'Showing static page 1 of 2.');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Documents');
      const documentTypography = await rowTypography(page.locator('#guavajam-static-section .guavajam-document-list > li > article > h3').first());
      await page.locator('.guavajam-static-pages a[rel=next]').click();
      assert(page.url().endsWith('/read/documents/page/2.html'));
      assert.equal(await page.locator('#guavajam-static-section .guavajam-document-list > li').count(), 5);
      await page.locator('#guavajam-search-enable').click();
      await waitTotal(fixture.documents, 'documents');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Search results');
      await page.locator('.guavajam-search-filters summary').click();
      assert.deepEqual(await rowTypography(page.locator('#guavajam-search-results h3').first()), documentTypography);
      await page.locator('select[name=sort]').selectOption('title');
      await waitTotal(fixture.documents, 'documents');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 5);
      await page.locator('#guavajam-search-results h3 a').first().evaluate(node => {
        node.textContent = 'A long supporting document title '.repeat(35);
      });
      await page.emulateMedia({media: 'print'});
      const printedDocuments = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        headings: [...document.querySelectorAll('#guavajam-search-results h3')]
          .map(node => ({wrap: getComputedStyle(node).whiteSpace, overflow: getComputedStyle(node).overflowX}))
      }));
      assert.equal(printedDocuments.overflow, false, `${name}: printed document titles leave the page`);
      assert(printedDocuments.headings.every(row => row.wrap === 'normal' && row.overflow === 'visible'));
      await page.emulateMedia({media: 'screen'});
      await query('Appendix 23', 1, 'documents');
      assert((await page.locator('#guavajam-search-results h3').textContent()).includes('Appendix 23'));

      const citationURL = base + fixture.work + `editions/${fixture.latest}/read/cite.html`;
      const deepCitationURL = base + fixture.work + `editions/${fixture.latest}/read/cite/page/3.html`;
      const supportCitationURL = base + fixture.work +
        `editions/${fixture.latest}/read/supporting/unlinked-23.cite/page/3.html`;
      const runtimeLoads = () => loaded.filter(url => /(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(url)).length;
      const loadsBeforeCitation = runtimeLoads();
      await page.goto(deepCitationURL + '?kind=documents&edition=all&document=content/supporting/unlinked-23.md&q=Bathyal#citation:section-44');
      assert.equal(await page.locator('#guavajam-search').getAttribute('data-directory'), 'citations');
      assert.equal(await page.locator('input[name=kind]').inputValue(), 'passages');
      assert.equal(await page.locator('input[name=edition]').inputValue(), fixture.latest);
      assert.equal(await page.locator('input[name=document]').inputValue(), 'content/manuscript.md');
      assert.equal(await page.locator('#guavajam-static-section .guavajam-citation-entry').count(), 5);
      assert.equal(await page.locator('#guavajam-search-status').textContent(), 'Showing static page 3 of 3.');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Passages');
      assert.equal(await page.locator('main > blockquote.guavajam-citation').count(), 0);
      assert.equal(await page.locator('[id="citation:section-44"]').isVisible(), true);
      assert.equal(runtimeLoads(), loadsBeforeCitation, 'opening a deep citation link must not initialize SQLite');
      await page.screenshot({path: path.join(output, `${name}-citation-static-deep.png`), fullPage: true});
      await query('Bathyal', 1, 'passages');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Search results');
      assert.equal(await page.locator('#guavajam-search-spinner').isVisible(), false);
      const citationResult = page.locator('#guavajam-search-results > li');
      assert.equal(await citationResult.locator('h3 a').getAttribute('href'), deepCitationURL + '#citation:section-44');
      assert.equal(await citationResult.getByRole('link', {name: 'Read this passage', exact: true}).getAttribute('href'), base + fixture.lastSection);
      const loadsBeforeStaticResult = runtimeLoads();
      await citationResult.locator('h3 a').click();
      assert.equal(page.url(), deepCitationURL + '#citation:section-44');
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), true);
      assert.equal(runtimeLoads(), loadsBeforeStaticResult, 'a search result is an ordinary static citation link');
      await query('', fixture.manuscriptPassages, 'passages');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-page').textContent(), 'Page 1 of 3');
      for (const number of [2, 3]) {
        await page.locator('#guavajam-search-next').click();
        await page.waitForFunction(expected => document.querySelector('#guavajam-search-page').textContent === expected,
          `Page ${number} of 3`);
        assert.equal(await page.locator('#guavajam-search-results > li').count(), number === 2 ? 20 : 5);
      }
      await query('Bathymetric ecology', 1, 'passages');
      assert((await page.locator('#guavajam-search-results h3 a').getAttribute('href')).endsWith('/read/cite/page/2.html#citation:section-24'));
      await page.locator('#guavajam-search-results h3 a').click();
      assert(page.url().endsWith('/read/cite/page/2.html#citation:section-24'));
      const previousCitation = new URL(page.url());
      previousCitation.hash = 'citation:section-23';
      await page.goto(previousCitation.href);
      await query('Bathymetric ecology', 1, 'passages');
      const samePageOrigin = await page.evaluate(() => performance.timeOrigin);
      await page.locator('#guavajam-search-results h3 a').click();
      assert.equal(await page.evaluate(() => performance.timeOrigin), samePageOrigin,
        'a citation on this static page uses ordinary fragment navigation');
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), true);
      assert.equal(await page.locator('#guavajam-search-results').isVisible(), false);
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Passages');
      assert.equal(await page.locator('#guavajam-search-status').textContent(), 'Showing static page 2 of 3.');
      assert.equal(await page.locator('[id="citation:section-24"]').isVisible(), true);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'citation:section-24');
      await query('Bathymetric ecology', 1, 'passages');
      await page.goBack();
      assert.equal(new URL(page.url()).hash, '#citation:section-23');
      await page.locator('[id="citation:section-23"]').waitFor({state: 'visible'});
      assert.equal(await page.locator('#guavajam-search-results').isVisible(), false);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'citation:section-23');
      assert.equal(await page.evaluate(() => performance.timeOrigin), samePageOrigin);
      await query('Bathymetric ecology', 1, 'passages');
      await page.locator('#guavajam-search-results h3 a').click();
      await query('Bathymetric ecology', 1, 'passages');
      await page.locator('#guavajam-search-results h3 a').click();
      assert.equal(new URL(page.url()).hash, '#citation:section-24');
      assert.equal(await page.evaluate(() => performance.timeOrigin), samePageOrigin);
      assert.equal(await page.locator('#guavajam-static-section').isVisible(), true);
      assert.equal(await page.locator('#guavajam-search-results').isVisible(), false);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'citation:section-24',
        'clicking a result for the existing fragment restores and focuses it without hashchange');
      await query('Bathymetric ecology', 1, 'passages');
      await page.locator('#guavajam-search-results').getByRole('link', {name: 'Read this passage', exact: true}).click();
      assert.equal(page.url(), base + fixture.section);
      assert.equal(await page.locator('script').count(), 0);

      await page.goto(supportCitationURL + '?edition=all&document=content/manuscript.md');
      assert.equal(await page.locator('input[name=document]').inputValue(), 'content/supporting/unlinked-23.md');
      assert.equal(await page.locator('#guavajam-static-section .guavajam-citation-entry').count(), 6);
      // The containing document section and its explicit source passage both
      // match. Each retains its own source position and citation page.
      await query('Bathyal', 2, 'passages');
      const supportingResult = page.locator('#guavajam-search-results > li').filter({
        has: page.locator('h3 a[href$="#citation:support-44"]')
      });
      assert.equal(await supportingResult.locator('h3 a').getAttribute('href'), supportCitationURL + '#citation:support-44');
      assert((await supportingResult.getByRole('link', {name: 'Read this passage', exact: true}).getAttribute('href')).endsWith('/read/supporting/unlinked-23.html#support-44'));
      await page.goto(citationURL);
      await query('', fixture.manuscriptPassages, 'passages');
      for (const width of [320, 390, 1280]) {
        await page.setViewportSize({width, height: 900});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false,
          `${name}: ${width}px citation search overflow`);
      }
      await page.evaluate(axe.source);
      const citationAudit = await page.evaluate(() => axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}
      }));
      assert.deepEqual(citationAudit.violations.map(v => ({id: v.id, targets: v.nodes.map(n => n.target)})), [],
        `${name}: citation search accessibility`);

      await page.goto(browse);
      await enable();
      await page.locator('.guavajam-search-filters summary').click();
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
      assert.equal(await page.locator('#guavajam-static-section a').count(), 0);
      assert.equal(await page.locator('#guavajam-search-listing').isVisible(), false);
      await enable(0, 'documents');
      await page.locator('.guavajam-search-filters summary').click();
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
      assert((await plain.locator('#guavajam-static-section').textContent()).includes('Searching requires JavaScript.'));
      assert.equal(await plain.getByRole('link', {name: 'Versions', exact: true}).count(), 0);
      // Static directories remain reachable from the work, not duplicated on Search.
      await plain.goto(base + fixture.work);
      await plain.getByRole('link', {name: 'History', exact: true}).click();
      assert(plain.url().endsWith('/editions/index.html'));
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-editions > li').count(), 20);
      assert.equal(await plain.locator('#guavajam-directory-enhancement').isVisible(), false);
      await plain.locator('.guavajam-static-pages a[rel=next]').first().click();
      assert(plain.url().endsWith('/editions/page/2.html'));
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-editions > li').count(), 3);
      assert((await plain.locator('#guavajam-static-section .guavajam-editions').textContent()).includes('v1'));
      const directory = base + fixture.work + `editions/${fixture.latest}/read/documents.html`;
      await plain.goto(directory);
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-document-list > li').count(), 20);
      await plain.locator('.guavajam-static-pages a[rel=next]').first().click();
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-document-list > li').count(), 5);
      const unlinked = plain.getByRole('link', {name: 'Appendix 23', exact: true});
      assert.equal(await unlinked.count(), 1);
      await unlinked.click();
      assert(plain.url().endsWith(`/editions/${fixture.latest}/read/supporting/unlinked-23.html`));
      assert((await plain.locator('article').textContent()).includes('Unlinked supporting evidence 23'));
      assert.equal(await plain.locator('script').count(), 0);
      await plain.getByRole('link', {name: 'Cite committed source passage support-44', exact: true}).click();
      assert.equal(plain.url(), supportCitationURL + '#citation:support-44');
      assert.equal(await plain.locator('#guavajam-directory-enhancement').isVisible(), false);
      assert.equal(await plain.locator('#guavajam-static-section .guavajam-citation-entry').count(), 6);
      await plain.locator('[id="citation:support-44"]').getByRole('link', {name: 'Read this passage', exact: true}).click();
      assert(plain.url().endsWith('/read/supporting/unlinked-23.html#support-44'));
      await plain.goto(citationURL);
      const plainAnchors = [];
      for (const [index, expected] of [20, 20, 5].entries()) {
        const entries = plain.locator('#guavajam-static-section .guavajam-citation-entry');
        assert.equal(await entries.count(), expected);
        plainAnchors.push(...await entries.evaluateAll(nodes => nodes.map(node => node.id)));
        if (index < 2) await plain.locator('.guavajam-static-pages a[rel=next]').first().click();
      }
      assert.equal(plainAnchors.length, fixture.manuscriptPassages);
      assert.equal(new Set(plainAnchors).size, fixture.manuscriptPassages);
      assert.equal(plainAnchors.at(-1), 'citation:section-44');
      await disabled.close();

      // A failed citation enhancement keeps the current static page and exact
      // fragment usable; it never sends the reader back through earlier pages.
      const citationFailure = await browser.newContext();
      await citationFailure.route(/\/work\.[a-f0-9]{64}\.sqlite\.gz$/, route => route.abort());
      const citationFallback = await citationFailure.newPage();
      await citationFallback.goto(deepCitationURL + '#citation:section-44');
      await citationFallback.locator('input[name=q]').fill('Bathyal');
      await citationFallback.locator('#guavajam-search button[type=submit]').click();
      await citationFallback.waitForFunction(() => document.querySelector('#guavajam-search-status').textContent.includes('unavailable'));
      assert.equal(await citationFallback.locator('#guavajam-static-section').isVisible(), true);
      assert.equal(await citationFallback.locator('#guavajam-search-results').isVisible(), false);
      await citationFallback.locator('[id="citation:section-44"]').getByRole('link', {name: 'Read this passage', exact: true}).click();
      assert.equal(citationFallback.url(), base + fixture.lastSection);
      await citationFailure.close();

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
        assert.equal(await fallback.locator('#guavajam-search-listing').isVisible(), true);
        assert.equal(await fallback.locator('#guavajam-search-status').isVisible(), true);
        assert.equal(await fallback.locator('#guavajam-static-section a').count(), 0);
        assert.equal(await fallback.locator('#guavajam-search-enable').textContent(), 'Retry search');
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
        staticDirectory: true, staticCitations: true, citationSearch: true,
        citationScope: true, citationFallback: true, fallback: true, accessibility: true});
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
