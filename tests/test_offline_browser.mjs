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
              const rule = getComputedStyle(element, '::before');
              const rect = element.getBoundingClientRect();
              const offset = -parseFloat(rule.insetInlineStart);
              const bandWidth = parseFloat(rule.inlineSize);
              const thickness = parseFloat(rule.borderInlineStartWidth);
              return {shadow: style.boxShadow, background: style.backgroundColor,
                position: style.position, ruleContent: rule.content, offset, thickness, bandWidth,
                marginFromText: offset - thickness, tintWidth: bandWidth - thickness,
                stripTint: rule.backgroundColor, ruleLeft: rect.left - offset,
                rect: {left: rect.left, width: rect.width, height: rect.height}};
            });
            assert.equal(targetCue.shadow, 'none');
            assert.equal(targetCue.position, 'relative');
            assert.equal(targetCue.ruleContent, '""', `${name}: citation margin rule missing`);
            assert.ok(targetCue.marginFromText >= 5 && targetCue.marginFromText <= 9,
              `${name}: citation rule is too close or too far from heading: ${JSON.stringify(targetCue)}`);
            assert.ok(targetCue.ruleLeft >= 0, `${name}: citation rule clipped by the viewport`);
            assert.notEqual(targetCue.background, 'rgba(0, 0, 0, 0)', `${name}: target tint missing`);
            assert.ok(Math.abs(targetCue.bandWidth - targetCue.offset) <= 1,
              `${name}: margin tint must meet the heading tint: ${JSON.stringify(targetCue)}`);
            assert.equal(targetCue.stripTint, targetCue.background,
              `${name}: margin gap must use the same background tint as the heading`);
            assert.ok(targetCue.tintWidth >= 5 && targetCue.tintWidth <= 9,
              `${name}: tinted space between rule and text should be 5–9px`);
            assert.equal(await page.locator('#sec-2 a.press-permalink').getAttribute('href'),
              manifest.record_url + '/read#sec-2');
            {
              // Enlarged text and wider letter spacing stress status-bar wrapping.
              await page.evaluate(() => {
                const root = document.documentElement;
                root.style.fontSize = parseFloat(getComputedStyle(root).fontSize) * 2 + 'px';
                // WCAG 1.4.12 stress values plus enlarged text: reflow without
                // clipping or hiding the linked heading under the status bar.
                for (const element of document.querySelectorAll('body, body *')) {
                  element.style.lineHeight = '1.5';
                  element.style.letterSpacing = '0.12em';
                  element.style.wordSpacing = '0.16em';
                }
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
                cue: getComputedStyle(document.querySelector('#sec-2'), '::before').content,
                supportsWidows: CSS.supports('widows', '3') && CSS.supports('orphans', '3'),
                orphans: paragraph.orphans, widows: paragraph.widows};
            });
            assert.equal(printRules.shadow, 'none', `${name}: print should not mark fragment targets`);
            assert.equal(printRules.cue, 'none', `${name}: print should hide the margin rule`);
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

  test(`${name}: heading, supporting passage and footnote targets retain geometry and cues`, {timeout: 60000}, async () => {
    const browser = await engine.launch({headless: true});
    try {
      for (const width of [320, 375, 1280]) for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({offline: true, viewport: {width, height: 900}, colorScheme});
        try {
          const page = await context.newPage();
          const url = pathToFileURL(path.join(root, 'index.html')).href;
          for (const id of ['sec-2', 'detail-2', 'record-md-4-note', 'support-md-4-note-claim', 'fn1']) {
            await page.goto(url);
            const target = page.locator('#' + id);
            const before = await target.evaluate(element => {
              const rect = element.getBoundingClientRect();
              return [rect.left + scrollX, rect.top + scrollY, rect.width, rect.height];
            });
            await page.goto(url + '#' + id);
            const after = await target.evaluate(element => {
              const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
              const cue = getComputedStyle(element, '::before');
              return {box: [rect.left + scrollX, rect.top + scrollY, rect.width, rect.height],
                background: style.backgroundColor, tint: cue.backgroundColor, content: cue.content,
                cueLeft: rect.left + parseFloat(cue.insetInlineStart),
                top: rect.top, header: document.querySelector('.press-status').getBoundingClientRect().bottom};
            });
            for (let i = 0; i < before.length; i++) {
              assert.ok(Math.abs(before[i] - after.box[i]) <= 1, `${name} ${id}: fragment changed layout`);
            }
            assert.equal(after.content, '""', `${name} ${id}: target cue missing`);
            assert.notEqual(after.background, 'rgba(0, 0, 0, 0)');
            assert.equal(after.tint, after.background);
            assert.ok(after.cueLeft >= 0, `${name} ${id}: cue clipped`);
            assert.ok(after.top >= after.header - 1, `${name} ${id}: target obscured`);
            await page.emulateMedia({media: 'print', colorScheme: 'dark'});
            assert.deepEqual(await target.evaluate(element => [getComputedStyle(element).backgroundColor,
              getComputedStyle(element, '::before').content]), ['rgba(0, 0, 0, 0)', 'none']);
            await page.emulateMedia({media: 'screen', colorScheme});
          }
        } finally { await context.close(); }
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
          shadow: style.boxShadow, cue: getComputedStyle(element, '::before').content};
      });
      assert.equal(target.outline, 'solid');
      assert.ok(target.width >= 2, `forced-colors fragment outline missing: ${JSON.stringify(target)}`);
      assert.equal(target.shadow, 'none');
      assert.equal(target.cue, 'none', 'forced colors should use only the system outline');
      const permalink = page.locator('#sec-2 a.press-permalink');
      await permalink.hover();
      assert.equal(await permalink.evaluate(el => getComputedStyle(el).outlineStyle), 'solid');
      await permalink.focus();
      // Keyboard navigation must retain a strong focus ring even after hover.
      await page.keyboard.press('Shift+Tab');
      await page.keyboard.press('Tab');
      assert.equal(await permalink.evaluate(el => el.matches(':focus-visible')), true);
      assert.ok(await permalink.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth)) >= 2);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await page.emulateMedia({media: 'print'});
      assert.equal(await page.locator('#sec-2').evaluate(el => getComputedStyle(el).outlineStyle), 'none');
    } finally { await context.close(); }
  } finally { await browser.close(); }
});
