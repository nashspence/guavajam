// Exercise actual Tab order and hit targets, rather than CSS selector presence.
import assert from 'node:assert/strict';

export async function keyboardControls(page) {
  const count = await page.evaluate(() => {
    const visible = [...document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex]')]
      .filter(n => !n.disabled && n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden')
      .filter(n => {
        for (let p = n.parentElement; p; p = p.parentElement) {
          if (p.matches('details:not([open])') && !p.querySelector(':scope > summary')?.contains(n)) return false;
        }
        return true;
      });
    for (const n of visible) {
      if (n.matches('a[href]:not([aria-hidden=true]),button,input,select,textarea,summary') && n.tabIndex < 0)
        throw Error('Interactive control missing from Tab order: ' + (n.getAttribute('aria-label') || n.textContent));
    }
    const controls = visible.filter(n => n.tabIndex >= 0);
    if (controls.some(n => n.tabIndex > 0)) throw Error('Positive tabindex changes the reading order');
    controls.forEach((n, index) => n.setAttribute('data-keyboard-test', index));
    document.body.tabIndex = -1; document.body.focus();
    return controls.length;
  });
  const seen = new Set();
  for (let step = 0; step < count; step++) {
    await page.keyboard.press('Tab');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const state = await page.evaluate(() => {
      const n = document.activeElement;
      const style = getComputedStyle(n), box = n.getBoundingClientRect();
      const ring = parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset);
      const clipped = [];
      for (let p = n.parentElement; p && p !== document.body; p = p.parentElement) {
        const css = getComputedStyle(p), bounds = p.getBoundingClientRect();
        if (css.overflowX !== 'visible' && (box.left - ring < bounds.left - 1 || box.right + ring > bounds.right + 1)) clipped.push({class:p.className, box:[box.left,box.top,box.right,box.bottom], bounds:[bounds.left,bounds.top,bounds.right,bounds.bottom], overflow:[css.overflowX,css.overflowY], pannable:p.tabIndex>=0 && p.scrollWidth>p.clientWidth, axis:'x'});
        if (css.overflowY !== 'visible' && (box.top - ring < bounds.top - 1 || box.bottom + ring > bounds.bottom + 1)) clipped.push({class:p.className, box:[box.left,box.top,box.right,box.bottom], bounds:[bounds.left,bounds.top,bounds.right,bounds.bottom], overflow:[css.overflowX,css.overflowY], axis:'y'});
      }
      return {index: n.getAttribute('data-keyboard-test'), label: n.getAttribute('aria-label') || n.textContent.slice(0, 60),
        visible: n.matches(':focus-visible'), width: parseFloat(style.outlineWidth), offset: parseFloat(style.outlineOffset),
        clipped, boxWidth: box.width, viewport: innerWidth};
    });
    assert(state.index !== null && !seen.has(state.index), `Tab ${step + 1}/${count} failed to reach each control: ${JSON.stringify(state)}`);
    assert.equal(state.index, String(step), `Tab order differs from document order: ${JSON.stringify(state)}`);
    assert(state.visible && state.width >= 2 && state.offset >= 3, `Focus needs breathing room: ${JSON.stringify(state)}`);
    // An unbroken title may exceed its scroll viewport. Its explicit tab stop
    // remains keyboard-pannable; every control that fits must show its full ring.
    const clipped = state.clipped.filter(c => !(c.axis === 'x' && c.pannable &&
      state.boxWidth + 12 > c.bounds[2] - c.bounds[0]));
    assert.deepEqual(clipped, [], `Clipped focus ring: ${JSON.stringify(state)}`);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Keyboard focus must keep the page horizontally contained');
    seen.add(state.index);
  }
  assert.equal(seen.size, count);
  return count;
}

export async function hoverControls(page) {
  for (const selector of ['.guavajam-passage-cite', '.guavajam-citation > a', '.guavajam-record-link',
    '.guavajam-search-filters > summary', '.guavajam-message-detail > summary', '#guavajam-search button:not(:disabled)']) {
    const target = page.locator(selector).first();
    if (!await target.isVisible()) continue;
    await page.evaluate(() => document.activeElement?.blur());
    await page.mouse.move(0, 0);
    const paint = async () => target.evaluate(n => {
      const s = getComputedStyle(n), label = getComputedStyle(n.querySelector('span') || n);
      return [s.color, s.backgroundColor, s.boxShadow, label.textDecorationLine, label.textDecorationThickness, s.outlineStyle, s.outlineWidth];
    });
    if (selector === '.guavajam-passage-cite') {
      assert.equal(await target.locator('span').textContent(), 'Cite');
      assert.equal(await target.locator('span').evaluate(n => getComputedStyle(n).textDecorationLine), 'underline');
    }
    const before = await paint(); await target.hover();
    const after = await paint();
    assert.notDeepEqual(after, before, `Hover has no visible feedback: ${selector}`);
    assert(await target.evaluate(n => {
      const b = n.getBoundingClientRect(), hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      return hit === n || n.contains(hit);
    }), `Overlapping hit target: ${selector}`);
  }
}

export async function instrumentSearch(page) {
  await page.addInitScript(() => {
    window.guavajamSearchRequests = 0;
    const NativeWorker = window.Worker;
    window.Worker = class extends NativeWorker {
      postMessage(data, ...args) {
        if (data.action === 'search') window.guavajamSearchRequests++;
        return super.postMessage(data, ...args);
      }
    };
  });
}

export async function openSearchFilters(page) {
  const requests = await page.evaluate(() => window.guavajamSearchRequests);
  const toggle = page.locator('.guavajam-search-filters > summary');
  await toggle.focus(); await toggle.press('Enter');
  assert(await page.locator('.guavajam-search-filters').evaluate(n => n.open));
  assert(await toggle.evaluate(n => n === document.activeElement));
  assert.equal(await page.evaluate(() => window.guavajamSearchRequests), requests,
    'opening filters is a native disclosure, never a database query');
  assert(await page.locator('select[name=sort]').isVisible());
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
}

export async function searchFeedbackGeometry(page, initial = false) {
  return page.evaluate(initial => {
    const bounds = n => {
      const r = n.getBoundingClientRect();
      return Object.fromEntries(['x', 'y', 'width', 'height'].map(k => [k, Math.round(r[k] * 10) / 10]));
    };
    const summary = document.querySelector('.guavajam-search-summary');
    const range = document.createRange(); range.selectNodeContents(summary.firstChild);
    const rect = range.getBoundingClientRect();
    const state = {summary: bounds(summary), label: [rect.x, rect.y, rect.width, rect.height],
      downloadSlot: bounds(document.querySelector('#guavajam-search-spinner').parentElement)};
    if (!initial) {
      state.button = bounds(document.querySelector('#guavajam-search button[type=submit]'));
      state.querySlot = bounds(document.querySelector('#guavajam-search-query-spinner').parentElement);
    }
    return state;
  }, initial);
}

// A text hit is useful only when the reader can see why it matched. This is
// shared by Works, Versions, Documents and Passages, including historical hits.
export async function searchEvidence(page) {
  for (const row of await page.locator('#guavajam-search-results > li').all()) {
    const evidence = row.locator('.guavajam-search-excerpt');
    assert.equal(await evidence.count(), 1, 'each text result explains its exact winning record');
    assert.match(await evidence.locator('.guavajam-excerpt-label').textContent(),
      /^(Title|Author|Description|Tag or subject|Publication metadata|Document text|Passage) match$/);
    assert(await evidence.locator('.guavajam-passage-excerpt mark').count() > 0,
      'even body-only, historical and provenance-only matches show highlighted evidence');
    assert((await evidence.locator('.guavajam-passage-excerpt').textContent()).length <= 322,
      'match evidence stays bounded near the matched text');
  }
}

export async function recordLinkAlignment(page) {
  for (const heading of await page.locator('.guavajam-record-list:visible h3:has(.guavajam-record-link)').all()) {
    const aligned = await heading.evaluate(n => {
      const title = n.querySelector('a:first-child').getBoundingClientRect();
      const icon = n.querySelector('.guavajam-record-link').getBoundingClientRect();
      return Math.abs((title.top + title.bottom - icon.top - icon.bottom) / 2) < 1 && icon.left >= title.right;
    });
    assert(aligned, 'the work-page icon stays inline and vertically centered beside its title');
  }
}

export async function fixedPageHorizontalScroll(page, {touch = false} = {}) {
  const prior = await page.evaluateHandle(() => document.activeElement);
  const field = page.locator('input[name=q]');
  const positions = await page.evaluate(() => ({body: document.body.getBoundingClientRect().x,
    main: document.querySelector('main').getBoundingClientRect().x, viewport: innerWidth,
    scale: visualViewport?.scale || 1}));
  const phases = await field.isVisible() ? ['opened', 'focused', 'blurred'] : ['static'];
  for (const phase of phases) {
    if (phase === 'focused') { if (touch) await field.tap(); else await field.click(); }
    if (phase === 'blurred') await field.evaluate(n => n.blur());
    // Desktop gestures target empty page margins. Touch-mode WebKit exposes
    // taps but no wheel API; it exercises the same focus/blur viewport checks.
    const gestures = touch ? [[0, 0]] : [2, positions.viewport - 2].flatMap(x => [[x, 240], [x, -240]]);
    for (const [x, delta] of gestures) {
      if (!touch) { await page.mouse.move(x, 160); await page.mouse.wheel(delta, 0); }
      const frames = await page.evaluate(() => new Promise(resolve => {
        const samples = [];
        const frame = () => {
          samples.push({scroll: scrollX, body: document.body.getBoundingClientRect().x,
            main: document.querySelector('main').getBoundingClientRect().x, scale: visualViewport?.scale || 1});
          if (samples.length === 6) resolve(samples); else requestAnimationFrame(frame);
        }; requestAnimationFrame(frame);
      }));
      for (const sample of frames) {
        assert.equal(sample.scroll, 0, `no horizontal viewport movement ${phase}`);
        assert.equal(sample.scale, positions.scale, `focus and blur preserve the visual viewport scale ${phase}`);
        assert(Math.abs(sample.body - positions.body) < .5 && Math.abs(sample.main - positions.main) < .5,
          `empty-page horizontal wheel gestures keep the whole column fixed ${phase}`);
      }
    }
  }
  // iOS focus zoom uses the visual viewport, not document scrollWidth.
  // Keep editable text at its non-magnifying size without disabling user zoom.
  const editable = 'input:not([type=hidden], [type=checkbox], [type=radio]), select, textarea, [contenteditable=true]';
  assert(await page.locator(editable).evaluateAll(nodes => nodes.every(n =>
    parseFloat(getComputedStyle(n).fontSize) >= 16)), 'every editable control avoids Safari small-text focus zoom');
  const viewport = await page.locator('meta[name=viewport]').getAttribute('content');
  assert(!/user-scalable\s*=\s*no|maximum-scale\s*=/.test(viewport), 'manual pinch zoom stays available');
  if (prior) await prior.evaluate(n => n.focus());
}

export async function searchHighlightContrast(page) {
  const original = await page.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  for (const colorScheme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme});
    const ratios = await page.locator('#guavajam-search-results mark').first().evaluate(n => {
      const luminance = rgb => {
        const values = rgb.match(/[\d.]+/g).slice(0,3).map(Number).map(value => {
          const c = value / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
        }); return values[0]*.2126 + values[1]*.7152 + values[2]*.0722;
      };
      const contrast = (a,b) => (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
      const style = getComputedStyle(n), paper = getComputedStyle(document.body).backgroundColor;
      return {text: contrast(luminance(style.color),luminance(style.backgroundColor)),
        cue: contrast(luminance(style.backgroundColor),luminance(paper))};
    });
    assert(ratios.text >= 4.5, `${colorScheme}: all marked text meets 4.5:1 text contrast`);
    assert(ratios.cue >= 3, `${colorScheme}: the match cue is distinct from the reading paper`);
  }
  await page.emulateMedia({colorScheme: original});
}
