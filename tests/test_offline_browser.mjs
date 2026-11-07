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
