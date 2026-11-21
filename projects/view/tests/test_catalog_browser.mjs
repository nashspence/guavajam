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
const pages = ['index.html', 'editions/index.html', edition + 'locators.html', edition + 'locators/page/2.html', edition + 'verify.html', edition + 'read/index.html',
  edition + 'read/documents.html', edition + 'read/supporting/note.html',
  edition + 'read/cite.html', edition + 'read/cite/page/3.html',
  edition + 'read/supporting/note.cite.html', edition + 'read/supporting/note.cite/page/3.html',
  first + 'verify.html', first + 'read/index.html', first + 'read/cite.html'];
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
      context.on('request', request => { if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin) unexpected.push(request.url()); });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      await page.goto(base);
      const bibliographic = await page.locator('main .guavajam-work-citation').textContent();
      assert(bibliographic.includes('Example Author'));
      assert(bibliographic.includes('Field Notes'));
      assert(!bibliographic.includes(revision) && !bibliographic.includes('Git commit'));
      assert.deepEqual(await page.locator('nav[aria-label="Work links"] a').allTextContents(), ['Latest', 'History']);
      assert(await page.locator('nav[aria-label="Work links"]').evaluate(node =>
        Boolean(node.compareDocumentPosition(document.querySelector('.guavajam-work-citation')) & Node.DOCUMENT_POSITION_FOLLOWING)));
      await page.keyboard.press('Tab');
      assert.equal(await page.locator(':focus').textContent(), 'Skip to content');
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(':focus').evaluate(n => n.tagName), 'MAIN');
      await page.getByRole('link', {name: 'History', exact: true}).click();
      assert(page.url().endsWith('/editions/index.html'));
      assert.equal(await page.getByRole('link', {name: 'v1', exact: true}).count(), 1);
      await page.goto(base);
      await page.getByRole('link', {name: 'History', exact: true}).click();
      await page.getByRole('link', {name: 'Verify', exact: true}).first().click();
      assert(page.url().endsWith('/verify.html'));
      assert.equal(await page.locator('main .guavajam-verification').count(), 1);
      assert((await page.locator('main .guavajam-verification').textContent()).includes('Verification record (JSON)'));
      assert.notEqual((await page.locator('main .guavajam-verification code').first().textContent()).length, 0);
      await page.getByRole('link', {name: 'v2', exact: true}).click();
      assert(page.url().endsWith('/read/index.html'));
      assert.equal(await page.locator('.guavajam-version-menu').count(), 1);
      assert(await page.locator('.guavajam-version-menu').evaluate(node =>
        Boolean(node.compareDocumentPosition(document.querySelector('main .guavajam-citation')) & Node.DOCUMENT_POSITION_FOLLOWING)));
      assert.deepEqual(await page.locator('.guavajam-version-menu a').allTextContents(),
        ['Search', 'Documents', 'Passages', 'Locators', 'Verify', 'License', 'Download']);
      await page.getByRole('link', {name: 'Locators', exact: true}).click();
      assert(page.url().endsWith('/locators.html'));
      assert.equal(await page.locator('.guavajam-navigation h1').textContent(), 'Locators');
      assert.equal(await page.locator('script').count(), 0);
      const locatorTitles = [];
      const firstLocator = page.locator('.guavajam-locator-list h3').first();
      await firstLocator.focus();
      assert.equal(await firstLocator.evaluate(node => node.scrollWidth > node.clientWidth), true);
      await firstLocator.press('ArrowRight');
      await page.waitForTimeout(200);
      assert.equal(await firstLocator.evaluate(node => node.scrollLeft > 0), true,
        'long static locator labels remain keyboard-scrollable');
      assert((await page.locator('.guavajam-locator-detail').first().textContent()).includes('Example authority'));
      for (const count of [20, 3]) {
        const rows = page.locator('.guavajam-locator-list > li');
        assert.equal(await rows.count(), count);
        locatorTitles.push(...await rows.locator('h3').allTextContents());
        if (count === 20) await page.locator('.guavajam-static-pages a[rel=next]').click();
      }
      assert.equal(new Set(locatorTitles).size, 23);
      await page.locator('.guavajam-static-pages a[rel=prev]').click();
      assert(page.url().endsWith('/locators.html'));
      await page.getByRole('link', {name: 'v2', exact: true}).click();
      assert.equal(await page.getByRole('link', {name: 'Cite this document', exact: true}).count(), 0);
      assert.equal(await page.getByRole('link', {name: 'All documents in this edition', exact: true}).count(), 0);
      await page.getByRole('link', {name: 'License', exact: true}).click();
      assert(page.url().endsWith('/license.html'));
      assert((await page.locator('main').textContent()).includes('CC-BY-4.0'));
      await page.getByRole('link', {name: 'v2', exact: true}).click();
      const footer = await page.locator('footer').textContent();
      assert(footer.includes('Generated by GuavaJam View'));
      assert(!footer.includes('Git revision is') && !footer.includes('Rights:'));
      const downloaded = page.waitForEvent('download');
      await page.getByRole('link', {name: 'Download', exact: true}).click();
      const download = await downloaded;
      assert.equal(download.suggestedFilename(), 'source.gfm');
      assert.deepEqual(await fs.readFile(await download.path()), await fs.readFile(path.join(root, edition, 'read/source.gfm')));
      await page.getByRole('link', {name: 'Passages', exact: true}).click();
      assert(page.url().endsWith('/read/cite.html'), 'Passages opens a human citation page');
      assert((await page.locator('main').textContent()).includes(revision));
      assert.equal(await page.locator('.guavajam-navigation h1').textContent(), 'Passages');
      assert.equal(await page.locator('#guavajam-works-heading').textContent(), 'Passages');
      assert.equal(await page.getByRole('link', {name: 'Return to reading', exact: true}).count(), 0);
      assert.equal(await page.getByRole('link', {name: 'Download exact GFM source', exact: true}).count(), 0);
      const staticAnchors = [];
      for (const [index, expected] of [20, 20, 5].entries()) {
        const entries = page.locator('#guavajam-static-section .guavajam-citation-entry');
        assert.equal(await entries.count(), expected, `citation page ${index + 1} is bounded`);
        staticAnchors.push(...await entries.evaluateAll(nodes => nodes.map(node => node.id.slice('citation:'.length))));
        assert.equal(await page.locator('.guavajam-static-pages').first().getByText(`Page ${index + 1} of 3`, {exact: true}).count(), 1);
        if (index < 2) await page.locator('.guavajam-static-pages a[rel=next]').first().click();
      }
      assert.deepEqual(staticAnchors, fixture.citationAnchors, 'ordinary page links cover every anchor once in source order');
      assert.equal(await page.locator('.guavajam-static-pages a[rel=next]').count(), 0);
      await page.locator('.guavajam-static-pages a[rel=prev]').first().click();
      assert(page.url().endsWith('/read/cite/page/2.html'));

      // Every boundary link must work directly without fetching or filtering
      // earlier citation pages. Source names may coincide with UI shell IDs.
      for (const position of [19, 20, 39, 40, 41, 42, 44]) {
        const anchor = fixture.citationAnchors[position];
        await page.goto(base + edition + 'read/index.html#' + anchor);
        await page.getByRole('link', {name: 'Cite committed source passage ' + anchor, exact: true}).click();
        const number = Math.floor(position / 20) + 1;
        const relative = 'read/' + (number === 1 ? 'cite.html' : `cite/page/${number}.html`);
        assert.equal(new URL(page.url()).pathname, new URL(base + edition + relative).pathname);
        assert.equal(decodeURIComponent(new URL(page.url()).hash), '#citation:' + anchor);
        const entry = page.locator('[id="citation:' + anchor + '"]');
        assert.equal(await entry.count(), 1);
        const citationText = await entry.locator('blockquote').textContent();
        for (const identity of [revision, 'content/manuscript.md', '#' + anchor]) assert(citationText.includes(identity));
        const jsonDownloaded = page.waitForEvent('download');
        await entry.getByRole('link', {name: 'Download official citation JSON', exact: true}).click();
        const citationDownload = await jsonDownloaded;
        const canonical = JSON.parse(await fs.readFile(await citationDownload.path(), 'utf8'));
        assert.equal(canonical.revision, revision); assert.equal(canonical.anchor, anchor);
        assert.equal(canonical.manuscript, 'content/manuscript.md'); assert(!Object.hasOwn(canonical, 'url'));
        await entry.getByRole('link', {name: 'Read this passage', exact: true}).click();
        assert.equal(page.url(), base + edition + 'read/index.html#' + anchor);
        assert.equal(await page.locator('[id="' + anchor + '"]').count(), 1);
        const permalink = page.locator('[id="' + anchor + '"] .guavajam-permalink').first();
        if (await permalink.count()) {
          await permalink.hover();
          const colors = await permalink.evaluate(node => ({
            target: getComputedStyle(node.closest('[id]')).backgroundColor,
            link: getComputedStyle(node).backgroundColor
          }));
          assert.notEqual(colors.target, 'rgba(0, 0, 0, 0)', 'passage itself is highlighted');
          assert(['rgba(0, 0, 0, 0)', 'transparent'].includes(colors.link), 'permalink does not paint a separate rectangle');
        }
        assert.equal(await page.locator('script').count(), 0);
      }
      await page.goto(base + edition + 'verify.html');
      await page.getByRole('link', {name: 'v2', exact: true}).click();
      assert(page.url().endsWith('/read/index.html'));
      await page.getByRole('link', {name: 'the supporting claim', exact: true}).click();
      assert(page.url().endsWith('/supporting/note.html#claim'));
      assert((await page.locator('#claim').textContent()).includes('exact independently citable'));
      await page.getByRole('link', {name: 'Cite committed source passage supporting-tail', exact: true}).click();
      assert(page.url().endsWith('/supporting/note.cite/page/3.html#citation:supporting-tail'));
      const supportingEntry = page.locator('[id="citation:supporting-tail"]');
      assert((await supportingEntry.textContent()).includes('content/supporting/note.md'));
      assert((await supportingEntry.textContent()).includes('Supporting note'));
      assert.equal(await page.locator('.guavajam-citation-entry').count(), 5);
      const supportDownloaded = page.waitForEvent('download');
      await supportingEntry.getByRole('link', {name: 'Download official citation JSON', exact: true}).click();
      const supportDownload = await supportDownloaded;
      const supportCanonical = JSON.parse(await fs.readFile(await supportDownload.path(), 'utf8'));
      assert.equal(supportCanonical.revision, revision);
      assert.equal(supportCanonical.document, 'content/supporting/note.md');
      assert.equal(supportCanonical.anchor, 'supporting-tail');
      await supportingEntry.getByRole('link', {name: 'Read this passage', exact: true}).click();
      assert(page.url().endsWith('/supporting/note.html#supporting-tail'));
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
          assert.equal(shape.scripts, 0); assert.equal(shape.main, 1); assert.equal(shape.h1, filename.endsWith('/read/index.html') ? 0 : 1);
          assert(shape.width <= shape.viewport + 1, `${name} ${filename} ${width} enlarged=${enlarge} overflow`);
          assert.equal(shape.ids.length, new Set(shape.ids).size);
          assert(shape.csp.includes("script-src 'none'"));
          const breadcrumb = await page.locator('.guavajam-navigation').evaluate(node => ({
            scrollbar: getComputedStyle(node).scrollbarWidth,
            scrollable: getComputedStyle(node).overflowX
          }));
          assert.equal(breadcrumb.scrollbar, 'none');
          assert.equal(breadcrumb.scrollable, 'auto');
          results.push({engine: name, filename, width, scheme, enlarge, overflow: false});
        }
      }
      await page.goto(base + first + 'read/index.html');
      assert.equal(await page.getByRole('link', {name: 'Verify', exact: true}).count(), 1);
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
      console.log(name + ': edition history, bounded source citations, direct passage round trips, downloads, reflow, print and accessibility passed');
    } finally { await browser.close(); }
  }
} finally { await new Promise(resolve => server.close(resolve)); }
await fs.writeFile(path.join(output, 'catalog-browser-report.json'), JSON.stringify({checks: results}, null, 2) + '\n');
console.log(`${results.length} script-disabled presentation checks passed.`);
