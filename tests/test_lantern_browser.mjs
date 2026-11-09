import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium, firefox, webkit } from 'playwright';
import { assertAccessible, reviewSummary } from './browser_accessibility.mjs';

const directory = process.env.PRESS_LANTERN_TEST_DIR;
assert.ok(directory, 'Use make test-browser to build the reviewed Lantern edition');
const manifest = JSON.parse(await readFile(path.join(directory, 'provenance.json'), 'utf8'));
const fixture = JSON.parse(await readFile(new URL('./fixtures/chatgpt/lantern.json', import.meta.url), 'utf8'))[0];
const programs = Object.values(fixture.mapping).flatMap(node => node.message?.content.parts ?? [])
  .flatMap(text => [...text.matchAll(/^<CodeBlock language="python"(?: editable)?>\n(.*?)^<\/CodeBlock>/gms)]
    .map(match => match[1].replace(/\n$/, '')));
assert.equal(programs.length, 2);

for (const [name, engine] of Object.entries({ chromium, firefox, webkit })) {
  test(`${name}: both Lantern branches render offline with exact code, mathematics and citations`, { timeout: 120000 }, async t => {
    const browser = await engine.launch({ headless: true });
    try {
      for (const width of [320, 1280]) {
        const context = await browser.newContext({ offline: true, viewport: { width, height: 900 } });
        try {
          const page = await context.newPage();
          const requests = [], errors = [];
          page.on('request', request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
          page.on('pageerror', error => errors.push(error.message));
          await page.goto(pathToFileURL(path.join(directory, manifest.canonical_artifact)).href);
          assert.deepEqual(requests, []);
          assert.deepEqual(errors, []);
          assert.equal(await page.locator('main').locator('Chart, CodeBlock, Cite, Link, row, box, icon').count(), 0);
          assert.equal(await page.locator('math').count(), 109, '106 fixture formulas plus three manuscript formulas');
          assert.equal(await page.locator('table').count() - await page.locator('math table').count(), 22);
          assert.ok(await page.locator('math mtable').count() >= 10, 'matrices and aligned mathematics remain structured MathML');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
          assert.equal(await page.locator('a[href^="sandbox:"], img[src^="sandbox:"]').count(), 0);
          assert.equal(await page.getByText('[Unsupported message content omitted]', { exact: true }).count(), 0);
          assert.ok(await page.locator('h3', { hasText: 'Assistant (gpt-6-thinking)' }).count() > 10);
          assert.ok(await page.getByText(/2020-01-01T.*Z/).count() > 10, 'synthetic fixture times survive publication');
          assert.ok(await page.locator('a[href="https://docs.python.org/3/library/csv.html"]').count() >= 2);
          assert.ok(await page.locator('a[href*="nist.gov/"]').count() >= 2);
          const presentation = page.locator('main span[title^="<text "]').filter({ hasText: 'Technical Notebook' }).first();
          assert.equal(await presentation.count(), 1);
          assert.match(await presentation.getAttribute('title'), /^<text .* … <\/text>$/);
          assert.doesNotMatch(await presentation.innerText(), /<text|<\/text>/);
          const icon = page.locator('main span[title^="<icon "]').first();
          assert.equal(await icon.innerText(), '□');
          await icon.hover();
          assert.match(await icon.getAttribute('title'), /^<icon .*\/>$/);
          assert.deepEqual(await icon.evaluate(el => el.getAttributeNames()), ['title']);
          const code = await page.locator('pre code').allTextContents();
          for (const program of programs) assert.ok(code.some(text => text.replace(/\n$/, '') === program), 'Python stays byte-exact');
          for (const document of manifest.supporting_documents) {
            const heading = page.locator('#record-md-' + document.source_path.split('/').at(-1).replace('.md', '').length +
              '-' + document.source_path.split('/').at(-1).replace('.md', ''));
            assert.equal(await heading.count(), 1);
            assert.ok(await page.locator('a[href="' + manifest.record_url + '/read#' + document.anchors[Object.keys(document.anchors)[1]] + '"]').count() >= 1);
          }
          if (width === 1280) {
            const incomplete = await assertAccessible(page);
            if (incomplete.length) t.diagnostic(JSON.stringify(reviewSummary(incomplete)));
          }
        } finally { await context.close(); }
      }
    } finally { await browser.close(); }
  });
}
