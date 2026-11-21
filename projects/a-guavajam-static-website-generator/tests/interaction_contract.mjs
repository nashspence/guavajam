// Exercise actual Tab order and hit targets, rather than CSS selector presence.
import assert from 'node:assert/strict';

export async function keyboardControls(page) {
  const count = await page.evaluate(() => {
    const visible = [...document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex]')]
      .filter(n => !n.disabled && n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden');
    for (const n of visible) {
      if (n.matches('a[href]:not([aria-hidden=true]),button,input,select,textarea,summary') && n.tabIndex < 0)
        throw Error('Interactive control missing from Tab order: ' + (n.getAttribute('aria-label') || n.textContent));
    }
    const controls = visible.filter(n => n.tabIndex >= 0);
    controls.forEach((n, index) => n.setAttribute('data-keyboard-test', index));
    document.body.tabIndex = -1; document.body.focus();
    return controls.length;
  });
  const seen = new Set();
  for (let step = 0; step < count; step++) {
    await page.keyboard.press('Tab');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const state = await page.locator(':focus').evaluate(n => {
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
    assert(state.index !== null && !seen.has(state.index), `Tab failed to reach each control: ${JSON.stringify(state)}`);
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
