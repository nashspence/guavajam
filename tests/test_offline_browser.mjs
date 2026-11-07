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
      for (const width of [320, 375, 768, 1280]) {
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
            assert.equal(await page.locator('#sec-2 a.press-permalink').getAttribute('href'),
              manifest.record_url + '/read#sec-2');
            if (width === 320) {
              // Narrow-screen reflow plus enlarged text protects zoomed reading.
              await page.evaluate(() => {
                const root = document.documentElement;
                root.style.fontSize = parseFloat(getComputedStyle(root).fontSize) * 2 + 'px';
                document.querySelector('#sec-2').scrollIntoView();
              });
              assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
              assert.equal(await clearOfHeader(), true, `${name}: enlarged-text target obscured`);
            }

            await page.emulateMedia({media: 'print', colorScheme: 'dark'});
            for (const selector of ['.press-skip', '.press-contents-link', '.press-contents']) {
              assert.equal(await page.locator(selector).isVisible(), false);
            }
            assert.deepEqual(await page.evaluate(() => {
              const style = getComputedStyle(document.body);
              return [style.backgroundColor, style.color];
            }), ['rgb(255, 255, 255)', 'rgb(0, 0, 0)']);
            assert.equal(await page.locator('footer').isVisible(), true);
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
