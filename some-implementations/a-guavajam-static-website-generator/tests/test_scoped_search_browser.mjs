// Native browser/network witnesses for scope, activation and recovery.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
import axe from 'axe-core';
import {fixedPageHorizontalScroll, searchEvidence} from './interaction_contract.mjs';

const [fixturePath, output] = process.argv.slice(2);
const fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
await fs.mkdir(output, {recursive: true});
const root = path.resolve(fixture.root), served = [];
let fault = null;
const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://localhost');
    const filename = path.resolve(root, '.' + decodeURIComponent(url.pathname) + (url.pathname.endsWith('/') ? 'index.html' : ''));
    assert(filename.startsWith(root + path.sep));
    let body = await fs.readFile(filename);
    served.push(url.pathname);
    if (fault === 'wrong-generation' && url.pathname.endsWith('/SEARCH.json')) {
      const routing = JSON.parse(body.toString());
      if (routing.history) {
        routing.history.generation = '0'.repeat(64);
        body = Buffer.from(JSON.stringify(routing)); fault = null;
      }
    }
    if (fault && url.pathname.includes('/history.') && url.pathname.endsWith('.sqlite.gz')) {
      const active = fault; fault = null;
      if (active === 'delay') await new Promise(resolve => setTimeout(resolve, 1200));
      if (active === 'truncate') body = body.subarray(0, body.length - 9);
      if (active === 'digest') { body = Buffer.from(body); body[4] ^= 1; }
    }
    const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
      '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.gz': 'application/gzip', '.json': 'application/json'};
    response.writeHead(200, {'Content-Type': types[path.extname(filename)] || 'application/octet-stream',
      'Cache-Control': filename.endsWith('.sqlite.gz') ? 'public,max-age=31536000,immutable' : 'no-cache',
      'Content-Length': body.length, 'X-Content-Type-Options': 'nosniff'});
    response.end(body);
  } catch { response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = 'http://127.0.0.1:' + server.address().port + '/';
const work = base + fixture.work;
const engines = {chromium, firefox, webkit};
const selected = (process.env.GUAVAJAM_BROWSER_ENGINES || 'chromium,firefox,webkit').split(',');
assert(selected.length && selected.every(name => Object.hasOwn(engines, name)));
const reports = [];
const indexRequest = url => /(?:\.wasm|sqlite3\.mjs|search-worker\.js|\.sqlite(?:\.gz)?)(?:\?|$)/.test(url);
async function complete(page, total, history = false) {
  try {
    await page.waitForFunction(expected => {
      const results = document.querySelector('#guavajam-search-results');
      return !results.hidden && !results.hasAttribute('aria-busy') &&
        document.querySelector('#guavajam-search-status').textContent === expected;
    }, String(total) + (history ? (total === 1 ? ' passage' : ' passages') : ' total'));
  } catch (error) {
    const state = await page.evaluate(() => ({url: location.href,
      status: document.querySelector('#guavajam-search-status').textContent,
      message: document.querySelector('#guavajam-search-message')?.textContent,
      scope: document.querySelector('form').dataset.scope,
      errors: window.scopeErrors || []}));
    throw new Error('Search did not complete: ' + JSON.stringify(state), {cause: error});
  }
}
async function submit(page, term, total, history = false) {
  await page.locator('input[name=q]').fill(term);
  await page.locator('#guavajam-search button[type=submit]').click();
  await complete(page, total, history);
}
async function open(page) {
  await page.locator('#guavajam-search-toggle').click();
}
try {
  for (const name of selected) {
    const executablePath = name === 'chromium' ? process.env.GUAVAJAM_CHROMIUM_EXECUTABLE : undefined;
    const browser = await engines[name].launch(executablePath ? {executablePath} : {});
    const errors = [];
    try {
      const context = await browser.newContext({viewport: {width: 390, height: 844}});
      await context.addInitScript(() => {
        window.scopeErrors = [];
        const OriginalWorker = window.Worker;
        window.Worker = class extends OriginalWorker {
          constructor(...args) {
            super(...args);
            this.addEventListener('message', event => {
              if (event.data?.ok === false) window.scopeErrors.push(event.data);
            });
          }
        };
      });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.on('pageerror', error => errors.push(error.message));
      const requests = [];
      context.on('request', request => requests.push(request.url()));
      for (const [url, scope, term] of [
        [base, 'index', 'Currenttitle'],
        [work, 'versions', 'Currentmessageselector'],
        [work + 'editions/' + fixture.latest + '/read/documents.html', 'documents', 'Directoryabstractselector'],
        [work + 'editions/' + fixture.latest + '/read/passages.html', 'passages', 'Currentbodyselector']
      ]) {
        requests.length = 0;
        await page.goto(url);
        assert.equal(requests.filter(indexRequest).length, 0, scope + ': page load is passive');
        await open(page);
        if (scope === 'versions') {
          const versions = page.locator('input[name=search-scope][value=versions]');
          const history = page.locator('input[name=search-scope][value=history]');
          await versions.focus(); await versions.press('ArrowRight');
          assert(await history.isChecked());
          assert(await history.evaluate(node => node === document.activeElement));
          await history.press('ArrowLeft');
          assert(await versions.isChecked());
          assert(await versions.evaluate(node => node === document.activeElement));
        }
        await page.locator('input[name=q]').fill(term);
        await page.locator('summary.guavajam-disclosure-summary .guavajam-disclosure-arrow').click();
        await page.locator('select[name=sort]').selectOption('relevance');
        await page.waitForTimeout(300);
        assert.equal(requests.filter(indexRequest).length, 0, scope + ': reveal, typing and filters stay passive');
        assert(Number(await page.locator('input[name=q]').evaluate(node => parseFloat(getComputedStyle(node).fontSize))) >= 16);
        const staticTotal = Number((await page.locator('#guavajam-search-status').textContent()).match(/[0-9]+/)[0]);
        await page.locator('input[name=q]').fill('');
        await page.locator('#guavajam-search button[type=submit]').click();
        await complete(page, staticTotal);
        await page.locator('input[name=q]').fill(term);
        await page.locator('#guavajam-search button[type=submit]').click();
        await complete(page, 1);
        await searchEvidence(page);
        const artifacts = requests.filter(value => /\.sqlite\.gz(?:\?|$)/.test(value));
        assert.equal(artifacts.length, 1);
        assert(path.basename(new URL(artifacts[0]).pathname).startsWith(scope + '.'));
        assert(!artifacts.some(value => value.includes('/history.')));
        assert(!requests.some(value => /(?:fflate\.mjs|search-chunks\.js)$/.test(new URL(value).pathname)), 'Small scopes avoid loading the chunk implementation');
        if (scope === 'documents' || scope === 'passages')
          assert(artifacts[0].includes('/editions/' + fixture.latest + '/'));
        await fixedPageHorizontalScroll(page);
        if (scope === 'index' || scope === 'versions') {
          const before = requests.filter(indexRequest).length;
          await page.locator('input[name=q]').fill('Historicalbodyselector');
          await complete(page, 0);
          assert.equal(requests.filter(indexRequest).length, before, 'Loaded ordinary refinements reuse the corpus');
        }
      }

      await page.goto(work + 'editions/' + fixture.latest + '/read/passages.html');
      await open(page);
      for (const term of ['Windowstartselector Windowendselector', 'Oversizedprefixselector']) {
        await submit(page, term, 1);
        await searchEvidence(page);
        assert((await page.locator('#guavajam-search-results .guavajam-search-excerpt').textContent()).length < 2400);
        await fixedPageHorizontalScroll(page);
      }

      // Generic options are independent of the main search database, and
      // selected values retain exact literal identity in queries and URLs.
      requests.length = 0;
      const documentsURL = work + 'editions/' + fixture.latest + '/read/documents.html';
      await page.goto(documentsURL);
      await open(page);
      await page.locator('summary.guavajam-disclosure-summary .guavajam-disclosure-arrow').click();
      await page.locator('#guavajam-metadata-key option[value="Literal"]').waitFor({state: 'attached'});
      await page.locator('#guavajam-metadata-key').selectOption('Literal');
      await page.locator('#guavajam-metadata-value option[value="001"]').waitFor({state: 'attached'});
      assert.equal(requests.filter(indexRequest).length, 0, 'Literal options do not initialize the search index');
      await page.getByRole('button', {name: 'Add filter', exact: true}).click();
      await page.locator('#guavajam-metadata-key').selectOption('Applies to');
      await page.locator('#guavajam-metadata-value option[value="01"]').waitFor({state: 'attached'});
      for (const value of ['01', 'A', '']) {
        await page.locator('#guavajam-metadata-value').selectOption(value);
        await page.getByRole('button', {name: 'Add filter', exact: true}).click();
      }
      assert.equal(requests.filter(indexRequest).length, 0, 'Selecting filter values remains passive');
      await page.locator('#guavajam-search button[type=submit]').click();
      await complete(page, 1);
      const filters = JSON.parse(new URL(page.url()).searchParams.get('metadata'));
      assert.deepEqual(filters, [{key: 'Literal', values: ['001']}, {key: 'Applies to', values: ['01','A','']}]);
      await page.goto(documentsURL + '?q=&metadata=' + encodeURIComponent(JSON.stringify([
        {key: 'Literal', values: ['001']}, {key: 'Applies to', values: ['a']}]))); 
      await complete(page, 0);
      requests.length = 0;
      await page.goto(documentsURL);
      await open(page);
      await page.locator('summary.guavajam-disclosure-summary .guavajam-disclosure-arrow').click();
      await page.locator('#guavajam-metadata-key option[value=Longliteral]').waitFor({state:'attached'});
      await page.locator('#guavajam-metadata-key').selectOption('Longliteral');
      await page.waitForFunction(() => document.querySelector('#guavajam-metadata-value')?.options[0]?.value.length === 28000);
      await page.getByRole('button', {name:'Add filter',exact:true}).click();
      assert.equal(requests.filter(indexRequest).length,0);
      await page.locator('#guavajam-search button[type=submit]').click();
      await complete(page,1);
      const largeStateURL = page.url();
      assert(new URL(largeStateURL).hash.startsWith('#guavajam-metadata='));
      assert(!new URL(largeStateURL).searchParams.has('metadata'));
      await fixedPageHorizontalScroll(page);
      await page.goto(largeStateURL);
      await complete(page,1);
      if (!await page.locator('details.guavajam-search-filters').evaluate(node => node.open))
        await page.locator('summary.guavajam-disclosure-summary .guavajam-disclosure-arrow').click();
      const selectedLarge = page.locator('.guavajam-selected-filters button').filter({hasText: /^Remove Longliteral:/});
      await selectedLarge.waitFor();
      assert.equal(await selectedLarge.textContent(), 'Remove Longliteral: ' + 'İ'.repeat(28000));
      await page.locator('input[name=q]').fill('Directoryabstractselector');
      await page.locator('#guavajam-search button[type=submit]').click();
      await complete(page,1);
      await selectedLarge.click();
      await complete(page,1);
      assert.equal(new URL(page.url()).hash,'');
      await page.goBack();
      await complete(page,1);
      assert(new URL(page.url()).hash.startsWith('#guavajam-metadata='));
      assert(requests.every(value => new URL(value).pathname.length + new URL(value).search.length < 4096));

      await page.goto(base);
      await open(page);
      await page.locator('summary.guavajam-disclosure-summary .guavajam-disclosure-arrow').click();
      await page.locator('select[name=keyword] option[value=Currenttag]').waitFor({state: 'attached'});
      assert.equal(await page.locator('select[name=subject] option').count(), 3);
      const subjects = await page.locator('select[name=subject]').textContent();
      assert(subjects.includes('Authority A') && subjects.includes('Authority B'));
      await page.locator('input[name=from]').fill('2026-01-01');
      await page.locator('#guavajam-search button[type=submit]').click();
      await complete(page, 0);
      await page.locator('input[name=from]').fill('2025-01-01');
      await complete(page, 1);

      requests.length = 0;
      await page.goto(work + '?scope=history');
      await page.waitForTimeout(300);
      assert.equal(requests.filter(indexRequest).length, 0, 'A mode-only URL is passive');
      assert(await page.locator('input[name=search-scope][value=history]').isChecked());
      await page.locator('input[name=q]').fill('Historicalbodyselector');
      await page.waitForTimeout(300);
      assert.equal(requests.filter(indexRequest).length, 0, 'Selecting and editing history never downloads it');
      assert(await page.locator('#guavajam-search-results').isHidden());
      await page.locator('#guavajam-search button[type=submit]').click();
      await complete(page, 1, true);
      await searchEvidence(page);
      const historicalTarget = await page.locator('#guavajam-search-results h3 a').first().getAttribute('href');
      assert(historicalTarget.includes(fixture.older));
      assert(historicalTarget.startsWith(work), 'History targets stay in this work');
      assert.equal((await page.request.get(historicalTarget)).status(), 200, 'Historical source target is reachable');
      assert.equal(await page.locator('#guavajam-search-results .guavajam-citation').count(), 1);
      const historyCount = requests.filter(value => /\/history\..*\.sqlite\.gz$/.test(value)).length;
      assert.equal(historyCount, 1);
      await page.screenshot({path: path.join(output, name + '-history-mobile.png'), fullPage: true});
      await page.locator('input[name=q]').fill('Missingtoken');
      await page.waitForTimeout(300);
      assert(await page.locator('#guavajam-search-results').isHidden(), 'History edits require deliberate execution');
      assert.equal(requests.filter(value => /\/history\..*\.sqlite\.gz$/.test(value)).length, historyCount);
      await page.locator('#guavajam-search button[type=submit]').click();
      await complete(page, 0, true);

      await page.goto(work + '?scope=history&q=Historicalbodyselector');
      await complete(page, 1, true);
      await page.locator('input[name=search-scope][value=versions]').check();
      assert(await page.locator('#guavajam-search-results').isHidden());
      await submit(page, 'Currentmessageselector', 1);
      await page.goBack();
      assert(await page.locator('input[name=search-scope][value=versions]').isChecked());
      assert(await page.locator('#guavajam-search-results').isHidden());
      await page.goBack();
      await complete(page, 1, true);
      assert(await page.locator('input[name=search-scope][value=history]').isChecked());

      await page.evaluate(axe.source);
      const accessibility = await page.evaluate(async () => {
        const results = await axe.run(document, {rules: {'color-contrast': {enabled: true}}});
        return results.violations.filter(item => ['serious', 'critical'].includes(item.impact)).map(item => item.id);
      });
      assert.deepEqual(accessibility, []);
      await context.close();

      for (const scenario of ['cancel', 'retry', 'switch', 'digest', 'generation']) {
        const recoveryContext = await browser.newContext({viewport: {width: 390, height: 844}});
        const recovery = await recoveryContext.newPage();
        recovery.setDefaultTimeout(15000);
        recovery.on('pageerror', error => errors.push(error.message));
        await recovery.goto(work + '?scope=history');
        await recovery.locator('input[name=q]').fill('Historicalbodyselector');
        fault = scenario === 'retry' ? 'truncate' : scenario === 'digest' ? 'digest' :
          scenario === 'generation' ? 'wrong-generation' : 'delay';
        const fetching = recovery.waitForRequest(request => /\/history\..*\.sqlite\.gz$/.test(request.url()));
        await recovery.locator('#guavajam-search button[type=submit]').click();
        await fetching;
        if (['retry', 'digest', 'generation'].includes(scenario)) {
          await recovery.getByRole('button', {name: 'Retry search', exact: true}).waitFor();
          assert(await recovery.locator('#guavajam-search-results').isHidden());
          assert.equal(await recovery.locator('#guavajam-search-status').textContent(), '3 total');
          await recovery.getByRole('button', {name: 'Retry search', exact: true}).click();
          await complete(recovery, 1, true);
        } else if (scenario === 'cancel') {
          await recovery.getByRole('button', {name: 'Cancel search', exact: true}).click();
          await recovery.waitForTimeout(1400);
          assert(await recovery.locator('#guavajam-search-results').isHidden());
          assert.equal(await recovery.locator('#guavajam-search-status').textContent(), '3 total');
        } else {
          await recovery.locator('input[name=search-scope][value=versions]').check();
          await submit(recovery, 'Currentmessageselector', 1);
          await recovery.waitForTimeout(1400);
          assert.equal(await recovery.locator('#guavajam-search-status').textContent(), '1 total');
          assert((await recovery.locator('#guavajam-search-results').textContent()).includes('Currentmessageselector'));
        }
        await recoveryContext.close();
      }

      const staticContext = await browser.newContext({javaScriptEnabled: false});
      const staticPage = await staticContext.newPage();
      await staticPage.goto(work);
      assert.equal(await staticPage.locator('#guavajam-static-section li').count(), 3);
      assert(await staticPage.locator('#guavajam-index-search').isHidden());
      assert.equal(await staticPage.locator('a[rel=alternate]').count(), 0);
      await staticContext.close();
      assert.deepEqual(errors, []);
      reports.push({engine: name, passed: true, passive_surfaces: 4, scoped_artifacts: true,
        explicit_history: true, deep_links_and_navigation: true, real_cancel_retry_switch: true,
        metadata_highlights: true, mobile_width: true, accessibility: true, static_fallback: true});
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({passed: true, reports}, null, 2));
