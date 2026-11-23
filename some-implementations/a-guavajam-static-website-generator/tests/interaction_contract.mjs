// Exercise actual Tab order and hit targets, rather than CSS selector presence.
import assert from 'node:assert/strict';

export async function iconLinkFallbacks(page) {
  const result = await page.evaluate(() => {
    const labels = [...document.querySelectorAll('a > .guavajam-icon-fallback')]
      .filter(n => n.parentElement.getClientRects().length);
    const geometry = () => labels.map(n => n.parentElement)
      .map(n => { const b = n.getBoundingClientRect(); return [b.x,b.y,b.width,b.height]; });
    const before = geometry();
    const pageWidth = document.documentElement.scrollWidth;
    const failures = [];
    for (const label of labels) {
      const css = getComputedStyle(label), b = label.getBoundingClientRect();
      if (!label.textContent.trim() || css.position !== 'absolute' ||
          css.clipPath === 'none' || b.width > 1 || b.height > 1)
        failures.push(label.textContent);
    }
    const parents = labels.map(label => [label, label.parentElement, label.nextSibling]);
    labels.forEach(n => n.remove());
    // Keep stable references: removing labels intentionally changes the selector.
    const without = parents.map(([,parent]) => {const b=parent.getBoundingClientRect();return [b.x,b.y,b.width,b.height];});
    const withoutWidth = document.documentElement.scrollWidth;
    parents.forEach(([label,parent,next]) => parent.insertBefore(label,next));
    return {failures, before, without,
      pageWidth, withoutWidth, restoredWidth: document.documentElement.scrollWidth, labels: labels.length};
  });
  assert.deepEqual(result.failures, [], 'Every icon-only link has a clipped, meaningful HTML fallback');
  assert.deepEqual(result.without, result.before, 'Fallbacks preserve link position and hit-target dimensions');
  assert.equal(result.withoutWidth, result.pageWidth, 'Fallbacks preserve page containment');
  assert.equal(result.restoredWidth, result.pageWidth, 'Restoring fallback text preserves page containment');
  return result.labels;
}

export async function textLinkCues(page) {
  for (const wrapper of await page.locator('.guavajam-text-link:visible').all()) {
    assert(await wrapper.evaluate(n=>{
      const a=n.querySelector('a'),caption=n.querySelector('.guavajam-link-label'),s=getComputedStyle(n);
      const probe=document.createElement('span');probe.style.color='var(--guavajam-prose-rule)';n.append(probe);
      const muted=getComputedStyle(probe).color;probe.remove();
      return !a.contains(caption) && a.getAttribute('aria-label') && a.textContent.trim()==='Link' &&
        getComputedStyle(caption).color===s.color && s.borderBottomStyle==='dotted' &&
        parseFloat(s.borderBottomWidth)===1 && s.borderBottomColor===muted &&
        [...a.querySelectorAll('svg')].every(svg=>Math.abs(svg.getBoundingClientRect().width-parseFloat(getComputedStyle(a).fontSize)*.9)<.2);
    }),'prose links activate through an emphasized icon; a subtle dotted rule groups icon and plain caption');
  }
}


export async function readerDetailLayout(page) {
  const report=await page.evaluate(()=>{
    const failures=[];
    // Measure artwork-to-caption distance, including any accidental HTML spaces.
    for (const n of document.querySelectorAll('.guavajam-icon-label,.guavajam-text-link,.guavajam-contents-entry,.guavajam-disclosure-summary')) {
      if (!n.getClientRects().length) continue;
      const a=n.querySelector('.guavajam-icon-control,.guavajam-contents-link,.guavajam-disclosure-arrow');
      if (!a) continue;
      const svg=[...a.querySelectorAll('svg')].at(-1), caption=n.querySelector('.guavajam-link-label,.guavajam-disclosure-label');
      const range=document.createRange();
      if(caption) range.selectNodeContents(caption);
      else {range.setStartAfter(a);range.setEnd(n,n.childNodes.length);}
      const b=range.getClientRects()[0],s=svg.getBoundingClientRect();
      if(!b || b.left<s.right || b.top>=s.bottom || b.bottom<=s.top) continue; // Prose may wrap.
      const expected=parseFloat(getComputedStyle(a).fontSize)*(n.matches('.guavajam-text-link') ? .2 : .3);
      if(Math.abs(b.left-s.right-expected)>.8) failures.push({kind:n.className,gap:b.left-s.right,expected});
    }
    const field=document.querySelector('#guavajam-search input[name=q]');
    if(field?.getClientRects().length) {
      const x=field.getBoundingClientRect().left;
      for(const a of document.querySelectorAll('.guavajam-disclosure-summary > .guavajam-disclosure-arrow')) {
        if(a.getClientRects().length && Math.abs(a.querySelector('svg').getBoundingClientRect().left-x)>.8)
          failures.push({kind:'disclosure alignment'});
      }
    }
    const search=document.querySelector('.guavajam-index-search');
    const header=document.querySelector('.guavajam-list-header');
    if(header) {
      const rule=header.parentElement.querySelector('.guavajam-list-divider');
      if(!rule || rule.getBoundingClientRect().top<header.getBoundingClientRect().bottom)
        failures.push({kind:'rule below heading and controls'});
      if(search) {
        const toggle=header.querySelector('#guavajam-search-toggle'), spinner=header.querySelector('.guavajam-search-progress');
        const heading=header.querySelector('h2'), total=header.querySelector('#guavajam-search-status');
        if(!toggle || !toggle.querySelector('svg.guavajam-icon-search') || !spinner ||
           spinner.previousElementSibling!==heading || spinner.nextElementSibling!==total ||
           !header.contains(toggle) || (!search.hidden &&
           (search.getBoundingClientRect().top<header.getBoundingClientRect().bottom ||
            search.getBoundingClientRect().bottom>rule.getBoundingClientRect().top)))
          failures.push({kind:'heading search and stable shared spinner'});
        if(toggle && toggle.getAttribute('aria-expanded')!==String(!search.hidden))
          failures.push({kind:'search expanded state'});
        if(document.querySelectorAll('.guavajam-search-spinner').length!==1)
          failures.push({kind:'one shared search spinner'});
      }
    }
    for(const code of document.querySelectorAll('.guavajam-citation-source code')) {
      const color=getComputedStyle(code).backgroundColor;
      // Forced colors may substitute Canvas RGB while preserving zero opacity.
      if(color!=='transparent' && !/^rgba\([^)]*,\s*0\)$/.test(color))
        failures.push({kind:'citation code background'});
    }
    const rule=document.createElement('span');rule.style.color='var(--guavajam-rule)';document.body.append(rule);
    const color=getComputedStyle(rule).color;rule.remove();
    for(const n of document.querySelectorAll('hr,.guavajam-navigation,.guavajam-record-list > li,footer'))
      if(getComputedStyle(n).borderTopStyle==='solid' && getComputedStyle(n).borderTopColor!==color ||
         getComputedStyle(n).borderBottomStyle==='solid' && getComputedStyle(n).borderBottomColor!==color)
        failures.push({kind:'decorative rule color'});
    return failures;
  });
  assert.deepEqual(report,[],'reader icons share a compact gap; disclosure edges, rules, and citation identities stay consistent');
}

export async function navigationCues(page) {
  await readerDetailLayout(page);
  await textLinkCues(page);
  const menu = page.locator('.guavajam-version-menu');
  if (await menu.count()) {
    const supporting = await menu.getAttribute('aria-label') === 'Document links';
    assert.equal(await menu.getAttribute('aria-label'), supporting ? 'Document links' : 'Version links');
    const stem = await page.evaluate(() => location.pathname.split('/').at(-1).replace(/\.html$/, ''));
    const tools = supporting
      ? [[stem + '.contents.html','list-tree','Contents'],[stem + '.metadata.html','info','Metadata']]
      : [['contents.html','list-tree','Contents'],['documents.html','files','Documents'],
         ['passages.html','book-open-text','Passages'],['../metadata.html','info','Metadata']];
    // Each supporting document has its own tools; both menus retain one GFM download.
    assert.equal(await menu.locator('a').count(), tools.length + 1);
    assert.equal(await menu.locator('a[download][type="text/markdown"]').count(), 1);
    for (const [href,name,label] of tools) {
      const link=menu.locator('a[href="'+href+'"]');
      assert.equal(await link.locator('svg.guavajam-icon-'+name+'[aria-hidden=true][focusable=false]').count(),1);
      assert.equal(await link.getAttribute('aria-label'),label);
      assert.equal((await link.textContent()).trim(),'Link');
    }
  }
  for (const summary of await page.locator('.guavajam-disclosure-summary:visible').all()) {
    assert.equal(await summary.locator('.guavajam-disclosure-arrow > svg.guavajam-icon-chevron-right[aria-hidden=true][focusable=false]').count(),1);
    const disclosure=summary.locator('..'), before=await disclosure.evaluate(n=>n.open);
    const caption=await summary.locator('.guavajam-disclosure-label').boundingBox();
    await page.mouse.click(caption.x+caption.width/2,caption.y+caption.height/2);
    assert.equal(await disclosure.evaluate(n=>n.open),before,'disclosure text never toggles the control');
  }
  for (const label of await page.locator('.guavajam-icon-label:visible').all()) {
    assert(await label.evaluate(n=>{
      const a=n.querySelector('a'), text=n.querySelector('.guavajam-link-label');
      const expected=n.closest('.guavajam-excerpt-label') || document.body;
      return !a.contains(text) && getComputedStyle(text).color===getComputedStyle(expected).color &&
        getComputedStyle(n).borderBottomStyle==='none';
    }),'only the leading icon links; adjacent text uses normal foreground');
  }
  for (const note of await page.locator('a.footnote-ref,a[role=doc-noteref]').all()) {
    assert.equal(await note.locator('svg').count(),0,'footnote superscripts retain their native appearance');
    assert.equal(await note.locator('sup').count(),1);
  }
  for (const back of await page.locator('a.footnote-back,a[role=doc-backlink]').all())
    assert.equal(await back.locator('svg').count(),0,'footnote return links retain their native glyph');
  for (const facet of await page.locator('.guavajam-facet:visible').all()) {
    assert(await facet.evaluate(n=>{
      const type=n.querySelector('.guavajam-facet-type'), button=n.querySelector('button'), value=n.querySelector('.guavajam-facet-value');
      const ink=getComputedStyle(document.body).color;
      const probe=document.createElement('span');probe.style.color='var(--guavajam-muted)';n.append(probe);
      const muted=getComputedStyle(probe).color;probe.remove();
      return getComputedStyle(value).color===ink && (button ?
        ['Tag','Subject'].includes(button.textContent.trim()) && !button.contains(value) :
        getComputedStyle(type).color===muted);
    }),'only active facet type words emphasize filtering; inactive TAG/SUBJECT use muted ink');
  }
  for (const quote of await page.locator('svg.guavajam-icon-quote:visible').all())
    assert(await quote.evaluate(n=>getComputedStyle(n).fill===getComputedStyle(n).color && getComputedStyle(n).stroke==='none'),
      'citation controls use the filled pinned quote silhouette');
  for (const heading of await page.locator('.guavajam-edition :is(h2,h3,h4,h5,h6)[id]').all()) {
    if (await heading.locator(':scope > .guavajam-permalink').count())
      assert(await heading.evaluate(n=>{
        const link=n.querySelector(':scope > .guavajam-permalink'),range=document.createRange();
        range.setStart(n,0);range.setEndBefore(link);
        return range.toString().trim().length>0 && n.lastElementChild.matches('.guavajam-permalink,.guavajam-passage-cite');
      }),'source heading text precedes its permalink and citation controls');
  }
  for (const control of await page.locator('#guavajam-search-prev:visible,#guavajam-search-next:visible').all()) {
    assert(await control.evaluate(n=>getComputedStyle(n).borderWidth==='0px'&&parseFloat(getComputedStyle(n).padding)===0),
      'search paging arrows have no button border or text-sized padding');
  }
}

export async function keyboardControls(page) {
  await iconLinkFallbacks(page);
  await navigationCues(page);
  await listControlLayout(page);
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
    const overflow=await page.evaluate(()=>({
      width:document.documentElement.scrollWidth,viewport:innerWidth,
      focus:{tag:document.activeElement.tagName,classes:document.activeElement.className,id:document.activeElement.id},
      outside:[...document.querySelectorAll('body *')].filter(n=>n.getClientRects().length).map(n=>({n,b:n.getBoundingClientRect()}))
        .filter(({b})=>b.right>innerWidth+1).slice(0,8).map(({n,b})=>({tag:n.tagName,classes:n.className?.baseVal??n.className,right:b.right}))
    }));
    assert(overflow.width <= overflow.viewport + 1, 'Keyboard focus must keep the page horizontally contained: '+JSON.stringify(overflow));
    seen.add(state.index);
  }
  assert.equal(seen.size, count);
  return count;
}

export async function hoverControls(page) {
  for (const selector of ['.guavajam-passage-cite', '.guavajam-citation > a', '.guavajam-record-link',
    '#guavajam-search-toggle', '.guavajam-disclosure-arrow', '.guavajam-message-detail > summary', '#guavajam-search button:not(:disabled)']) {
    const target = page.locator(selector).first();
    if (!await target.isVisible()) continue;
    await page.evaluate(() => document.activeElement?.blur());
    await page.mouse.move(0, 0);
    const paint = async () => target.evaluate(n => {
      const s = getComputedStyle(n), label = getComputedStyle(n.querySelector('span') || n);
      return [s.color, s.backgroundColor, s.boxShadow, label.textDecorationLine, label.textDecorationThickness, s.outlineStyle, s.outlineWidth];
    });
    if (selector === '.guavajam-passage-cite') {
      assert.equal(await target.locator('svg.guavajam-icon-quote[aria-hidden=true]').count(), 1);
      assert.match(await target.getAttribute('aria-label'), /^Cite committed source passage /);
      assert.equal(await target.textContent(), 'Cite');
      assert(await target.evaluate(n => Number(getComputedStyle(n).fontWeight) >= 600));
    }
    const before = await paint(); await target.hover();
    const after = await paint();
    // Forced colors belong to the user/UA, which may map link and hover ink
    // to the same system color. Keep its palette and test keyboard focus too.
    if (await page.evaluate(() => matchMedia('(forced-colors: active)').matches)) {
      assert.notEqual(await target.evaluate(n => getComputedStyle(n).getPropertyValue('forced-color-adjust')), 'none',
        'Keep system colors under browser control, including engines without this CSS property');
    } else {
      assert.notEqual(after[0], before[0], `Hover brightens the foreground: ${selector}`);
    }
    assert.deepEqual(after.slice(1), before.slice(1), `Hover changes only the foreground: ${selector}`);
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
    const state = {toggle: bounds(document.querySelector('#guavajam-search-toggle')),
      heading: bounds(document.querySelector('#guavajam-works-heading')),
      total: bounds(document.querySelector('#guavajam-search-status')),
      spinnerSlot: bounds(document.querySelector('#guavajam-search-spinner').parentElement)};
    if (!initial) state.button = bounds(document.querySelector('#guavajam-search button[type=submit]'));
    return state;
  }, initial);
}

// A text hit is useful only when the reader can see why it matched. This is
// shared by Works, Versions, Documents and Passages, including historical hits.
export async function searchEvidence(page) {
  await textLinkCues(page);
  for(const label of await page.locator('.guavajam-excerpt-label:visible').all()) {
    if((await label.textContent()).trim()!=='LinkDocument text match') continue;
    const href=await label.locator('a').getAttribute('href');
    assert(new URL(href,page.url()).hash.length>1,'document-body evidence links to a passage or native text directive');
  }
  for (const row of await page.locator('#guavajam-search-results > li').all()) {
    if (await page.locator('form[data-directory=documents]').count()) {
      assert(await row.locator('h3 mark, .guavajam-abstract mark').count() > 0,
        'document text hits explain themselves in their visible title or abstract');
      assert.equal(await row.locator('.guavajam-search-excerpt, .guavajam-meta').count(), 0);
      continue;
    }
    const evidence = row.locator('.guavajam-search-excerpt');
    assert.equal(await evidence.count(), 1, 'each text result explains its exact winning record');
    assert.match(await evidence.locator('.guavajam-excerpt-label .guavajam-link-label').textContent(),
      /^(Title|Author|Description|Tag or subject|Publication metadata|Document text|Passage) match$/);
    assert(await evidence.locator('.guavajam-passage-excerpt mark').count() > 0,
      'even body-only, historical and provenance-only matches show highlighted evidence');
    assert((await evidence.locator('.guavajam-passage-excerpt').textContent()).length <= 322,
      'match evidence stays bounded near the matched text');
    const destination=evidence.locator('.guavajam-excerpt-label a');
    if (await destination.count()) {
      assert.equal(await destination.locator('svg.guavajam-icon-link').count(),1);
      assert.equal(await destination.getAttribute('aria-label'),
        await evidence.locator('.guavajam-excerpt-label .guavajam-link-label').textContent());
      assert(await destination.getAttribute('href'));
    }
  }
}

export async function recordLinkAlignment(page) {
  for (const row of await page.locator('.guavajam-works:visible > li').all()) {
    assert.equal(await row.locator('h3 .guavajam-record-link').count(), 0);
    const link = row.locator('.guavajam-byline > .guavajam-record-link');
    assert.equal(await link.count(), 1, 'each work retains a leading byline history control');
    assert(await link.evaluate(n => n.parentElement.firstElementChild === n &&
      !n.previousSibling?.textContent.trim()), 'the history icon precedes date and author text');
    assert(await link.evaluate(n => {
      const date=n.parentElement.querySelector(':scope > time');
      return !date || n.nextElementSibling === date;
    }), 'the publication date precedes author text when available');
    assert(await link.evaluate(n => {
      const title=n.closest('article').querySelector('h3 .guavajam-icon-control svg').getBoundingClientRect();
      const artwork=n.querySelector('svg').getBoundingClientRect();
      return Math.abs(artwork.left + n.parentElement.scrollLeft - title.left - n.closest('article').querySelector('h3').scrollLeft) < 1;
    }), 'the leading history artwork aligns with the title edge');
  }
}

export async function listControlLayout(page) {
  await recordLinkAlignment(page);
  const field = page.locator('form#guavajam-search input[name=q]');
  if (await field.isVisible()) {
    const input = await field.boundingBox();
    const button = await page.locator('form#guavajam-search button[type=submit]').boundingBox();
    assert(Math.abs(input.y + input.height / 2 - button.y - button.height / 2) < 1 &&
      button.x > input.x + input.width, 'search input and right-hand submit share one centered row');
  }
  for (const pager of await page.locator('.guavajam-pagination:visible').all()) {
    assert(await pager.evaluate(n=>n.children.length===3 &&
      n.firstElementChild.getAttribute('aria-label')==='Previous page' &&
      n.lastElementChild.getAttribute('aria-label')==='Next page' &&
      /^Page \d+ of \d+$/.test(n.children[1].textContent)),
      'static and enhanced lists share Previous / Page N of M / Next');
    const centered = await pager.evaluate(n => {
      const bounds = n.getBoundingClientRect(), rows = new Map();
      for (const child of n.children) {
        const box = child.getBoundingClientRect(), key = Math.round(box.top + box.height / 2);
        const row = rows.get(key) || [];
        row.push(box); rows.set(key, row);
      }
      return [...rows.values()].every(row => Math.abs(
        Math.min(...row.map(b => b.left)) + Math.max(...row.map(b => b.right)) - bounds.left - bounds.right) < 2);
    });
    assert(centered, 'static and searched paging controls are horizontally centered');
  }
  for (const header of await page.locator('.guavajam-list-header:visible').all()) {
    const inline=await header.evaluate(n => {
      const a=n.querySelector('h2').getBoundingClientRect(), b=n.querySelector('p').getBoundingClientRect();
      return b.left >= a.right && b.top < a.bottom && a.top < b.bottom &&
        getComputedStyle(n).overflowX === 'auto' && getComputedStyle(n).whiteSpace === 'nowrap';
    });
    assert(inline, 'list heading and status share one horizontal scroll lane');
    assert(await header.evaluate(n=>{
      const heading=n.querySelector('h2'),status=n.querySelector('p');
      return heading.textContent===heading.dataset.staticHeading && /^\d+ total$/.test(status.textContent);
    }),'list identity stays stable; its adjacent status reports only the current total');

  }
  for (const control of await page.locator('.guavajam-pagination a[rel], .guavajam-pagination button').all()) {
    const previous=await control.evaluate(n=>n.parentElement.firstElementChild===n);
    assert.equal(await control.getAttribute('aria-label'), previous ? 'Previous page' : 'Next page');
    assert.equal(await control.locator('svg.guavajam-icon-'+(previous ? 'arrow-left' : 'arrow-right')+'[aria-hidden=true][focusable=false]').count(),1);
    if (await control.evaluate(n=>n.matches(':disabled,[aria-disabled=true]'))) {
      assert(await control.evaluate(n=>{
        const p=document.createElement('span');p.style.color='var(--guavajam-muted)';n.parentElement.append(p);
        const muted=getComputedStyle(p).color;p.remove();
        return getComputedStyle(n).color===muted && getComputedStyle(n).opacity==='1';
      }),'disabled paging uses readable muted ink rather than dimmed emphasis');
    }
  }
  const footer=page.locator('footer');
  if (await footer.count() && (await footer.textContent()).includes('a-guavajam-static-website-generator')) {
    const link=footer.getByRole('link',{name:'a-guavajam-static-website-generator',exact:true});
    assert.equal(await link.getAttribute('href'),'https://github.com/nashspence/guavajam');
    assert.equal(await link.locator('svg.guavajam-icon-external-link[aria-hidden=true]').count(),1);
  }
  for (const link of await page.locator('.guavajam-editions:visible a[href*="/metadata.html#provenance"]').all()) {
    assert.match(await link.getAttribute('aria-label'), /^Metadata for v[0-9]+$/);
    assert.equal(await link.locator('svg.guavajam-icon-info[aria-hidden=true][focusable=false]').count(), 1);
    assert.equal(await link.locator('.guavajam-icon-fallback').textContent(), 'Metadata');
  }
}

export async function listHeadingOverflow(page) {
  const header=page.locator('.guavajam-list-header'), status=header.locator('#guavajam-search-status');
  const saved=await status.textContent();
  const feed=header.locator('.guavajam-feed-link');
  if(await feed.count()) await feed.focus();
  try {
    await status.evaluate(n=>n.textContent='9'.repeat(320)+' total');
    assert(await header.evaluate(n=>n.scrollWidth > n.clientWidth && getComputedStyle(n).scrollbarWidth === 'none'));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth + 1));
    await header.focus();await header.press('ArrowRight');await page.waitForTimeout(100);
    assert(await header.evaluate(n=>n.scrollLeft>0),'long list counts are keyboard-scrollable');
    await page.emulateMedia({media:'print'});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth + 1),
      'long list counts wrap within the printed column');
    await page.emulateMedia({media:'screen'});
  } finally {
    await page.emulateMedia({media:'screen'});
    await status.evaluate((n,text)=>n.textContent=text,saved);
    await header.evaluate(n=>n.scrollLeft=0);
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

export async function linkPalette(page) {
  const original = await page.evaluate(() => ({style: document.documentElement.getAttribute('style'),
    x: scrollX, y: scrollY, scheme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'}));
  await page.evaluate(() => {
    const link = document.createElement('a');link.href = '#link-color-contract';link.id = 'link-color-contract';
    link.textContent = 'Readable link';document.body.append(link);
  });
  const probe = page.locator('#link-color-contract');
  try {
    const native = await page.evaluate(() => getComputedStyle(document.documentElement)
      .getPropertyValue('--guavajam-accent-source').trim() !== '');
    for (const colorScheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme});
      for (const accent of native ? [null, '#000', '#fff', '#ff0', '#0f0', '#f00', '#00f', '#f0f'] : [null]) {
        await page.evaluate(accent => {
          if (accent) document.documentElement.style.setProperty('--guavajam-accent-source', accent);
          else document.documentElement.style.removeProperty('--guavajam-accent-source');
        }, accent);
        await page.mouse.move(0, 0);
        const state = () => probe.evaluate(n => {
          // Canvas resolves CSS system/relative colors to display RGB too.
          const canvas = document.createElement('canvas');canvas.width = canvas.height = 1;
          const ctx = canvas.getContext('2d', {willReadFrequently: true});
          const luminance = color => {
            ctx.clearRect(0,0,1,1);ctx.fillStyle = color;ctx.fillRect(0,0,1,1);
            const rgb = [...ctx.getImageData(0,0,1,1).data].slice(0,3).map(v => {
              const c = v / 255;return c <= .04045 ? c / 12.92 : ((c+.055)/1.055)**2.4;
            });return rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722;
          };
          const style = getComputedStyle(n), fg = luminance(style.color), bg = luminance(getComputedStyle(document.body).backgroundColor);
          return {color: style.color, luminance: fg, contrast: (Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05),
            weight: Number(style.fontWeight), decoration: style.textDecorationLine,
            background: style.backgroundColor, shadow: style.boxShadow};
        });
        const before = await state();await probe.hover();const after = await state();
        assert(before.weight >= 600 && before.decoration === 'none', 'weight identifies links without underlines');
        assert(before.contrast >= 4.5 && after.contrast >= 4.5,
          `${colorScheme}/${accent || 'browser'}: readable emphasis before/after hover: ${JSON.stringify({before,after})}`);
        assert(after.luminance >= before.luminance - .001, 'hover only brightens foreground ink');
        assert.equal(after.background, before.background);assert.equal(after.shadow, before.shadow);
      }
    }
  } finally {
    await probe.evaluate(n => n.remove());
    await page.evaluate(original => {
      if (original.style === null) document.documentElement.removeAttribute('style');
      else document.documentElement.setAttribute('style',original.style);
      scrollTo(original.x,original.y);
    }, original);
    await page.emulateMedia({colorScheme: original.scheme});
  }
}
