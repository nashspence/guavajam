import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {chromium, firefox, webkit} from 'playwright';
import {assertAccessible, reviewSummary} from './browser_accessibility.mjs';

const sites = JSON.parse(process.env.PRESS_SITE_TESTS || 'null');
assert.ok(sites, 'Run make test-browser to serve the real generated publisher pages');
assert.deepEqual(sites.map(site => site.state), ['published', 'corrected', 'withdrawn']);

async function readerContext(engine, site, width, colorScheme) {
  const browser = await engine.launch();
  const context = await browser.newContext({viewport: {width, height: 900}, colorScheme, acceptDownloads: true});
  const unexpected = [];
  const origin = new URL(site.recordURL).origin;
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === origin) {
      // Canonical links keep their public origin; delivery stays on the real
      // loopback preview server, with no external publication or network access.
      const response = await context.request.get(site.base + url.pathname + url.search, {maxRedirects: 0});
      await route.fulfill({response});
    } else if (url.origin === site.base || url.protocol === 'data:') {
      await route.continue();
    } else {
      unexpected.push(url.href);
      await route.abort();
    }
  });
  return {browser, context, unexpected};
}

async function keyboardFollow(page, locator) {
  for (let n = 0; n < 50 && !await locator.evaluate(el => el === document.activeElement); n++) {
    await page.keyboard.press('Tab');
  }
  assert.equal(await locator.evaluate(el => el === document.activeElement), true, 'Link cannot be reached with Tab');
  assert.equal(await locator.evaluate(el => el.matches(':focus-visible')), true);
  await page.keyboard.press('Enter');
}

function noPageOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
}

async function settledScroll(box) {
  let previous = NaN, stable = 0;
  for (let n = 0; n < 40; n++) {
    const current = await box.evaluate(el => el.scrollLeft);
    stable = Math.abs(current - previous) < .25 ? stable + 1 : 0;
    if (stable >= 3) return current;
    previous = current;
    await box.page().waitForTimeout(50);
  }
  throw new Error('Keyboard scrolling did not settle');
}

for (const [name, engine] of Object.entries({chromium, firefox, webkit})) {
  test(`${name}: work, record, reader and current notices form a navigable browser journey`, {timeout: 180000}, async () => {
    for (const width of [320, 1280]) for (const colorScheme of ['light', 'dark']) for (const site of sites) {
      const {browser, context, unexpected} = await readerContext(engine, site, width, colorScheme);
      try {
        const page = await context.newPage();
        await page.goto(site.base + site.work);
        assert.equal(await page.getByRole('main').count(), 1);
        assert.equal(await page.getByRole('heading', {level: 1}).count(), 1);
        assert.equal(await noPageOverflow(page), true);
        const expectedStatus = site.state === 'withdrawn' ? 'Withdrawn' : 'Published';
        assert.equal(await page.locator('.press-record-list strong').textContent(), expectedStatus);
        if (site.state === 'withdrawn') assert.equal(await page.getByText('All editions of this work have been withdrawn.', {exact: true}).count(), 1);
        await keyboardFollow(page, page.locator('.press-record-list a').first());
        await page.waitForURL(url => url.pathname === site.record);
        assert.equal(await page.locator('.press-record-facts strong').textContent(), expectedStatus);
        assert.equal(await noPageOverflow(page), true);
        if (site.state !== 'published') {
          assert.equal(await page.getByRole('heading', {name: 'Editorial notices', exact: true}).count(), 1);
          assert.equal(await page.getByText(/Correction: The notation example remains illustrative/).count(), 1);
        }
        if (site.state === 'withdrawn') assert.equal(await page.getByText(/Withdrawal: Retained only as an archived example/).count(), 1);
        const read = page.locator('#press-record-read');
        assert.equal(await read.textContent(), site.state === 'withdrawn' ? 'Read the archived edition' : 'Read this edition online');
        const [readerResponse] = await Promise.all([
          page.waitForResponse(response => new URL(response.url()).pathname === site.record + '/read'),
          keyboardFollow(page, read),
        ]);
        await page.waitForURL(url => url.pathname === site.record + '/read');
        assert.equal(createHash('sha256').update(await readerResponse.body()).digest('hex'), site.htmlSHA256);
        const identity = page.locator('article > h1 + .press-edition-identity');
        assert.equal(await identity.count(), 1, 'Edition identity must follow the title');
        assert.equal(await identity.locator('[data-press-field="byline"]').textContent(), 'Nash Spence');
        assert.equal(await identity.locator('[data-press-field="edition-version"]').textContent(),
          'Edition ' + site.record.split('/').at(-1));
        assert.equal(await identity.isVisible(), true);
        assert.equal(await noPageOverflow(page), true);
        await keyboardFollow(page, page.locator('#press-edition-status'));
        await page.waitForURL(url => url.origin === new URL(site.recordURL).origin && url.pathname === site.record);
        assert.equal(await page.locator('.press-record-facts strong').textContent(), expectedStatus);
        await keyboardFollow(page, page.getByRole('link', {name: 'All editions of this work', exact: true}));
        await page.waitForURL(url => url.pathname === site.work);
        await page.goBack();
        await page.waitForURL(url => url.pathname === site.record);
        assert.equal(await page.getByRole('heading', {level: 1}).count(), 1);
        assert.deepEqual(unexpected, []);
      } finally { await browser.close(); }
    }
  });

  test(`${name}: wide tables, mathematics and code pan by keyboard without trapping focus`, {timeout: 90000}, async () => {
    const site = sites[0];
    for (const colorScheme of ['light', 'dark']) {
      const {browser, context, unexpected} = await readerContext(engine, site, 320, colorScheme);
      try {
        const page = await context.newPage();
        await page.goto(site.base + site.record + '/read');
        const containers = page.locator('.press-scroll, pre');
        const covered = new Set();
        for (const box of await containers.all()) {
          if (!await box.evaluate(el => el.scrollWidth > el.clientWidth + 1)) continue;
          const kind = await box.evaluate(el => el.tagName === 'PRE' ? 'code' : el.classList.contains('press-table-scroll') ? 'table' : 'math');
          covered.add(kind);
          await box.focus();
          await page.keyboard.press('Shift+Tab');
          await page.keyboard.press('Tab');
          assert.equal(await box.evaluate(el => el === document.activeElement), true);
          assert.equal(await box.evaluate(el => el.matches(':focus-visible')), true);
          assert.ok(await box.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth)) >= 2);
          const minimum = await box.evaluate(el => Math.min(5, (el.scrollWidth - el.clientWidth) / 2));
          for (let n = 0; n < 5; n++) await page.keyboard.press('ArrowRight');
          await page.waitForFunction(({el, minimum}) => el.scrollLeft > minimum,
            {el: await box.elementHandle(), minimum}, {timeout: 5000}).catch(async error => {
              const geometry = await box.evaluate(el => ({left: el.scrollLeft, width: el.clientWidth,
                content: el.scrollWidth, focused: el === document.activeElement}));
              throw new Error(`${name} ${kind} did not pan right: ${JSON.stringify(geometry)}`, {cause: error});
            });
          const right = await settledScroll(box);
          for (let n = 0; n < 10; n++) await page.keyboard.press('ArrowLeft');
          await page.waitForFunction(({el, right}) => el.scrollLeft < right,
            {el: await box.elementHandle(), right}, {timeout: 5000}).catch(async error => {
              const geometry = await box.evaluate(el => ({left: el.scrollLeft, width: el.clientWidth,
                content: el.scrollWidth, focused: el === document.activeElement}));
              throw new Error(`${name} ${kind} did not pan left from ${right}: ${JSON.stringify(geometry)}`, {cause: error});
            });
          await settledScroll(box);
          assert.equal(await noPageOverflow(page), true);
          await page.keyboard.press('Tab');
          assert.equal(await box.evaluate(el => el.contains(document.activeElement)), false);
        }
        assert.deepEqual([...covered].sort(), ['code', 'math', 'table']);
        assert.deepEqual(unexpected, []);
      } finally { await browser.close(); }
    }
  });

  test(`${name}: generated work, record and reader pass local accessibility scans`, {timeout: 240000}, async () => {
    const review = new Map();
    for (const colorScheme of ['light', 'dark']) for (const site of sites) {
      const {browser, context, unexpected} = await readerContext(engine, site, 375, colorScheme);
      try {
        const page = await context.newPage();
        for (const route of [site.work, site.record, site.record + '/read']) {
          await page.goto(site.base + route);
          for (const finding of await assertAccessible(page)) review.set(JSON.stringify(finding), finding);
        }
        assert.deepEqual(unexpected, []);
      } finally { await browser.close(); }
    }
    if (review.size) console.info(`${name}: axe findings needing review: ${JSON.stringify(reviewSummary([...review.values()]))}`);
  });
}
