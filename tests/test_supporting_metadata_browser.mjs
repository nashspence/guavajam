import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import {chromium, firefox, webkit} from 'playwright';
import {assertAccessible, reviewSummary} from './browser_accessibility.mjs';

const directory = process.env.PRESS_METADATA_TEST_DIR;
assert.ok(directory, 'Use make test-browser to build the reviewed metadata edition');
const manifest = JSON.parse(await readFile(path.join(directory, 'provenance.json'), 'utf8'));
const url = pathToFileURL(path.join(directory, manifest.canonical_artifact)).href;

for (const [name, engine] of Object.entries({chromium, firefox, webkit})) {
  test(`${name}: supporting metadata is scoped, literal, citable, quiet and printable`, {timeout: 60000}, async t => {
    const browser = await engine.launch({headless: true});
    try {
      for (const width of [320, 1280]) for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({offline: true, viewport: {width, height: 900}, colorScheme});
        try {
          const page = await context.newPage(), requests = [], errors = [];
          page.on('request', r => {if (/^https?:/.test(r.url())) requests.push(r.url());});
          page.on('pageerror', e => errors.push(e.message));
          await page.goto(url);
          assert.equal(await page.locator('.press-document-metadata').count(), 2);
          assert.equal(await page.locator('script').count(), 0);
          assert.equal(await page.locator('head').textContent().then(x => /Example User|Supporting metadata title/.test(x)), false);
          assert.equal(await page.locator('meta[name="schema"],meta[name="date_created"],meta[name="user_account"]').count(), 0);
          for (const entry of manifest.supporting_documents) {
            const slug = path.basename(entry.source_path, '.md');
            const block = page.locator('#' + entry.anchors.details);
            assert.equal(await block.getAttribute('data-press-document'), 'record-md-' + slug.length + '-' + slug);
            const fields = await block.locator('dt').evaluateAll(terms => terms.map(term => {
              const definition = term.nextElementSibling;
              const label = term.querySelector('[data-press-metadata-key]');
              if (label.textContent !== label.getAttribute('data-press-metadata-key')) throw new Error('Metadata label changed');
              return [term.querySelector('[data-press-metadata-key]').getAttribute('data-press-metadata-key'),
                definition.querySelector('[data-press-metadata-kind]').getAttribute('data-press-metadata-kind'),
                [...definition.querySelectorAll('[data-press-metadata-value]')].map(node => node.textContent)];
            }));
            const byKey = Object.fromEntries(fields.map(([key, kind, values]) => [key, {kind, values}]));
            assert.deepEqual(fields.slice(0, 6).map(x => x[0]), ['date_created','user_account','exported_identity','title','schema','unknown_']);
            assert.deepEqual(byKey.date_created.values, ['2026-11-08']);
            assert.deepEqual(byKey.unknown_.values, ['Do not discard this field']);
            assert.deepEqual(byKey['header-includes'].values, ['<script>literal()</script>']);
            assert.deepEqual(byKey['Quoted <key> & label'], {kind:'list', values:['first & literal','second <literal>']});
            assert.deepEqual(byKey.boolean.values, ['yes']); assert.deepEqual(byKey.number.values, ['001']);
            assert.deepEqual(byKey.empty.values, ['']);
            assert.deepEqual(byKey.multiline.values, ['First line.\nSecond line.\n']);
            assert.deepEqual(byKey['Quoted \"label\" · Café'].values, ['Δ & <literal>  \t spaced']);
            assert.deepEqual(byKey['Empty list'], {kind:'list', values:[]});
            assert.deepEqual(byKey.record_note.values, [slug === 'first' ? 'metadata-token-one' : 'metadata-token-two']);
            const appearance = await block.evaluate(node => {
              const style = getComputedStyle(node), provenance = getComputedStyle(document.querySelector('.press-record-provenance'));
              return [style.fontSize, provenance.fontSize, style.color, provenance.color, style.fontFamily, provenance.fontFamily];
            });
            for (let i=0; i<appearance.length; i+=2) assert.equal(appearance[i], appearance[i+1]);
            const permalink = block.locator('.press-permalink');
            assert.equal(await permalink.getAttribute('href'), manifest.record_url + '/read#' + entry.anchors.details);
            await permalink.focus();
            assert.equal(await permalink.evaluate(node => getComputedStyle(node).outlineStyle !== 'none'), true);
          }
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
          const target = manifest.supporting_documents[0].anchors.details;
          await page.getByRole('link', {name:'First metadata', exact:true}).click();
          assert.equal(await page.evaluate(() => location.hash), '#' + target);
          assert.equal(await page.locator('#'+target).evaluate(node => node.getBoundingClientRect().top >=
            document.querySelector('.press-status').getBoundingClientRect().bottom - 1), true);
          await page.emulateMedia({media:'print'});
          for (const block of await page.locator('.press-document-metadata').all()) assert.equal(await block.isVisible(), true);
          assert.equal(await page.getByText('metadata-token-one', {exact:true}).isVisible(), true);
          assert.equal(await page.getByText('metadata-token-two', {exact:true}).isVisible(), true);
          await page.emulateMedia({media:'screen'});
          if (width === 1280 && colorScheme === 'light') {
            const incomplete = await assertAccessible(page);
            if (incomplete.length) t.diagnostic(JSON.stringify(reviewSummary(incomplete)));
          }
          assert.deepEqual(requests, []); assert.deepEqual(errors, []);
        } finally {await context.close();}
      }
    } finally {await browser.close();}
  });
}
