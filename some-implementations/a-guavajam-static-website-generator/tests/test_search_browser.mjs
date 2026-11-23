// Real SQLite WASM, real signed inputs, and ordinary static HTTP without COOP/COEP.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
import axe from 'axe-core';
import {keyboardControls, hoverControls, instrumentSearch, openSearchFilters, searchFeedbackGeometry, searchEvidence, recordLinkAlignment, listControlLayout, listHeadingOverflow, fixedPageHorizontalScroll, searchHighlightContrast, linkPalette} from './interaction_contract.mjs';

const [fixturePath, output] = process.argv.slice(2);
assert(fixturePath && output, 'Use some-implementations/a-guavajam-static-website-generator/tests/run_search_browser.py');
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
      await instrumentSearch(page);
      page.setDefaultTimeout(15000);
      const errors = [], external = [], loaded = [];
      page.on('pageerror', error => errors.push(error.message));
      context.on('request', request => {
        loaded.push(request.url());
        if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin) external.push(request.url());
      });
      const waitTotal = async total => {
        try {
          await page.waitForFunction(total => !document.querySelector('#guavajam-search-results').hidden && !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') &&
            document.querySelector('#guavajam-search-status').textContent === `${total} total`, total);
        } catch (error) {
          throw new Error(`${name}: expected ${total} matches; status=${await page.locator('#guavajam-search-status').textContent()}; browser errors=${errors.join('; ')}`, {cause: error});
        }
      };
      // All four directories use the same closed search panel. Inspect
      // its heading toggle at phone widths without activating SQLite, plus
      // form typography for the controls available once it opens.
      const parityContext = await browser.newContext();
      const parityPage = await parityContext.newPage();
      const parityDownloads = [];
      parityContext.on('request', request => {
        if (/(?:\.wasm|\.sqlite(?:\.gz)?|search-worker\.js)(?:\?|$)/.test(request.url()))
          parityDownloads.push(request.url());
      });
      await parityPage.goto(base + 'sources/work-02/index.html');
      const latest = new URL(await parityPage.locator('#guavajam-static-section .guavajam-editions h3 a').first().getAttribute('href'), parityPage.url()).href;
      const surfaces = [base + 'sources/work-02/index.html',
        latest.replace('index.html', 'documents.html'), latest.replace('index.html', 'passages.html')];
      const controls = async () => {
        await parityPage.evaluate(async () => {
          await document.fonts.ready;
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        return parityPage.evaluate(() => {
          const section = document.querySelector('.guavajam-list-header').getBoundingClientRect();
          const toggle = document.querySelector('#guavajam-search-toggle').getBoundingClientRect();
          const crumb = document.querySelector('.guavajam-navigation').getBoundingClientRect();
          const geometry = {sectionWidth: section.width, toggleHeight: toggle.height,
            toggleOffset: toggle.top - section.top, headingGap: section.top - crumb.bottom};
          const style = getComputedStyle(document.querySelector('#guavajam-search-toggle'));
          const typography = Object.fromEntries(['fontSize', 'fontFamily', 'fontWeight', 'lineHeight'].map(property => [property, style[property]]));
          return {geometry, typography, collapsed: document.querySelector('#guavajam-index-search').hidden};
        });
      };
      for (const width of [320, 390, 500, 760]) {
        await parityPage.setViewportSize({width, height: 900});
        await parityPage.goto(base);
        const reference = await controls();
        assert.equal(reference.collapsed, true);
        for (const url of surfaces) {
          await parityPage.goto(url);
          const actual = await controls();
          assert.deepEqual(actual.typography, reference.typography, `${name}: ${width}px control text at ${url}`);
          for (const [property, expected] of Object.entries(reference.geometry)) {
            assert(Math.abs(actual.geometry[property] - expected) < .5,
              `${name}: ${width}px ${property} at ${url}: ${actual.geometry[property]} versus ${expected}`);
          }
          assert.equal(await parityPage.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
        }
      }
      assert.deepEqual(parityDownloads, [], 'layout parity must not activate SQLite');
      // Preserve the established Works row contract across every static and
      // enhanced directory, including deliberately long content at phone widths.
      const rowStyle = async row => row.evaluate(node => {
        const box = node.getBoundingClientRect(), article = node.querySelector('article').getBoundingClientRect();
        const title = node.querySelector('h3'), metaNode = node.querySelector('.guavajam-byline, .guavajam-meta');
        const heading = getComputedStyle(title), text = getComputedStyle(title.querySelector('a')), meta = metaNode ? getComputedStyle(metaNode) : null;
        const border = parseFloat(getComputedStyle(node).borderBottomWidth);
        return {meta: meta ? {...Object.fromEntries(['fontSize','fontFamily','lineHeight','color','whiteSpace','overflowX'].map(k => [k, meta[k]])),
            gap: metaNode.getBoundingClientRect().top-title.getBoundingClientRect().bottom} : null,
          row: {paddingTop: article.top-box.top, paddingBottom: box.bottom-article.bottom-border, borderBottomWidth:border},
          title: {...Object.fromEntries(['fontFamily','fontSize','fontWeight','lineHeight'].map(k => [k, text[k]])),
            whiteSpace:heading.whiteSpace, overflowX:heading.overflowX}};
      });
      const assertRowStyle = (actual, expected, message) => {
        // Engines preserve fractional layout positions with different floating
        // precision. Numeric geometry must agree within half a CSS pixel;
        // typography and overflow contracts still match exactly.
        for (const [group, values] of Object.entries(expected)) for (const [property, value] of Object.entries(values)) {
          if (actual[group] === null) continue; // Documents and Passages intentionally omit redundant metadata rows.
          if (typeof value === 'number') assert(Math.abs(actual[group][property]-value)<.5,
            `${message}: ${group}.${property}: ${actual[group][property]} versus ${value}`);
          else assert.equal(actual[group][property],value,`${message}: ${group}.${property}`);
        }
      };
      const contain = async () => {
        const rows = parityPage.locator('.guavajam-record-list:visible');
        assert(await rows.count() > 0);
        const heading = rows.first().locator('h3').first();
        await heading.locator('.guavajam-link-label').first().evaluate(a => { a.textContent = 'Extremelylongunbrokentitle'.repeat(35); });
        const source = rows.first().locator('.guavajam-byline, .guavajam-meta').first();
        if (await source.count()) await source.evaluate(n => {
          n.append(document.createTextNode(' Verylongsourcepath'.repeat(60)));
        });
        const citation = rows.first().locator('.guavajam-citation-description').first();
        if (await citation.count()) await citation.evaluate(n => {
          n.append(document.createTextNode('Verylongunbrokenbibliographicname'.repeat(60)));
        });
        for (const width of [320,390,760]) {
          await parityPage.setViewportSize({width, height:900});
          for (const scale of ['', '34px']) {
            await parityPage.evaluate(font => { document.documentElement.style.fontSize = font; }, scale);
            const bad = await parityPage.evaluate(() => {
              const main = document.querySelector('main').getBoundingClientRect();
              return [...document.querySelectorAll('.guavajam-record-list, .guavajam-record-list > li, .guavajam-record-list > li > article, .guavajam-record-list h3, .guavajam-record-list .guavajam-scrollline, .guavajam-record-list .guavajam-citation')]
                .filter(n => n.getClientRects().length).filter(n => {
                  const b = n.getBoundingClientRect(); return b.left < main.left - (n.matches("h3, .guavajam-scrollline") ? 7 : 1) || b.right > main.right + (n.matches("h3, .guavajam-scrollline") ? 7 : 1);
                }).map(n => n.className || n.tagName);
            });
            assert.deepEqual(bad, [], `${name}: contained record boxes at ${width}px/${scale}`);
            assert(await parityPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
          }
        }
        await parityPage.evaluate(() => { document.documentElement.style.fontSize = ''; });
        await parityPage.setViewportSize({width:390,height:900});
        const titleLink = heading;
        await titleLink.focus(); await titleLink.press('ArrowRight');
        await parityPage.waitForTimeout(100);
        assert(await titleLink.evaluate(n => n.scrollLeft > 0), 'long titles are keyboard-scrollable');
        await keyboardControls(parityPage);
        await parityPage.emulateMedia({media:'print'});
        assert.equal(await heading.evaluate(n => getComputedStyle(n).whiteSpace), 'normal');
        assert(await parityPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await parityPage.emulateMedia({media:'screen'});
      };
      await parityPage.setViewportSize({width:390,height:900});
      await parityPage.goto(base);
      const referenceRow = await rowStyle(parityPage.locator('#guavajam-static-list > li').first());
      const referenceLast = await rowStyle(parityPage.locator('#guavajam-static-list > li').last());
      const expectedRow = async row => await row.evaluate(n => n.matches(':last-child')) ?
        {...referenceRow, row: {...referenceRow.row, paddingBottom:referenceLast.row.paddingBottom, borderBottomWidth:referenceLast.row.borderBottomWidth}} : referenceRow;
      for (const url of [base, ...surfaces]) {
        await parityPage.goto(url);
        const staticRow = parityPage.locator('.guavajam-record-list:visible > li').first();
        assertRowStyle(await rowStyle(staticRow), await expectedRow(staticRow), `${name}: static Works row parity at ${url}`);
        await listHeadingOverflow(parityPage);
        await contain();
        await parityPage.locator('#guavajam-search-toggle').click();
        await parityPage.waitForFunction(() => !document.querySelector('#guavajam-search-results').hidden &&
          !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy'));
        assertRowStyle(await rowStyle(parityPage.locator('#guavajam-search-results > li').first()), await expectedRow(parityPage.locator('#guavajam-search-results > li').first()),
          `${name}: SQLite Works row parity at ${url}`);
        for (const width of [320,390,760,1280]) {
          await parityPage.setViewportSize({width, height:900});
          await listControlLayout(parityPage);
          if (width===390) await listHeadingOverflow(parityPage);
        }
        await parityPage.setViewportSize({width:390,height:900});
        await contain();
      }
      await parityContext.close();
      if (name === 'webkit') {
        const phone = await browser.newContext({viewport: {width: 390, height: 844},
          isMobile: true, hasTouch: true, deviceScaleFactor: 3});
        const mobile = await phone.newPage();
        for (const url of [base, ...surfaces]) {
          await mobile.goto(url);await mobile.locator('#guavajam-search-toggle').tap();
          await mobile.waitForFunction(() => !document.querySelector('#guavajam-search-results').hidden &&
            !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy'));
          await fixedPageHorizontalScroll(mobile, {touch: true});
        }
        await phone.close();
      }
      const query = async (term, total) => {
        await page.locator('input[name=q]').fill(term);
        await page.locator('#guavajam-search button[type=submit]').click();
        if (total === undefined) await page.waitForFunction(() =>
          !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy'));
        else await waitTotal(total);
        if (/[\p{L}\p{N}]/u.test(term)) await searchEvidence(page);
        await recordLinkAlignment(page);
      };
      await page.goto(base);
      await fixedPageHorizontalScroll(page);
      await recordLinkAlignment(page);
      assert.equal(await page.locator('#guavajam-static-list > li').count(), 20);
      assert.equal(await page.locator('#guavajam-static-list').isVisible(), true);
      assert.equal(await page.locator('#guavajam-search-status').textContent(), `${fixture.works} total`);
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Works');
      assert.equal(await page.locator('#guavajam-search-spinner').isVisible(), false);
      assert.equal(await page.locator('#guavajam-static-list .guavajam-facet').count() > 0, true);
      assert.equal(await page.locator('#guavajam-static-list button.guavajam-facet-type').count(), 0,
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
      await page.locator('#guavajam-search-toggle').click();
      await waitTotal(fixture.works);
      assert.equal(await page.locator('#guavajam-search-spinner').isVisible(), false);
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Works');
      assert.equal(await page.locator('#guavajam-search-results .guavajam-facet').count() > 0, true);
      const pagedResult = await page.locator('#guavajam-search-results h3 a').first().getAttribute('href');
      assert(pagedResult.startsWith('../sources/'));
      await query('Glaciolacustrine', 1);
      const pagedHit = await page.locator('.guavajam-excerpt-label a').getAttribute('href');
      assert(pagedHit.startsWith('../sources/'));
      assert((await page.request.get(new URL(pagedHit, page.url()).href)).ok());
      await page.goto(base);
      await page.locator('#guavajam-search-toggle').focus();
      await page.locator('#guavajam-search-toggle').press('Enter');
      await waitTotal(fixture.works);
      assert.equal(await page.locator('#guavajam-search-toggle').evaluate(node => node === document.activeElement), true,
        'the heading search toggle retains keyboard focus after loading');
      assert.equal(await page.evaluate(() => crossOriginIsolated), false);
      assert.equal(await page.locator('#guavajam-static-list').isVisible(), false);
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      assert.equal(await page.locator('#guavajam-search-results h3 .guavajam-link-label').first().textContent(), 'Work 22');
      await page.locator('#guavajam-search-next').focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 2);
      assert.equal(await page.locator('#guavajam-search-next').isDisabled(), true);
      assert.equal(await page.locator(':focus').getAttribute('id'), 'guavajam-search-results',
        'paging hands keyboard focus to the new results before disabling the last-page control');
      await page.keyboard.press('Tab');
      assert.equal(await page.locator('#guavajam-search-results h3').first().evaluate(n=>n===document.activeElement),true);
      await page.keyboard.press('Tab');
      assert.equal(await page.locator('#guavajam-search-results h3 a').first().evaluate(n => n === document.activeElement), true,
        'Tab continues into the new page of results');
      await openSearchFilters(page);
      await page.locator('select[name=sort]').focus();
      await page.locator('select[name=sort]').selectOption('title');
      await page.waitForFunction(() => document.querySelector('#guavajam-search-results h3 .guavajam-link-label').textContent === 'Work 01');
      assert.equal(await page.locator('select[name=sort]').evaluate(n => n === document.activeElement), true,
        'changing a filter keeps focus on that filter');
      assert.equal(await page.locator('#guavajam-search-page').textContent(), 'Page 1 of 2');
      await query('Palaeoriver', 1);
      assert.equal(await page.locator('.guavajam-match-source').textContent(), 'v2 · Old geography');
      assert.equal(await page.locator('.guavajam-passage-excerpt mark').textContent(), 'Palaeoriver');
      assert.equal(await page.locator('#guavajam-search-results h3 .guavajam-link-label').textContent(), 'Work 01');
      assert.equal(await page.locator('.guavajam-excerpt-label a').getAttribute('href'), fixture.historical + '#opening');
      await page.locator('.guavajam-excerpt-label a').click();
      assert(page.url().endsWith(fixture.historical + '#opening'));
      assert((await page.locator('article').textContent()).includes('Palaeoriver'));
      await page.goto(base);
      await page.locator('#guavajam-search-toggle').click();
      await waitTotal(fixture.works);
      await fixedPageHorizontalScroll(page);
      await query('Glaciolacustrine', 1);
      assert.equal(await page.locator('.guavajam-excerpt-label .guavajam-link-label').textContent(), 'Document text match');
      assert.equal(await page.locator('.guavajam-passage-excerpt mark').textContent(), 'Glaciolacustrine');
      await searchHighlightContrast(page);await linkPalette(page);
      const support = await page.locator('.guavajam-excerpt-label a').getAttribute('href');
      assert(support.startsWith('sources/work-02/editions/'));
      assert(support.endsWith('/read/supporting/evidence.html#claim'));
      assert.equal((await page.request.get(base + support)).status(), 200);
      // Follow the actual reason control: it must reveal the cited source block.
      const destination = await context.newPage();
      await destination.goto(base);
      await destination.locator('#guavajam-search-toggle').click();
      await destination.waitForFunction(() => !document.querySelector('#guavajam-search-results').hidden && !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy'));
      await destination.locator('input[name=q]').fill('Glaciolacustrine');
      await destination.locator('#guavajam-search button[type=submit]').click();
      await destination.waitForFunction(() => !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') && document.querySelector('#guavajam-search-status').textContent==='1 total');
      await destination.locator('.guavajam-excerpt-label a').click();
      assert.equal(new URL(destination.url()).hash,'#claim');
      assert((await destination.locator(':target').textContent()).includes('Glaciolacustrine'));
      await destination.close();
      await query('Freeprosebodyevidence', 1);
      const freeProse = await page.locator('.guavajam-excerpt-label a').getAttribute('href');
      assert(freeProse.endsWith('/read/index.html#:~:text=Freeprosebodyevidence'));
      assert.equal(await page.locator('.guavajam-passage-excerpt mark').textContent(),'Freeprosebodyevidence');
      const freeResponse=await page.request.get(new URL(freeProse,page.url()).href);
      assert(freeResponse.ok() && (await freeResponse.text()).includes('Freeprosebodyevidence'));
      await query('cafe collection', 1); // metadata and body share the same search record
      assert.equal(await page.locator('#guavajam-search-results .guavajam-tag mark').textContent(), 'Café');
      // Commit identifiers are searchable too: another random fixture hash can
      // start with 01. Assert the intended title hit without excluding those hits.
      await query('Work 01');
      const titleHit = page.locator('#guavajam-search-results > li').filter({
        has: page.getByRole('link', {name: 'Work 01', exact: true})});
      assert.equal(await titleHit.count(), 1);
      assert.equal((await titleHit.locator('h3 mark').allTextContents()).join(' '), 'Work 01');
      assert.equal(await titleHit.locator('h3 .guavajam-link-label').textContent(), 'Work 01');
      await query("zznonexistent' OR 1=1 --", 0); // never SQL or an FTS expression
      await query('!!!', fixture.works);
      await query('', fixture.works);
      await page.locator('.guavajam-search-filters summary .guavajam-disclosure-arrow').click();
      const options = await page.locator('select[name=subject] option').evaluateAll(nodes => nodes.slice(1).map(n => ({key: n.value, label: n.textContent})));
      assert(options.filter(item => item.label.includes('Java · ')).length >= 2,
        'same-name subjects keep their distinct authority metadata');
      assert.equal(options.length, 2);
      assert.equal(new Set(options.map(o => o.key)).size, 2);
      assert(options.every(o => o.label.includes('Java')));
      for (const option of options) {
        await page.locator('select[name=subject]').selectOption(option.key);
        await waitTotal(1);
        assert(['Work 01', 'Work 02'].includes(await page.locator('#guavajam-search-results h3 .guavajam-link-label').textContent()));
      }
      await page.locator('select[name=subject]').selectOption('');
      await page.locator('select[name=keyword]').selectOption('Café studies');
      await waitTotal(1);
      assert.equal(await page.locator('#guavajam-search-results h3 .guavajam-link-label').textContent(), 'Work 01');
      assert.equal(await page.locator('select[name=keyword] option').filter({hasText: 'Retired topic'}).count(), 0);
      await page.locator('select[name=keyword]').selectOption('');
      await waitTotal(fixture.works);
      // Facet controls are native buttons only once SQLite is enabled.
      const fieldTag = page.locator('#guavajam-search-results .guavajam-tag > button.guavajam-facet-type').first();
      assert.equal(await fieldTag.getAttribute('data-value'), 'Field observations');
      await fieldTag.focus(); await page.keyboard.press('Enter');
      await waitTotal(fixture.works - 1);
      assert.equal(await page.locator('select[name=keyword]').inputValue(), 'Field observations');
      assert.equal(await page.locator('#guavajam-search-results > li').count(), 20);
      assert.equal(await page.locator(':focus').getAttribute('id'), 'guavajam-search-results',
        'replacing an activated facet never drops focus to the document body');
      await page.keyboard.press('Tab');
      assert.equal(await page.locator('#guavajam-search-results h3').first().evaluate(n=>n===document.activeElement),true);
      await page.keyboard.press('Tab');
      assert.equal(await page.locator('#guavajam-search-results h3 a').first().evaluate(n => n === document.activeElement), true);
      await page.locator('#guavajam-search-results .guavajam-tag > button.guavajam-facet-type').first().click();
      await waitTotal(fixture.works);
      assert.equal(await page.locator('select[name=keyword]').inputValue(), '');
      // A distinct subject authority filters by its exact SHA-derived key.
      await page.locator('#guavajam-search-next').click();
      await page.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      const subjectButton = page.locator('#guavajam-search-results .guavajam-subject > button.guavajam-facet-type').first();
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
      await page.locator('#guavajam-search-results h3 .guavajam-link-label').first().evaluate(a => {
        a.textContent = 'Very long work title '.repeat(35);
      });
      await page.locator('#guavajam-search-results .guavajam-byline').first().evaluate(p => {
        p.append(document.createTextNode('An exceptionally long bibliographic byline '.repeat(25)));
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
      await keyboardControls(page);
      await hoverControls(page);
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
      // Delay genuine SQLite replies at the worker boundary. This lets the
      // reader move focus while a request is in flight, without fake results.
      await delayed.addInitScript(() => {
        const NativeWorker = window.Worker;
        window.guavajamSearchTest = {hold: false, replies: [], requests: 0};
        window.Worker = class extends NativeWorker {
          postMessage(...args) {
            if (args[0]?.action === 'search') window.guavajamSearchTest.requests++;
            return super.postMessage(...args);
          }
          set onmessage(listener) {
            super.onmessage = event => {
              const deliver = () => listener.call(this, event);
              if (window.guavajamSearchTest.hold && event.data.rows) window.guavajamSearchTest.replies.push(deliver);
              else deliver();
            };
          }
        };
      });
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
        const beforeDownload = await searchFeedbackGeometry(waiting, true);
        await waiting.locator('#guavajam-search-toggle').click();
        await Promise.race([signalled, new Promise((_, reject) =>
          setTimeout(() => reject(new Error('Expected a deferred SQLite download')), 15000))]);
        assert.equal(await waiting.locator('#guavajam-search-spinner').isVisible(), true);
        assert.deepEqual(await searchFeedbackGeometry(waiting, true), beforeDownload,
          'the download spinner occupies the reserved heading slot');
        assert.equal(await waiting.locator('#guavajam-search-status').textContent(), `${fixture.works} total`);
        assert.equal(await waiting.locator('#guavajam-search-progress-status').textContent(), 'Loading local search…');
        await waiting.locator('#guavajam-static-list h3 a').first().focus();
      } finally {
        releaseDatabase();
      }
      await waiting.waitForFunction(total => !document.querySelector('#guavajam-search-results').hidden && !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') && document.querySelector('#guavajam-search-status').textContent ===
        `${total} total`, fixture.works);
      assert.equal(await waiting.locator('#guavajam-search-spinner').isVisible(), false);
      assert.equal(await waiting.locator(':focus').getAttribute('id'), 'guavajam-search-results',
        'hiding the static list hands its focused link to the new results after the search controls');
      await waiting.evaluate(() => { window.guavajamSearchTest.hold = true; });
      await waiting.locator('#guavajam-search-next').focus();
      const beforeQuery = await searchFeedbackGeometry(waiting);
      const beforeRows = await waiting.locator('#guavajam-search-results').innerHTML();
      await waiting.keyboard.press('Enter');
      await waiting.waitForFunction(() => window.guavajamSearchTest.replies.length === 1);
      assert.equal(await waiting.locator(':focus').getAttribute('id'), 'guavajam-search-next',
        'a pending page change keeps its initiating button focused');
      assert.equal(await waiting.locator('#guavajam-search-next').getAttribute('aria-disabled'), 'true');
      assert.equal(await waiting.locator('#guavajam-search-spinner').isVisible(), true);
      assert.deepEqual(await searchFeedbackGeometry(waiting), beforeQuery,
        'query progress occupies the same reserved heading slot');
      assert.equal(await waiting.locator('#guavajam-search-results').innerHTML(), beforeRows,
        'pending work retains the visible result rows');
      const pendingRequests = await waiting.evaluate(() => window.guavajamSearchTest.requests);
      await waiting.keyboard.press('Enter');
      assert.equal(await waiting.evaluate(() => window.guavajamSearchTest.requests), pendingRequests,
        'a busy pager cannot submit another page change');
      await waiting.locator('#guavajam-search-toggle').focus();
      await waiting.keyboard.press('Tab');
      assert.equal(await waiting.locator('input[name=q]').evaluate(n => n === document.activeElement), true);
      await waiting.evaluate(() => {
        window.guavajamSearchTest.focus = document.activeElement;
        window.guavajamSearchTest.hold = false;
        window.guavajamSearchTest.replies.shift()();
      });
      await waiting.waitForFunction(() => document.querySelector('#guavajam-search-page').textContent === 'Page 2 of 2');
      assert.equal(await waiting.evaluate(() => document.activeElement === window.guavajamSearchTest.focus), true,
        'a delayed reply never steals focus after the reader tabs back into the query');
      await waiting.locator('input[name=q]').fill('Glaciolacustrine');
      await waiting.waitForFunction(() => document.querySelector('#guavajam-search-status').textContent === '1 total');
      assert.equal(await waiting.locator('input[name=q]').evaluate(n => n === document.activeElement), true,
        'live search does not move focus while the reader is typing');
      await delayed.close();

      const disabled = await browser.newContext({javaScriptEnabled: false});
      const plain = await disabled.newPage();
      await plain.goto(base);
      assert.equal(await plain.locator('#guavajam-static-list > li').count(), 20);
      assert.equal(await plain.locator('#guavajam-static-list button.guavajam-facet-type').count(), 0);
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
      assert.equal(await plain.locator('h1').count(), 1);
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
        await fallback.locator('#guavajam-search-toggle').click();
        await fallback.waitForFunction(() => document.querySelector('#guavajam-search-message').textContent.includes('unavailable'));
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
      await stale.locator('#guavajam-search-toggle').click();
      await stale.waitForFunction(() => document.querySelector('#guavajam-search-message').textContent.includes('unavailable'));
      assert.equal(await stale.locator('#guavajam-static-list').isVisible(), true);
      await Promise.all([stale.waitForNavigation(), stale.locator('#guavajam-search-reload').click()]);
      assert.equal(await stale.locator('#guavajam-search').getAttribute('data-database'), currentDatabase);
      if (!await stale.locator('#guavajam-index-search').evaluate(n => !n.hidden))
        await stale.locator('#guavajam-search-toggle').click();
      await stale.waitForFunction(total => !document.querySelector('#guavajam-search-results').hidden && !document.querySelector('#guavajam-search-results').hasAttribute('aria-busy') && document.querySelector('#guavajam-search-status').textContent === `${total} total`, fixture.works);
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
