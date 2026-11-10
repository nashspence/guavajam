import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import {chromium, firefox, webkit} from 'playwright';
import {assertAccessible, reviewSummary} from './browser_accessibility.mjs';

const directory = process.env.PRESS_QUOTATION_TEST_DIR;
assert.ok(directory, 'Use make test-browser to build the verified quotation edition');
const cases = JSON.parse(process.env.PRESS_QUOTATION_CASES);
const manifest = JSON.parse(await readFile(path.join(directory, 'provenance.json'), 'utf8'));
const [entry] = manifest.supporting_documents;
const uri = pathToFileURL(path.join(directory, manifest.canonical_artifact)).href;

async function checkTargets(page, targets) {
  for (const id of Object.values(targets)) {
    await page.goto(uri + '#' + id);
    const cue = await page.locator('#' + id).evaluate(node => {
      const quote = node.closest('blockquote.press-turn-quotation');
      const before = getComputedStyle(node, '::before');
      return {tint: getComputedStyle(node).backgroundColor, paper: getComputedStyle(document.body).backgroundColor,
        content: before.content, color: before.borderInlineStartColor,
        link: getComputedStyle(document.querySelector('.press-status a')).color,
        x: node.getBoundingClientRect().left + parseFloat(before.insetInlineStart),
        railX: quote.getBoundingClientRect().left, width: parseFloat(before.borderInlineStartWidth),
        railWidth: parseFloat(getComputedStyle(quote).borderInlineStartWidth),
        top: node.getBoundingClientRect().top, statusBottom: document.querySelector('.press-status').getBoundingClientRect().bottom};
    });
    assert.equal(cue.content, '""'); assert.notEqual(cue.tint, cue.paper);
    assert.equal(cue.color, cue.link, 'the citation cue uses the normal link color');
    assert.ok(Math.abs(cue.x - cue.railX) <= 1, 'blue cue aligns with the quotation rail');
    assert.ok(Math.abs(cue.width - cue.railWidth) <= 1);
    assert.ok(cue.top >= cue.statusBottom - 1, 'citation target clears the sticky status');
  }
}

for (const [name, engine] of Object.entries({chromium, firefox, webkit})) {
  test(`${name}: quotation edge cases retain targets, credits and 200% reflow`, {timeout: 90000}, async t => {
    const browser = await engine.launch({headless: true});
    try {
      for (const width of [320, 375, 1280]) for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({offline: true, viewport: {width, height: 900}, colorScheme});
        try {
          const page = await context.newPage(), requests = [], errors = [];
          page.on('request', request => {if (/^https?:/.test(request.url())) requests.push(request.url());});
          page.on('pageerror', error => errors.push(error.message));
          await page.goto(uri);
          assert.equal(await page.locator('figure.press-turn').count(), Object.keys(cases.credits).length);
          for (const [key, credit] of Object.entries(cases.credits)) {
            const figure = page.locator('#' + entry.anchors[key]);
            assert.equal((await figure.locator(':scope > figcaption .press-turn-credit').textContent()).replace(/\s+/g, ' ').trim(), credit);
            assert.equal(await figure.locator(':scope > blockquote').count(), 1);
            const link = figure.locator(':scope > figcaption .press-permalink'); await link.focus();
            const box = await link.boundingBox(); assert.ok(box.width >= 44 && box.height >= 44);
            assert.notEqual(await link.evaluate(node => getComputedStyle(node).outlineStyle), 'none');
          }
          for (const key of ['m-author', 'm-end']) assert.equal(await page.locator('#' + entry.anchors[key]).evaluate(node => !!node.closest('figure, blockquote')), false);
          const long = page.locator('#' + entry.anchors['m-long']);
          const targets = await long.locator(':scope > blockquote').evaluate(quote => {
            const blocks = [...quote.children].filter(node => node.id);
            const pick = predicate => {const node = blocks.find(predicate); if (!node) throw new Error('Missing quotation target type'); return node.id;};
            return {heading: pick(node => /^H[3-6]$/.test(node.tagName)),
              paragraph: pick(node => node.tagName === 'DIV' && node.firstElementChild?.tagName === 'P'),
              math: pick(node => node.querySelector('math[display="block"]')),
              code: pick(node => node.querySelector('pre')),
              table: pick(node => node.querySelector('.press-table-scroll table')),
              nestedQuote: pick(node => node.querySelector(':scope > blockquote'))};
          });
          assert.equal(new Set(Object.values(targets)).size, 6, 'each content type has an independent target');
          await checkTargets(page, targets);
          await page.goto(uri + '#' + entry.anchors['m-long']);
          assert.equal(await long.evaluate(node => {
            const link = getComputedStyle(document.querySelector('.press-status a')).color;
            return getComputedStyle(node.querySelector(':scope > blockquote')).borderInlineStartColor === link
              && getComputedStyle(node.querySelector(':scope > figcaption')).color === link;
          }), true, 'whole-quotation rail and credit use the link color');
          await page.goBack(); assert.equal(await page.evaluate(() => location.hash), '#' + targets.nestedQuote);
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
          if (width === 375) {
            const font = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));
            await page.addStyleTag({content: `html {font-size:${font * 2}px !important;letter-spacing:.12em !important;word-spacing:.16em !important} * {line-height:1.8 !important} p {margin-block-end:2em !important}`});
            assert.equal(await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize)), font * 2);
            await checkTargets(page, targets);
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'long credits and quotations reflow at 200% text with spacing');
          }
          if (width === 1280 && colorScheme === 'light') {
            const incomplete = await assertAccessible(page);
            if (incomplete.length) t.diagnostic(JSON.stringify(reviewSummary(incomplete)));
            await page.emulateMedia({forcedColors: 'active'});
            await page.goto(uri + '#' + targets.nestedQuote);
            assert.equal(await page.locator('#' + targets.nestedQuote).evaluate(node => getComputedStyle(node).outlineStyle), 'solid');
            assert.equal(await page.locator('#' + targets.nestedQuote).evaluate(node => getComputedStyle(node, '::before').content), 'none');
          }
          assert.deepEqual(requests, []); assert.deepEqual(errors, []);
        } finally {await context.close();}
      }
    } finally {await browser.close();}
  });
}

test('chromium: actual multi-page PDFs retain text, credits and visible quotation rails', {timeout: 60000}, async () => {
  const browser = await chromium.launch({headless: true}), scratch = await mkdtemp(path.join(os.tmpdir(), 'press-quotation-pdf-'));
  try {
    const inspect = file => {
      const result = spawnSync(process.env.PRESS_BROWSER_PYTHON, ['tests/verify_quotation_pdf.py', file],
        {input: JSON.stringify(cases), encoding: 'utf8', timeout: 15000, maxBuffer: 1024 * 1024});
      assert.ifError(result.error); return result;
    };
    for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({offline: true, viewport: {width: 1280, height: 900}, colorScheme});
      try {
        const page = await context.newPage(); await page.emulateMedia({media: 'print'}); await page.goto(uri);
        await page.evaluate(() => document.fonts.ready);
        const options = {format: 'A4', printBackground: true, margin: {top: '12mm', bottom: '12mm', left: '12mm', right: '12mm'}};
        const file = path.join(scratch, colorScheme + '.pdf'); await page.pdf({...options, path: file});
        const result = inspect(file); assert.equal(result.status, 0, result.stderr);
        const facts = JSON.parse(result.stdout); assert.ok(facts.pages >= 3 && facts.rail_pages >= 3);
        if (colorScheme === 'light') {
          // Reproduce #80's real print failure: unchanged border style and
          // width, but its negative margin clips it outside the printed page.
          await page.addStyleTag({content: '@media print {figure.press-turn > blockquote.press-turn-quotation {margin-inline-start:-.35rem !important}}'});
          const clipped = path.join(scratch, 'clipped.pdf'); await page.pdf({...options, path: clipped});
          const rejected = inspect(clipped); assert.notEqual(rejected.status, 0);
          assert.match(rejected.stderr, /missing visible quotation rail/);
        }
      } finally {await context.close();}
    }
  } finally {await browser.close(); await rm(scratch, {recursive: true, force: true});}
});
