/* Source-backed quotations, plain authorial prose, and semantic end credits. */
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
  test(`${engineName}: only externally sourced passages are quoted and credited`, {timeout: 120000}, async () => {
    const browser = await engine.launch({headless: true});
    try {
      for (const width of [320, 375, 1280]) for (const colorScheme of ['light', 'dark']) {
        const context = await browser.newContext({offline: true, viewport: {width, height: 900}, colorScheme});
        try {
          const page = await context.newPage(), requested = [];
          page.on('request', req => { if (/^https?:/.test(req.url())) requested.push(req.url()); });
          await page.goto(uri);
          const turns = page.locator('article figure.press-turn[data-press-speaker="assistant"]');
          assert.ok(await turns.count() >= 8);
          assert.equal(await page.locator('article [data-press-speaker]').count(), await turns.count());
          assert.equal(await page.locator('article [data-press-speaker="user"], article [data-press-speaker="author"]').count(), 0);
          assert.equal(await page.locator('article .press-turn > blockquote.press-turn-quotation').count(), await turns.count());
          assert.equal(await page.locator('article .press-turn > figcaption.press-turn-attribution').count(), await turns.count());
          assert.equal(await page.locator('article .press-turn h3.press-passage-heading').count(), 0,
            'quotation contents must not acquire synthetic speaker headings');
          assert.equal(await page.locator('article .press-passage-datetime').count(), 0,
            'private message timestamps do not take space in the published document');
          const technical = page.locator('article figure.press-turn > figcaption .press-turn-details');
          assert.equal(await technical.count(), await turns.count(), 'Lantern responses must retain reviewed model/effort tokens');
          assert.ok((await technical.allTextContents()).every(x =>
            x.includes('Model: gpt-6-thinking') && x.includes('Thinking effort: max')));
          const captionGeometry = await technical.first().evaluate(el => ({
            style: getComputedStyle(el).display,
            color: getComputedStyle(el).color,
            pageOverflow: document.documentElement.scrollWidth > innerWidth + 1,
          }));
          assert.equal(captionGeometry.style, 'block');
          assert.equal(captionGeometry.pageOverflow, false);

          const reader = await page.evaluate(() => {
            const figure = document.querySelector('article figure.press-turn');
            const quote = figure.querySelector(':scope > blockquote');
            const credit = figure.querySelector(':scope > figcaption');
            const text = [...document.querySelectorAll('article .press-supporting-element')].find(el =>
              el.textContent.includes('Help me develop a short technical notebook'));
            const quotedBlock = quote.querySelector('.press-supporting-element');
            const rail = getComputedStyle(quote);
            const figRect = figure.getBoundingClientRect(), quoteRect = quote.getBoundingClientRect();
            return {
              figureTag: figure.tagName, quoteTag: quote.tagName, captionTag: credit.tagName,
              speaker: figure.dataset.pressSpeaker, figID: figure.id, passageID: quotedBlock.id,
              caption: credit.textContent, captionLink: credit.querySelector('a.press-permalink')?.getAttribute('href'),
              authorshipIsPlain: !!text && !text.closest('blockquote, figure') && !text.closest('[data-press-speaker]'),
              authoredElementHasID: !!text?.id, border: rail.borderInlineStartStyle,
              borderWidth: parseFloat(rail.borderInlineStartWidth),
              padding: parseFloat(rail.paddingInlineStart),
              clearance: quotedBlock.getBoundingClientRect().left - quoteRect.left - parseFloat(rail.borderInlineStartWidth),
              left: quoteRect.left, quoteHeight: quoteRect.height, width: figRect.width,
              overflow: document.documentElement.scrollWidth > innerWidth + 1,
            };
          });
          assert.deepEqual([reader.figureTag, reader.quoteTag, reader.captionTag], ['FIGURE', 'BLOCKQUOTE', 'FIGCAPTION']);
          assert.equal(reader.speaker, 'assistant');
          assert.ok(reader.figID && reader.passageID);
          assert.ok(reader.caption.trim().startsWith('— Assistant'));
          assert.ok(reader.captionLink?.endsWith('#' + reader.figID));
          assert.ok(reader.authorshipIsPlain && reader.authoredElementHasID);
          assert.equal(reader.border, 'solid');
          assert.ok(reader.borderWidth >= 2.5 && reader.borderWidth <= 4);
          assert.ok(reader.padding >= 10 && reader.padding <= 12.5);
          assert.ok(reader.clearance >= 9 && reader.clearance <= 15);
          assert.ok(reader.left >= 0 && reader.width <= width && !reader.overflow);
          assert.ok(reader.quoteHeight > 100);

          // Long quotations must keep the neutral rail visible even after any
          // interior heading scrolls out of view; no persistent chat labels.
          const long = await page.evaluate(() => {
            const quotes = [...document.querySelectorAll('figure.press-turn > blockquote')];
            const quote = quotes.sort((a, b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height)[0];
            const children = quote.querySelectorAll('.press-supporting-element');
            children[Math.floor(children.length / 2)].scrollIntoView({block: 'center'});
            return {height: quote.getBoundingClientRect().height, bottom: quote.getBoundingClientRect().bottom,
              border: getComputedStyle(quote).borderInlineStartStyle};
          });
          assert.ok(long.height > 900 && long.bottom > 0 && long.border === 'solid');

          // A cited inner block receives the standard blue segment aligned
          // over the quote rail, while a cited whole turn highlights the figure.
          await page.goto(uri + '#' + reader.passageID);
          const cited = await page.locator('#' + reader.passageID).evaluate(element => {
            const before = getComputedStyle(element, '::before');
            const quote = element.closest('blockquote.press-turn-quotation');
            const border = getComputedStyle(quote);
            return {content: before.content, tint: getComputedStyle(element).backgroundColor,
              paper: getComputedStyle(document.body).backgroundColor,
              grayX: quote.getBoundingClientRect().left,
              blueX: element.getBoundingClientRect().left + parseFloat(before.insetInlineStart),
              grayWidth: parseFloat(border.borderInlineStartWidth),
              blueWidth: parseFloat(before.borderInlineStartWidth)};
          });
          assert.equal(cited.content, '""');
          assert.notEqual(cited.tint, cited.paper);
          assert.ok(Math.abs(cited.grayX - cited.blueX) <= 1);
          assert.ok(Math.abs(cited.grayWidth - cited.blueWidth) <= .75);
          await page.goto(uri + '#' + reader.figID);
          const turnTarget = await turns.first().evaluate(el => ({
            border: getComputedStyle(el.querySelector(':scope > blockquote')).borderInlineStartColor,
            caption: getComputedStyle(el.querySelector(':scope > figcaption')).color,
          }));
          assert.equal(turnTarget.border, turnTarget.caption);

          // Each whole-turn credit is navigable without inventing a heading.
          await turns.first().locator('figcaption a.press-permalink').focus();
          assert.notEqual(await turns.first().locator('figcaption a.press-permalink').evaluate(el =>
            getComputedStyle(el).outlineStyle), 'none');
          await page.emulateMedia({media: 'print', colorScheme: 'dark'});
          const print = await turns.first().evaluate(el => ({
            border: getComputedStyle(el.querySelector('blockquote')).borderInlineStartStyle,
            captionDisplay: getComputedStyle(el.querySelector('figcaption')).display,
          }));
          assert.equal(print.border, 'solid');
          assert.notEqual(print.captionDisplay, 'none');
          assert.equal(await page.locator('#' + reader.passageID).evaluate(el =>
            getComputedStyle(el, '::before').content), 'none');
          assert.deepEqual(requested, []);
        } finally { await context.close(); }
      }
    } finally { await browser.close(); }
  });
}

test('chromium: forced colors retain the real quotation border and targeted source block', {timeout: 45000}, async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({offline: true, forcedColors: 'active', viewport: {width: 375, height: 900}});
    const page = await context.newPage();
    await page.goto(uri);
    const quote = page.locator('figure.press-turn > blockquote.press-turn-quotation').first();
    const targetID = await quote.locator('.press-supporting-element[id]').first().getAttribute('id');
    await page.goto(uri + '#' + targetID);
    assert.equal(await quote.evaluate(el => getComputedStyle(el).borderInlineStartStyle), 'solid');
    const target = await page.locator('#' + targetID).evaluate(el => ({
      outline: getComputedStyle(el).outlineStyle,
      stripe: getComputedStyle(el, '::before').content,
    }));
    assert.equal(target.outline, 'solid');
    assert.equal(target.stripe, 'none');
    await context.close();
  } finally { await browser.close(); }
});
