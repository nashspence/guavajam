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
          // Every explicit message is a plain, non-landmark div. The H3 still
          // owns its ID, role, and canonical permalink; body blocks remain
          // individually citable within the correct source-backed turn.
          assert.equal(await page.locator('article .press-turn').count(), await attributed.count());
          assert.equal(await page.locator('article .press-turn > h3.press-passage-heading').count(),
            await attributed.count());
          const styles = await page.evaluate(() => {
            const assistant = document.querySelector('h3[data-press-speaker="assistant"]');
            const user = document.querySelector('h3[data-press-speaker="user"]');
            const turn = assistant.parentElement, userTurn = user.parentElement;
            const rule = getComputedStyle(assistant, '::after');
            const rail = getComputedStyle(turn, '::before');
            const a = getComputedStyle(assistant), u = getComputedStyle(user);
            return {
              tint: rule.backgroundColor, paper: getComputedStyle(document.body).backgroundColor,
              content: rule.content, railContent: rail.content,
              thickness: parseFloat(rail.borderInlineStartWidth),
              railStyle: rail.borderInlineStartStyle,
              ownLabel: assistant.textContent.includes('Assistant'),
              originLabel: user.textContent.includes('User'),
              originPseudo: getComputedStyle(userTurn, '::before').content,
              font: a.fontFamily === u.fontFamily && a.fontSize === u.fontSize && a.fontWeight === u.fontWeight,
              textColor: a.color === u.color,
              overflow: document.documentElement.scrollWidth > innerWidth + 1,
              sourceParagraphs: turn.querySelectorAll('.press-supporting-element').length,
              responderPadding: parseFloat(getComputedStyle(turn).paddingInlineStart),
              userPadding: parseFloat(getComputedStyle(userTurn).paddingInlineStart),
              railLeft: turn.getBoundingClientRect().left + parseFloat(rail.insetInlineStart),
              textClearance: turn.querySelector('.press-supporting-element').getBoundingClientRect().left -
                turn.getBoundingClientRect().left - parseFloat(rail.insetInlineStart) -
                parseFloat(rail.borderInlineStartWidth),
            };
          });
          assert.equal(styles.content, '""');
          assert.equal(styles.railContent, '""');
          assert.equal(styles.railStyle, 'solid');
          assert.ok(styles.thickness >= 2);
          assert.ok(styles.sourceParagraphs > 1, 'response body was not grouped');
          assert.notEqual(styles.tint, styles.paper, 'responder heading tint must be perceptible');
          assert.equal(styles.originPseudo, 'none', 'unaccented source roles remain neutral');
          assert.equal(styles.font, true);
          assert.equal(styles.textColor, true);
          assert.equal(styles.ownLabel && styles.originLabel, true);
          assert.equal(styles.overflow, false);
          // Text moves inward, never the outer rail: preserve the narrow-phone
          // margin while leaving enough breathing room to read long answers.
          assert.ok(styles.responderPadding >= 4 && styles.responderPadding <= 5,
            `${engineName}: unexpected responder padding: ${JSON.stringify(styles)}`);
          assert.equal(styles.userPadding, 0);
          assert.ok(styles.railLeft >= 0 && styles.textClearance >= 10 && styles.textClearance <= 14,
            `${engineName}: responder rail too close to words or clipped: ${JSON.stringify(styles)}`);
          // The rail remains visible at the middle of a long turn even when
          // its heading is several screens above, and ends at the next message.
          const midTurn = await page.evaluate(() => {
            const turns = [...document.querySelectorAll('.press-turn')].filter(turn =>
              turn.querySelector(':scope > h3[data-press-speaker="assistant"]'));
            const longest = turns.sort((a,b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height)[0];
            const parts = [...longest.querySelectorAll(':scope > .press-supporting-element')];
            const midway = parts[Math.floor(parts.length / 2)];
            midway.scrollIntoView({block:'center'});
            return {
              headingTop:longest.querySelector('h3').getBoundingClientRect().top,
              end:longest.getBoundingClientRect().bottom,
              bottom:innerHeight,
              rail:getComputedStyle(longest,'::before').borderInlineStartStyle,
              nextRole:longest.nextElementSibling?.matches('.press-turn') ?
                longest.nextElementSibling.querySelector(':scope > h3')?.getAttribute('data-press-speaker') : null,
            };
          });
          assert.ok(midTurn.headingTop < 0 && midTurn.end > 0,
            `${engineName}: long response rail must remain in view after its heading scrolls away`);
          assert.equal(midTurn.rail, 'solid');
          // A citation within a responder turn remains specific without a
          // second vertical blue stripe competing with its continuous rail.
          const targetID = await page.evaluate(() => {
            const turns = [...document.querySelectorAll('.press-turn')].filter(turn =>
              turn.querySelector(':scope > h3[data-press-speaker="assistant"]'));
            const longest = turns.sort((a,b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height)[0];
            return longest.querySelector(':scope > .press-supporting-element[id]').id;
          });
          await page.goto(uri + '#' + targetID);
          const cited = await page.locator('#' + targetID).evaluate(element => {
            const tint = getComputedStyle(element), stripe = getComputedStyle(element,'::before');
            const group = element.closest('.press-turn');
            return {
              shadow: tint.boxShadow, tint: tint.backgroundColor,
              paper: getComputedStyle(document.body).backgroundColor,
              stripe: stripe.content, speakerRail: getComputedStyle(group,'::before').borderInlineStartStyle,
              clearOfStatus: element.getBoundingClientRect().top >=
                document.querySelector('.press-status').getBoundingClientRect().bottom - 1,
              overflow: document.documentElement.scrollWidth > innerWidth + 1,
            };
          });
          assert.ok(cited.shadow.includes('inset'), `${engineName}: citation needs a horizontal rule`);
          assert.notEqual(cited.tint, cited.paper);
          assert.equal(cited.stripe, 'none', `${engineName}: doubled citation and role rails`);
          assert.equal(cited.speakerRail, 'solid');
          assert.equal(cited.clearOfStatus && !cited.overflow, true);
          // A citation to the responder heading behaves the same way.
          const assistantID = await assistant.first().getAttribute('id');
          await page.goto(uri + '#' + assistantID);
          const headTarget = await assistant.first().evaluate(element => ({
            shadow: getComputedStyle(element).boxShadow,
            stripe: getComputedStyle(element,'::before').content,
          }));
          assert.ok(headTarget.shadow.includes('inset'));
          assert.equal(headTarget.stripe, 'none');
          // Ordinary author citations keep the original blue margin cue.
          const userID = await user.first().getAttribute('id');
          await page.goto(uri + '#' + userID);
          assert.equal(await user.first().evaluate(el => getComputedStyle(el,'::before').content), '""');
          // Re-enter the responder to check print removes only the citation cue.
          await page.goto(uri + '#' + targetID);
          const canonical = await assistant.first().locator('a.press-permalink').getAttribute('href');
          assert.ok(canonical.startsWith(manifest.record_url + '/read#'));
          await assistant.first().locator('a.press-permalink').focus();
          assert.notEqual(await assistant.first().locator('a.press-permalink').evaluate(x => getComputedStyle(x).outlineStyle), 'none');
          await page.emulateMedia({media:'print', colorScheme:'dark'});
          const print = await assistant.first().evaluate(x => {
            const turn = x.parentElement;
            return {heading: getComputedStyle(x,'::after').backgroundColor,
              border: getComputedStyle(turn).borderInlineStartStyle,
              width: parseFloat(getComputedStyle(turn).borderInlineStartWidth),
              screenRail: getComputedStyle(turn,'::before').content};
          });
          assert.equal(print.heading, 'rgba(0, 0, 0, 0)');
          assert.equal(print.border, 'solid');
          assert.ok(print.width >= 2, 'printed long turns need a fragment-safe border');
          assert.equal(print.screenRail, 'none');
          assert.equal(await page.locator('#' + targetID).evaluate(el => getComputedStyle(el).boxShadow), 'none',
            `${engineName}: citation cue should not be printed`);
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
    const targetID = await page.locator('.press-turn:has(> h3[data-press-speaker="assistant"])')
      .first().locator('.press-supporting-element[id]').first().getAttribute('id');
    await page.goto(uri + '#' + targetID);
    const targeted = await page.locator('#' + targetID).evaluate(el => ({
      shadow: getComputedStyle(el).boxShadow,
      outline: getComputedStyle(el).outlineStyle,
      stripe: getComputedStyle(el,'::before').content,
    }));
    assert.equal(targeted.shadow, 'none');
    assert.equal(targeted.outline, 'solid');
    assert.equal(targeted.stripe, 'none');
    const details = await page.locator('h3[data-press-speaker="assistant"]').first().evaluate(node => ({
      label: node.textContent, border: getComputedStyle(node.parentElement,'::before').borderInlineStartStyle,
      width: parseFloat(getComputedStyle(node.parentElement,'::before').borderInlineStartWidth),
    }));
    assert.match(details.label, /Assistant/);
    assert.equal(details.border, 'solid');
    assert.ok(details.width >= 2);
    await context.close();
  } finally {await browser.close();}
});
