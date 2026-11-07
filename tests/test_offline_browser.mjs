import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import {chromium, firefox, webkit} from 'playwright';

const root = process.env.PRESS_OFFLINE_TEST_DIR;
assert.ok(root, 'Run make test-browser to build and verify the current complete ZIP');
const manifest = JSON.parse(await readFile(path.join(root, 'provenance.json'), 'utf8'));
const asset = manifest.assets.find(a => a.role === 'download');
const key = new URL(asset.source_url).pathname.slice(1);
const expected = await readFile(path.join(root, key));
assert.match(expected.toString(), /<script>.*alert/);

for (const [name, engine] of Object.entries({chromium, firefox, webkit})) {
  test(`${name}: offline contents, keyboard focus, section targets and print remain usable`, {timeout: 120000}, async () => {
    const browser = await engine.launch({headless: true});
    try {
      // 640/320px also exercise reflow equivalent to a 1280px desktop at 2x/4x zoom.
      for (const width of [320, 375, 640, 768, 1280]) {
        for (const colorScheme of ['light', 'dark']) {
          const context = await browser.newContext({offline: true,
            viewport: {width, height: 900}, colorScheme});
          try {
            const page = await context.newPage();
            const network = [];
            page.on('request', request => { if (/^https?:/.test(request.url())) network.push(request.url()); });
            const url = pathToFileURL(path.join(root, 'index.html')).href;
            await page.goto(url);
            const contents = page.getByRole('navigation', {name: 'Contents', exact: true});
            assert.equal(await contents.count(), 1);
            assert.deepEqual(await contents.locator('a').evaluateAll(links => links.map(a => a.getAttribute('href'))),
              ['#sec-1', '#sec-2', '#sec-3', '#record-md-4-note']);
            assert.equal(await contents.locator('math').count(), 0);
            assert.equal(await contents.locator('a').nth(1).textContent(), 'Comparison of x=x');
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);

            await page.keyboard.press('Tab');
            assert.equal(await page.evaluate(() => document.activeElement.className), 'press-skip');
            assert.ok((await page.locator('.press-skip').boundingBox()).y >= 0);
            await page.keyboard.press('Enter');
            assert.equal(await page.evaluate(() => document.activeElement.id), 'press-main');
            await page.locator('.press-contents-link').click();
            await contents.locator('a[href="#sec-3"]').click();
            await page.waitForFunction(() => location.hash === '#sec-3');
            const clearOfHeader = () => page.evaluate(() =>
              document.querySelector(location.hash).getBoundingClientRect().top >=
              document.querySelector('.press-status').getBoundingClientRect().bottom - 1);
            assert.equal(await clearOfHeader(), true, `${name} ${width} ${colorScheme}: target obscured`);
            await page.goBack();
            await page.waitForFunction(() => location.hash === '#press-contents');

            await page.goto(url + '#sec-2');
            assert.equal(await clearOfHeader(), true, `${name} ${width} ${colorScheme}: direct fragment obscured`);
            const targetCue = await page.locator('#sec-2').evaluate(element => {
              const style = getComputedStyle(element);
              return {shadow: style.boxShadow, background: style.backgroundColor};
            });
            assert.notEqual(targetCue.shadow, 'none', `${name}: deep-link rule missing`);
            assert.notEqual(targetCue.background, 'rgba(0, 0, 0, 0)', `${name}: target tint missing`);
            assert.equal(await page.locator('#sec-2 a.press-permalink').getAttribute('href'),
              manifest.record_url + '/read#sec-2');
            {
              // Enlarged text and wider letter spacing stress status-bar wrapping.
              await page.evaluate(() => {
                const root = document.documentElement;
                root.style.fontSize = parseFloat(getComputedStyle(root).fontSize) * 2 + 'px';
                // WCAG 1.4.12 stress values plus enlarged text: reflow without
                // clipping or hiding the linked heading under the status bar.
                root.style.letterSpacing = '0.12em';
                root.style.wordSpacing = '0.16em';
                for (const paragraph of document.querySelectorAll('p')) {
                  paragraph.style.marginBottom = '2em';
                }
                document.querySelector('#sec-2').scrollIntoView();
              });
              assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
              const geometry = await page.evaluate(() => ({
                target: document.querySelector('#sec-2').getBoundingClientRect().top,
                header: document.querySelector('.press-status').getBoundingClientRect().bottom,
              }));
              assert.ok(geometry.target >= geometry.header - 1,
                `${name} ${width}: enlarged-text target obscured: ${JSON.stringify(geometry)}`);
            }

            await page.emulateMedia({media: 'print', colorScheme: 'dark'});
            for (const selector of ['.press-skip', '.press-contents-link', '.press-contents']) {
              assert.equal(await page.locator(selector).isVisible(), false, `${selector} remains visible in print`);
            }
            assert.deepEqual(await page.evaluate(() => {
              const style = getComputedStyle(document.body);
              return [style.backgroundColor, style.color];
            }), ['rgb(255, 255, 255)', 'rgb(0, 0, 0)']);
            assert.equal(await page.locator('footer').isVisible(), true);
            const printRules = await page.evaluate(() => {
              const target = getComputedStyle(document.querySelector('#sec-2'));
              const paragraph = getComputedStyle(document.querySelector('.press-edition p'));
              return {shadow: target.boxShadow, background: target.backgroundColor,
                supportsWidows: CSS.supports('widows', '3') && CSS.supports('orphans', '3'),
                orphans: paragraph.orphans, widows: paragraph.widows};
            });
            assert.equal(printRules.shadow, 'none', `${name}: print should not mark fragment targets`);
            if (printRules.supportsWidows) {
              assert.equal(printRules.orphans, '3');
              assert.equal(printRules.widows, '3');
            }
            assert.deepEqual(network, []);
          } finally { await context.close(); }
        }
      }
    } finally { await browser.close(); }
  });

  test(`${name}: opaque downloads from the complete ZIP remain exact and inert offline`, {timeout: 45000}, async () => {
    const browser = await engine.launch({headless: true});
    try {
      const context = await browser.newContext({acceptDownloads: true, offline: true,
        viewport: {width: 375, height: 900}, colorScheme: 'dark'});
      const page = await context.newPage();
      const dialogs = [], network = [];
      page.on('dialog', dialog => { dialogs.push(dialog.type()); void dialog.dismiss(); });
      page.on('request', request => { if (/^https?:/.test(request.url())) network.push(request.url()); });
      await page.goto(pathToFileURL(path.join(root, 'index.html')).href);
      const originalURL = page.url();
      const [download] = await Promise.all([page.waitForEvent('download'),page.locator('a[download]').first().click()]);
      const actual = await readFile(await download.path());
      assert.deepEqual(actual, expected);
      assert.equal(actual.length, asset.byte_count);
      assert.equal(createHash('sha256').update(actual).digest('hex'), asset.sha256);
      assert.equal(download.suggestedFilename(), path.basename(key));
      assert.equal(page.url(), originalURL);
      assert.deepEqual(dialogs, []);
      assert.deepEqual(network, []);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    } finally { await browser.close(); }
  });
}

// Chromium's high-contrast emulation is a focused system-colors regression.
// This is not a substitute for an actual Windows high-contrast review.
test('chromium: forced colors preserve a discoverable fragment and permalink', {timeout: 45000}, async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({offline: true, forcedColors: 'active',
      viewport: {width: 375, height: 900}});
    try {
      const page = await context.newPage();
      await page.goto(pathToFileURL(path.join(root, 'index.html')).href + '#sec-2');
      assert.equal(await page.evaluate(() => matchMedia('(forced-colors: active)').matches), true);
      const target = await page.locator('#sec-2').evaluate(element => {
        const style = getComputedStyle(element);
        return {outline: style.outlineStyle, width: parseFloat(style.outlineWidth),
          shadow: style.boxShadow};
      });
      assert.equal(target.outline, 'solid');
      assert.ok(target.width >= 2, `forced-colors fragment outline missing: ${JSON.stringify(target)}`);
      assert.equal(target.shadow, 'none');
      const permalink = page.locator('#sec-2 a.press-permalink');
      await permalink.hover();
      assert.equal(await permalink.evaluate(el => getComputedStyle(el).outlineStyle), 'solid');
      await permalink.focus();
      assert.ok(await permalink.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth)) >= 2);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await page.emulateMedia({media: 'print'});
      assert.equal(await page.locator('#sec-2').evaluate(el => getComputedStyle(el).outlineStyle), 'none');
    } finally { await context.close(); }
  } finally { await browser.close(); }
});
