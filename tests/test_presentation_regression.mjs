import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import {chromium} from 'playwright';
import {presentationSnapshot, assertPresentation} from './presentation_snapshot.mjs';

const directory = process.env.PRESS_LANTERN_TEST_DIR;
assert.ok(directory, 'Use make test-browser to build the reviewed Lantern edition');
const manifest = JSON.parse(await readFile(path.join(directory, 'provenance.json'), 'utf8'));
const baseline = JSON.parse(await readFile(new URL('./fixtures/presentation/gfm-appearance.json', import.meta.url), 'utf8'));
const url = pathToFileURL(path.join(directory, manifest.canonical_artifact)).href;

test('GFM evidence keeps approved typography, spacing, palette and layout', {timeout: 60000}, async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const width of [320, 1280]) for (const colorScheme of ['light', 'dark']) for (const media of ['screen', 'print']) {
      const label = `${width}-${colorScheme}-${media}`;
      assert.ok(Object.hasOwn(baseline.cases, label), 'Missing approved presentation case: ' + label);
      const context = await browser.newContext({offline: true, viewport: {width, height: 900}, colorScheme});
      try {
        const page = await context.newPage();
        await page.emulateMedia({media});
        await page.goto(url);
        const expected = {...baseline.base, ...baseline.cases[label]};
        const actual = await presentationSnapshot(page);
        assert.equal(actual['page.horizontalOverflow'], false, label + ': horizontal overflow');
        assertPresentation(actual, expected, label);
        // The gate must detect the first-entry and metadata regressions this baseline protects.
        if (width === 320 && colorScheme === 'light' && media === 'screen') {
          for (const css of [
            '.press-first-passage { border-top: 1px solid red !important; margin-top: 2rem !important; }',
            '.press-passage-datetime { font-size: 2rem !important; font-weight: 700 !important; }',
          ]) {
            const mutation = await page.addStyleTag({content: css});
            const changed = await presentationSnapshot(page);
            assert.throws(() => assertPresentation(changed, expected, label), /firstMessage|metadata/);
            await mutation.evaluate(node => node.remove());
            assertPresentation(await presentationSnapshot(page), expected, label + ': restored');
          }
        }
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
});
