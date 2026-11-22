// Script-free edition citation and reading journeys in all three pinned engines.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';
import axe from 'axe-core';
import {keyboardControls, hoverControls} from './interaction_contract.mjs';

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
      '.gfm': 'text/plain; charset=utf-8', '.xml': 'application/xml'};
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
const passages = position => 'read/' + (position < 20 ? 'passages.html' : `passages/page/${Math.floor(position / 20) + 1}.html`);
const fragment = (source, anchor) => 'citation:' + createHash('sha256').update(source).digest('hex').slice(0, 20) + ':' + anchor;
const pages = ['index.html', edition + 'metadata.html', edition + 'read/index.html',
  edition + 'read/contents.html', edition + 'read/documents.html',
  edition + 'read/supporting/note.html', edition + passages(0), edition + passages(40),
  edition + passages(80), first + 'read/index.html'];
const engines = {chromium, firefox, webkit};
const selected = (process.env.GUAVAJAM_BROWSER_ENGINES || 'chromium,firefox,webkit').split(',');
assert(selected.length && selected.every(name => Object.hasOwn(engines, name)), 'Unknown browser engine');
const results = [];
try {
  for (const name of selected) {
    const executablePath = name === 'chromium' ? process.env.GUAVAJAM_CHROMIUM_EXECUTABLE : undefined;
    const browser = await engines[name].launch(executablePath ? {executablePath} : {});
    try {
      const context = await browser.newContext({javaScriptEnabled: false, acceptDownloads: true,
        viewport: {width: 360, height: 900}, reducedMotion: 'reduce'});
      const unexpected = [];
      context.on('request', request => {
        if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin)
          unexpected.push(request.url());
      });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      await page.goto(base);
      assert.equal(await page.locator('.guavajam-navigation h1').textContent(), 'Field Notes, Revised');
      assert.equal(await page.getByRole('heading', {name: 'Versions', exact: true}).count(), 1);
      assert.deepEqual(await page.locator('.guavajam-editions h3 a').allTextContents(), ['v2', 'v1']);
      await page.keyboard.press('Tab');
      assert.equal(await page.locator(':focus').textContent(), 'Skip to content');
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(':focus').evaluate(n => n.tagName), 'MAIN');
      await page.getByRole('link', {name: 'v2', exact: true}).click();
      assert(page.url().endsWith('/read/index.html'));
      assert.equal(await page.locator('.guavajam-version-menu').count(), 1);
      assert(await page.locator('.guavajam-version-menu').evaluate(node =>
        Boolean(node.compareDocumentPosition(document.querySelector('main .guavajam-citation')) & Node.DOCUMENT_POSITION_FOLLOWING)));
      assert.deepEqual(await page.locator('.guavajam-version-menu a').allTextContents(),
        ['Contents', 'Documents', 'Passages', 'Metadata', 'Download']);
      for (const width of [320, 360, 390, 430, 500]) {
        await page.setViewportSize({width, height: 900});
        await page.goto(base + edition + 'read/index.html');
        const menu = page.getByRole('navigation', {name: 'Version links', exact: true});
        assert(await menu.evaluate(n => n.scrollWidth > n.clientWidth), 'mobile version links should overflow within their own lane');
        const rows = await menu.locator('a').evaluateAll(ns => ns.map(n => n.getBoundingClientRect().top));
        assert(rows.every(top => Math.abs(top - rows[0]) < 1), 'version links remain on one row');
        await menu.focus();
        const beforePan = await menu.evaluate(n => n.scrollLeft);
        await page.keyboard.press('ArrowRight');
        await page.waitForTimeout(250); // Native scroll animation; this context has scripts disabled.
        assert(await menu.evaluate(n => n.scrollLeft) > beforePan, 'arrow keys pan the version menu');
        for (const link of await menu.locator('a').all()) {
          await page.keyboard.press('Tab');
          await page.waitForTimeout(250);
          assert(await link.evaluate(n => n === document.activeElement), 'Tab visits every version link in order');
          assert(await menu.locator('a').evaluateAll(ns => ns.every(n => Math.abs(n.getBoundingClientRect().top - ns[0].getBoundingClientRect().top) < 1)), 'focused links stay in one row');
          assert(await link.evaluate(n => {
            const box=n.getBoundingClientRect(), lane=n.closest('nav').getBoundingClientRect();
            return box.left >= lane.left + 5 && box.right <= lane.right - 5;
          }), 'keyboard focus exposes the complete link and its focus gutter');
        }
        // Switching from the final keyboard-focused control to an earlier
        // pointer target must not trap scrolling at the focused item.
        await page.getByRole('link', {name: 'Contents', exact: true}).click();
        await page.waitForURL('**/read/contents.html');
        assert(page.url().endsWith('/read/contents.html'));
      }
      await page.setViewportSize({width: 360, height: 900});
      await page.goto(base + edition + 'read/index.html');
      assert.equal(await page.locator('nav.guavajam-contents').count(), 0,
        'Contents has moved off the manuscript without changing the standalone renderer');
      const downloadedSource = page.waitForEvent('download');
      await page.getByRole('link', {name: 'Download', exact: true}).click();
      const source = await downloadedSource;
      assert.equal(source.suggestedFilename(), 'source.gfm');
      assert.deepEqual(await fs.readFile(await source.path()), await fs.readFile(path.join(root, edition, 'read/source.gfm')));
      await page.getByRole('link', {name: 'Metadata', exact: true}).click();
      assert((await page.locator('main').textContent()).includes('CC-BY-4.0'));
      for (const branch of ['Publication', 'Provenance', 'Rights', 'Locators']) {
        assert.equal(await page.getByRole('heading', {name: branch, exact: true}).count(), 1);
      }
      await page.getByRole('link', {name: 'v2', exact: true}).click();
      await page.getByRole('link', {name: 'Contents', exact: true}).click();
      assert(page.url().endsWith('/read/contents.html'));
      assert.equal(await page.locator('.guavajam-contents-list a[href="index.html#opening"]').count(), 1);
      await page.locator('.guavajam-contents-list a[href="index.html#opening"]').click();
      assert(page.url().endsWith('/read/index.html#opening'));
      await page.getByRole('link', {name: 'Passages', exact: true}).click();
      assert(page.url().endsWith('/read/passages.html'));
      const staticFragments = [];
      for (const [number, expected] of [20, 20, 20, 20, 9].entries()) {
        const entries = page.locator('#guavajam-static-section .guavajam-citation-entry');
        assert.equal(await entries.count(), expected);
        staticFragments.push(...await entries.evaluateAll(nodes => nodes.map(node => node.id)));
        if (number < 4) await page.locator('.guavajam-static-pages a[rel=next]').first().click();
      }
      assert.equal(staticFragments.length, 89, 'manuscript and supporting passages share one bounded index');
      assert.equal(new Set(staticFragments).size, 89);
      assert.deepEqual(staticFragments.slice(0, fixture.passages),
        fixture.citationAnchors.map(anchor => fragment('content/manuscript.md', anchor)));
      assert.equal(await page.locator('.guavajam-static-pages a[rel=next]').count(), 0);
      for (const position of [0, 19, 20, 39, 40, 41, 42, 44]) {
        const anchor = fixture.citationAnchors[position];
        await page.goto(base + edition + 'read/index.html#' + anchor);
        const citeLink = page.getByRole('link', {name: 'Cite committed source passage ' + anchor, exact: true});
        await citeLink.focus(); await citeLink.press('Enter');
        const destination = base + edition + passages(position) + '#' + fragment('content/manuscript.md', anchor);
        assert.equal(page.url(), destination);
        const entry = page.locator('[id="' + fragment('content/manuscript.md', anchor) + '"]');
        assert.equal(await entry.count(), 1);
        const citation = await entry.locator('.guavajam-citation').textContent();
        for (const part of [revision, 'content/manuscript.md', '#' + anchor]) assert(citation.includes(part));
        const downloaded = page.waitForEvent('download');
        const downloadLink = entry.getByRole('link', {name: 'Download official citation JSON'});
        await downloadLink.focus(); await downloadLink.press('Enter');
        const file = await downloaded;
        const canonical = JSON.parse(await fs.readFile(await file.path(), 'utf8'));
        assert.equal(canonical.revision, revision);
        assert.equal(canonical.anchor, anchor);
        assert(!Object.hasOwn(canonical, 'url'));
        await entry.getByRole('link', {name: 'Read this passage'}).click();
        assert.equal(page.url(), base + edition + 'read/index.html#' + anchor);
      }
      await page.goto(base + edition + 'read/supporting/note.html');
      assert.equal(await page.locator('h1').count(), 1);
      assert.equal(await page.locator('.guavajam-citation h1:not([id])').count(), 1);
      assert.equal(await page.locator('.guavajam-citation a').count(), 1);
      assert.equal(await page.locator('.guavajam-citation a[download]').count(), 1);
      assert.equal(await page.getByRole('group', {name: 'Abstract', exact: true}).locator('p').last().textContent(), 'Independently reviewed evidence for the manuscript.');
      assert.equal(await page.locator('.guavajam-citation h1 cite').textContent(), 'Supporting note');
      assert.equal(await page.locator('.guavajam-navigation [aria-current=page]').textContent(), 'Supporting note');
      assert.equal(await page.getByRole('link', {name: 'Cite this document'}).count(), 0);
      await page.getByRole('link', {name: 'Cite committed source passage supporting-tail'}).click();
      assert(page.url().endsWith('/read/passages/page/5.html#' + fragment('content/supporting/note.md', 'supporting-tail')));
      const support = page.locator('[id="' + fragment('content/supporting/note.md', 'supporting-tail') + '"]');
      const cited = (await support.locator('.guavajam-citation').textContent());
      assert(cited.includes('content/supporting/note.md'));
      await support.getByRole('link', {name: 'Read this passage'}).click();
      assert(page.url().endsWith('/read/supporting/note.html#supporting-tail'));
      await page.goto(base + edition + 'metadata.html');
      assert.equal(await page.locator('.guavajam-metadata-locators > li').count() > 0, true);
      assert.equal(await page.locator('.guavajam-static-pages').count(), 0);
      assert.equal(await page.locator('.guavajam-metadata-locators > li').count(), 23);
      assert((await page.locator('.guavajam-metadata-leaf').first().textContent()).includes('Example authority'));
      assert.equal(await page.locator('script').count(), 0);
      for (const old of ['locators.html', 'verify.html', 'license.html']) {
        assert.equal((await page.request.get(base + edition + old)).status(), 404);
      }
      for (const filename of pages) {
        for (const [width, scheme, enlarge] of [[320, 'light', false], [1280, 'dark', false], [360, 'light', true]]) {
          await page.setViewportSize({width, height: 900});
          await page.emulateMedia({media: 'screen', colorScheme: scheme});
          await page.goto(base + filename);
          if (enlarge) await page.evaluate(() => {
            document.documentElement.style.fontSize = '34px';
            document.documentElement.style.lineHeight = '1.8';
          });
          const shape = await page.evaluate(() => ({scripts: document.scripts.length,
            main: document.querySelectorAll('main').length,
            csp: document.querySelector('meta[http-equiv="Content-Security-Policy"]').content,
            width: document.documentElement.scrollWidth, viewport: innerWidth,
            ids: [...document.querySelectorAll('[id]')].map(n => n.id)}));
          assert.equal(shape.scripts, 0); assert.equal(shape.main, 1);
          assert(shape.csp.includes("script-src 'none'"));
          assert(shape.width <= shape.viewport + 1, `${name} ${filename} ${width} overflow`);
          assert.equal(shape.ids.length, new Set(shape.ids).size, `duplicated HTML IDs ${filename}`);
          results.push({engine: name, filename, width, scheme, enlarge, overflow: false});
        }
      }
      await page.goto(base + first + 'read/index.html');
      assert((await page.locator('main').textContent()).includes('The first observation.'));
      await page.emulateMedia({media: 'print', colorScheme: 'light'});
      assert.equal(await page.locator('main').isVisible(), true);
      assert.deepEqual(unexpected, []);
      await context.close();
      const auditContext = await browser.newContext({bypassCSP: false, viewport: {width: 360, height: 900}});
      const review = await auditContext.newPage();
      for (const filename of pages) {
        await review.goto(base + filename);
        try { await keyboardControls(review); } catch (error) { error.message = `${name} ${filename}: ${error.message}`; throw error; }
        await hoverControls(review);
        await review.evaluate(axe.source);
        const audit = await review.evaluate(() => axe.run(document, {
          runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}
        }));
        assert.deepEqual(audit.violations.map(v => ({id: v.id, targets: v.nodes.map(n => n.target)})), [],
          `${name}: accessibility ${filename}`);
      }
      await review.emulateMedia({forcedColors: 'active'});
      await keyboardControls(review); await hoverControls(review);
      await review.emulateMedia({forcedColors: 'none'});
      await review.screenshot({path: path.join(output, name + '-catalog.png'), fullPage: true});
      await auditContext.close();
      console.log(name + ': combined static passage pages, exact citations, contents, locators and accessibility passed');
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
await fs.writeFile(path.join(output, 'catalog-browser-report.json'), JSON.stringify({checks: results}, null, 2) + '\n');
console.log(`${results.length} script-disabled presentation checks passed.`);
