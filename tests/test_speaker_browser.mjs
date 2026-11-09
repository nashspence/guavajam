/* Review source-attributed human/agent turns without making a chat application. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import {chromium, firefox, webkit} from 'playwright';

const directory = process.env.PRESS_LANTERN_TEST_DIR;
assert.ok(directory, 'Use make test-browser to build the real reviewed Lantern edition');
const manifest = JSON.parse(await readFile(path.join(directory, 'provenance.json'), 'utf8'));
const uri = pathToFileURL(path.join(directory, manifest.canonical_artifact)).href;

for (const [engineName, engine] of Object.entries({chromium, firefox, webkit})) {
  test(`${engineName}: reviewed speakers are distinguishable without a chat shell`, {timeout: 120000}, async () => {
    const browser = await engine.launch({headless:true});
    try {
      for (const width of [320, 375, 1280]) for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({offline:true, viewport:{width, height:900}, colorScheme});
        try {
          const page = await context.newPage(), requested = [];
          page.on('request', req => { if (/^https?:/.test(req.url())) requested.push(req.url()); });
          await page.goto(uri);
          const attributed = page.locator('h3.press-passage-heading[data-press-speaker]');
          const user = page.locator('h3.press-passage-heading[data-press-speaker="user"]');
          const assistant = page.locator('h3.press-passage-heading[data-press-speaker="assistant"]');
          assert.ok(await user.count() >= 5 && await assistant.count() >= 8);
          assert.equal(await attributed.count(), await user.count() + await assistant.count());
          assert.equal(await page.locator('article .press-record-heading[data-press-speaker]').count(), 0);
          assert.equal(await page.locator('article [data-press-speaker]:not(h3)').count(), 0);
          assert.equal(await page.locator('article .press-supporting-element[data-press-speaker]').count(), 0);
          const styles = await page.evaluate(() => {
            const assistant = document.querySelector('h3[data-press-speaker="assistant"]');
            const user = document.querySelector('h3[data-press-speaker="user"]');
            const rule = getComputedStyle(assistant, '::after');
            const a = getComputedStyle(assistant), u = getComputedStyle(user);
            return {
              tint: rule.backgroundColor, paper: getComputedStyle(document.body).backgroundColor,
              content: rule.content, thickness: parseFloat(rule.borderInlineStartWidth),
              ownLabel: assistant.textContent.includes('Assistant'),
              originLabel: user.textContent.includes('User'),
              originPseudo: getComputedStyle(user, '::after').content,
              font: a.fontFamily === u.fontFamily && a.fontSize === u.fontSize && a.fontWeight === u.fontWeight,
              textColor: a.color === u.color,
              overflow: document.documentElement.scrollWidth > innerWidth + 1,
            };
          });
          assert.equal(styles.content, '""');
          assert.ok(styles.thickness >= 2);
          assert.notEqual(styles.tint, styles.paper, 'responder heading tint must be perceptible');
          assert.equal(styles.originPseudo, 'none', 'unaccented source roles remain neutral');
          assert.equal(styles.font, true);
          assert.equal(styles.textColor, true);
          assert.equal(styles.ownLabel && styles.originLabel, true);
          assert.equal(styles.overflow, false);
          const canonical = await assistant.first().locator('a.press-permalink').getAttribute('href');
          assert.ok(canonical.startsWith(manifest.record_url + '/read#'));
          await assistant.first().locator('a.press-permalink').focus();
          assert.notEqual(await assistant.first().locator('a.press-permalink').evaluate(x => getComputedStyle(x).outlineStyle), 'none');
          await page.emulateMedia({media:'print', colorScheme:'dark'});
          assert.equal(await assistant.first().evaluate(x => getComputedStyle(x,'::after').backgroundColor), 'rgba(0, 0, 0, 0)');
          assert.deepEqual(requested, []);
        } finally {await context.close();}
      }
    } finally {await browser.close();}
  });
}

test('chromium: Windows forced-color simulation preserves textual labels and system rule', {timeout:45000}, async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const context = await browser.newContext({offline:true, forcedColors:'active', viewport:{width:375,height:900}});
    const page = await context.newPage();
    await page.goto(uri);
    assert.equal(await page.evaluate(() => matchMedia('(forced-colors: active)').matches), true);
    const details = await page.locator('h3[data-press-speaker="assistant"]').first().evaluate(node => ({
      label: node.textContent, border: getComputedStyle(node,'::after').borderInlineStartStyle,
      width: parseFloat(getComputedStyle(node,'::after').borderInlineStartWidth),
    }));
    assert.match(details.label, /Assistant/);
    assert.equal(details.border, 'solid');
    assert.ok(details.width >= 2);
    await context.close();
  } finally {await browser.close();}
});
