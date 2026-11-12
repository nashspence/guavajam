// Internal browser half of qualify_edition.py. Input is a verified candidate;
// every observation is made against those file bytes, never a rebuilt edition.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {readFile, rename, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromium} from 'playwright';
import {assertAccessible, reviewSummary} from '../tests/browser_accessibility.mjs';

const PAPERS = {A4: {width: 210, height: 297}, Letter: {width: 215.9, height: 279.4}};

export function assertCandidateAccessible(page) {
  // axe's heading-order rule flattens quotations into the authorial outline.
  // inspectStructure checks the intentional independent quotation scopes;
  // all other rules, and the shared reader tests' defaults, remain enabled.
  return assertAccessible(page, {rules: {'heading-order': {enabled: false}}});
}

export async function inspectStructure(page, canonicalURL) {
  return page.evaluate(canonicalURL => {
    const failures = [], ids = new Set(), article = document.querySelector('article.press-edition');
    if (!article || document.querySelectorAll('main').length !== 1) throw new Error('Candidate needs one main landmark and its edition article');
    for (const node of document.querySelectorAll('[id]')) {
      if (!node.id || ids.has(node.id)) failures.push('Duplicate or empty fragment identifier: ' + node.id);
      ids.add(node.id);
    }
    const headings = [...article.querySelectorAll('h1,h2,h3,h4,h5,h6')];
    if (headings.filter(node => node.tagName === 'H1').length !== 1 || headings[0]?.tagName !== 'H1') failures.push('The edition must begin with its single H1 title');
    let outerLevel = 0, supporting = false;
    const turnLevels = new Map();
    for (const heading of headings) {
      const level = Number(heading.tagName[1]), turn = heading.closest('figure.press-turn');
      const label = heading.id || heading.textContent.trim().slice(0, 80);
      if (turn) {
        // Each attributed response has its own outline below its supporting
        // record's H2. Leaving a quotation restores the authorial outline.
        const previous = turnLevels.get(turn) ?? 2;
        if (!supporting || level < 3 || level > previous + 1) failures.push('Skipped heading level in attributed quotation at ' + label);
        turnLevels.set(turn, level);
      } else {
        if (heading.classList.contains('press-record-heading')) supporting = true;
        if (level > outerLevel + 1) failures.push('Skipped authorial heading level at ' + label);
        outerLevel = level;
      }
    }
    let fragments = 0;
    for (const link of document.querySelectorAll('a[href]')) {
      const href = link.getAttribute('href');
      const url = new URL(href, document.URL), canonical = new URL(canonicalURL);
      if (!href.startsWith('#') && !(url.origin === canonical.origin && url.pathname === canonical.pathname && url.hash)) continue;
      fragments++;
      let id;
      try { id = decodeURIComponent(url.hash.slice(1)); } catch { failures.push('Malformed fragment URL'); continue; }
      if (!ids.has(id)) failures.push('Missing fragment destination: ' + id);
    }
    const candidates = [...article.querySelectorAll('h2[id],h3[id],figure.press-turn[id],.press-supporting-element[id]')];
    const targets = [...new Set([candidates[0]?.id, candidates[Math.floor(candidates.length / 2)]?.id, candidates.at(-1)?.id].filter(Boolean))];
    // Inventory visible MathML leaves, excluding TeX annotation/fallback text.
    const mathTokens = [...new Set([...article.querySelectorAll('math mi,math mn,math mo,math mtext,math ms')]
      .map(node => node.textContent.trim()).filter(Boolean))];
    return {failures, headings: headings.length, fragments, targets, math_tokens: mathTokens,
      mathematics: article.querySelectorAll('math').length};
  }, canonicalURL);
}

export async function inspectScreen(page) {
  return page.evaluate(() => {
    const failures = [], samples = {}, containers = [...document.querySelectorAll('.press-scroll,pre')];
    const headings = [...document.querySelectorAll('article :is(h1,h2,h3,h4,h5,h6)[id]')];
    const label = node => node.closest('.press-supporting-element[id],figure.press-turn[id]')?.id ||
      headings.findLast(heading => heading.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)?.id || node.tagName.toLowerCase();
    if (document.documentElement.scrollWidth > innerWidth + 1) {
      const wide = [...document.querySelectorAll('article :is(p,math,table,pre,img)')].filter(node => {
        if (node.closest('.press-scroll,pre') && !node.matches('.press-scroll,pre')) return false;
        const box = node.getBoundingClientRect();
        return box.right > innerWidth + 1 || box.left < -1;
      }).slice(0, 5).map(node => label(node) + '/' + node.tagName.toLowerCase());
      failures.push('Page overflows the viewport horizontally' + (wide.length ? ' near ' + wide.join(', ') : ''));
    }
    let overflowing = 0;
    for (const [index, node] of containers.entries()) {
      if (node.scrollWidth <= node.clientWidth + 1) continue;
      overflowing++;
      const target = label(node);
      if (node.tabIndex < 0 || !['auto', 'scroll'].includes(getComputedStyle(node).overflowX)) failures.push('Wide content is not keyboard scrollable at ' + target);
      const kind = node.tagName === 'PRE' ? 'code' : node.classList.contains('press-table-scroll') ? 'table' : 'math';
      if (!samples[kind] || node.scrollWidth - node.clientWidth > samples[kind].overflow) samples[kind] = {
        index, label: target, overflow: node.scrollWidth - node.clientWidth, direction: getComputedStyle(node).direction};
    }
    return {failures, containers: containers.length, overflowing, samples};
  });
}

export async function inspectPrint(page, contentWidth) {
  return page.evaluate(contentWidth => {
    const failures = [];
    const headings = [...document.querySelectorAll('article :is(h1,h2,h3,h4,h5,h6)[id]')];
    for (const [index, node] of [...document.querySelectorAll('article math')].entries()) {
      const box = node.getBoundingClientRect(), label = node.closest('.press-supporting-element[id],figure.press-turn[id]')?.id ||
        headings.findLast(heading => heading.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)?.id || 'mathematics ' + (index + 1);
      if (!node.getClientRects().length || box.width === 0 || box.height === 0) failures.push('Mathematics is hidden in print at ' + label);
      if (box.left < -2 || box.right > contentWidth + 2) failures.push('Mathematics exceeds printable width at ' + label + '; split the equation in the reviewed source');
    }
    if (document.documentElement.scrollWidth > contentWidth + 2) failures.push('Printed content exceeds the paper content width; inspect wide mathematics, tables or code');
    return {failures, content_width: contentWidth, mathematics: document.querySelectorAll('article math').length};
  }, contentWidth);
}

async function checkKeyboard(page, samples) {
  const checked = [];
  for (const [kind, sample] of Object.entries(samples)) {
    const node = page.locator('.press-scroll,pre').nth(sample.index);
    await node.focus();
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    assert.equal(await node.evaluate(el => document.activeElement === el), true, 'Cannot focus wide ' + kind + ' at ' + sample.label);
    assert.notEqual(await node.evaluate(el => getComputedStyle(el).outlineStyle), 'none', 'Wide ' + kind + ' has no visible focus');
    const initial = await node.evaluate(el => el.scrollLeft);
    const key = sample.direction === 'rtl' ? 'ArrowLeft' : 'ArrowRight';
    for (let n = 0; n < 4; n++) await page.keyboard.press(key);
    await page.waitForFunction(({el, initial}) => Math.abs(el.scrollLeft - initial) > 1,
      {el: await node.elementHandle(), initial}, {timeout: 3000});
    await page.keyboard.press('Tab');
    // An in-table link is a legitimate next stop. Require progress, not an
    // artificial jump past all descendants of a semantic table.
    assert.equal(await node.evaluate(el => document.activeElement === el), false, 'Wide ' + kind + ' traps Tab focus');
    checked.push({kind, target: sample.label});
  }
  return checked;
}

export async function checkTargets(page, targets) {
  const checked = [];
  for (const id of targets) {
    const uri = page.url().split('#')[0];
    // A hash assignment is a no-op when that fragment is already selected.
    // Navigate from an empty fragment and await the browser's native navigation
    // before measuring. These same-document moves retain enlarged-text styles.
    if (page.url() !== uri + '#') await page.goto(uri + '#', {waitUntil: 'load'});
    await page.goto(uri + '#' + encodeURIComponent(id), {waitUntil: 'load'});
    await page.waitForFunction(id => {
      const box = document.getElementById(id).getBoundingClientRect();
      const status = document.querySelector('.press-status')?.getBoundingClientRect().bottom ?? 0;
      return box.top >= status - 1 && box.top < innerHeight && box.bottom > status;
    }, id, {timeout: 3000}).catch(() => {});
    const result = await page.evaluate(id => {
      const node = document.getElementById(id), box = node.getBoundingClientRect();
      const status = document.querySelector('.press-status')?.getBoundingClientRect();
      return {top: box.top, bottom: box.bottom, status: status?.bottom ?? 0, height: innerHeight,
        scroll_y: scrollY, scroll_height: document.documentElement.scrollHeight, width: innerWidth,
        color_scheme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
        font_size: getComputedStyle(document.documentElement).fontSize,
        active_element: document.activeElement?.id || document.activeElement?.tagName,
        fragment: location.hash};
    }, id);
    assert.ok(result.top >= result.status - 1 && result.top < result.height && result.bottom > result.status,
      'Fragment destination is obscured at ' + id + ': ' + JSON.stringify(result));
    checked.push({target: id, ...result});
  }
  return checked;
}

async function prepareScreen(page, uri, scenario, phase = async () => {}) {
  await phase(scenario.label + ': load');
  await page.setViewportSize({width: scenario.width, height: 900});
  await page.emulateMedia({media: 'screen', colorScheme: scenario.color});
  await page.goto(uri);
  await phase(scenario.label + ': fonts');
  await page.evaluate(() => document.fonts.ready);
  if (scenario.enlarged) {
    const size = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));
    await page.addStyleTag({content: `html {font-size:${size * 2}px !important;letter-spacing:.12em !important;word-spacing:.16em !important} * {line-height:1.5 !important} p {margin-block-end:2em !important}`});
  }
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

export async function checkScreenTargets(page, uri, scenario, targets) {
  const checked = [];
  // Qualify external fragment entry independently of the keyboard-panning
  // session's focus, scroll position and pending native scroll restoration.
  for (const id of targets) {
    const entry = await page.context().newPage();
    try {
      await prepareScreen(entry, uri, scenario);
      checked.push(...await checkTargets(entry, [id]));
    } finally { await entry.close(); }
  }
  return checked;
}

export async function qualifyCandidate(request, {engine = chromium} = {}) {
  const raw = await readFile(request.file);
  assert.equal(createHash('sha256').update(raw).digest('hex'), request.html_sha256, 'Candidate HTML differs from its verified digest');
  const uri = pathToFileURL(request.file).href;
  const report = {schema: 'press.candidate-browser.v1', html_sha256: request.html_sha256, provenance_sha256: request.provenance_sha256,
    engine: 'chromium', failures: [], screens: {}, print: {}, math_tokens: []};
  const phase = async name => {
    // Leave a small, candidate-bound checkpoint even if the parent terminates
    // a stalled browser. This evidence stays outside the reviewed package.
    const progress = path.join(request.output, 'progress.json');
    await writeFile(progress + '.tmp', JSON.stringify({schema: 'press.candidate-progress.v1',
      html_sha256: request.html_sha256, provenance_sha256: request.provenance_sha256, phase: name}) + '\n');
    await rename(progress + '.tmp', progress);
  };
  await phase('launch browser');
  const browser = await engine.launch({headless: true});
  report.browser_version = browser.version();
  const attempt = async (label, operation) => {
    try { return await operation(); } catch (error) { report.failures.push(label + ': ' + String(error.message).slice(0, 1800)); }
  };
  // The verified edition's CSP blocks document scripts. Keep the JS engine
  // available for trusted DevTools instrumentation: axe and Playwright polling
  // need asynchronous callbacks, which javaScriptEnabled:false also disables.
  const context = await browser.newContext({offline: true, bypassCSP: false,
    serviceWorkers: 'block', viewport: {width: 1280, height: 900}});
  context.setDefaultTimeout(10000);
  const unexpected = [];
  await context.route('**/*', async route => {
    const url = route.request().url();
    if (url.startsWith('data:') || url.split('#')[0] === uri) await route.continue();
    else { unexpected.push(url); await route.abort(); }
  });
  const page = await context.newPage();
  const errors = [];
  context.on('page', candidate => candidate.on('pageerror', error => errors.push(error.message)));
  page.on('pageerror', error => errors.push(error.message));
  try {
    await phase('open candidate');
    await page.goto(uri, {waitUntil: 'load', timeout: 30000});
    await phase('inspect structure');
    const structure = await inspectStructure(page, request.canonical_url);
    report.structure = structure;
    report.math_tokens = structure.math_tokens;
    report.failures.push(...structure.failures);
    for (const scenario of [
      {label: 'desktop', width: 1280, color: 'light'},
      {label: 'mobile', width: 320, color: 'light'},
      {label: 'mobile-dark', width: 320, color: 'dark'},
      {label: 'mobile-enlarged', width: 320, color: 'light', enlarged: true},
    ]) await attempt(scenario.label, async () => {
      await prepareScreen(page, uri, scenario, phase);
      await phase(scenario.label + ': reflow');
      const result = await inspectScreen(page);
      report.screens[scenario.label] = result;
      report.failures.push(...result.failures.map(message => scenario.label + ': ' + message));
      await phase(scenario.label + ': keyboard');
      result.keyboard_checked = await checkKeyboard(page, result.samples);
      await phase(scenario.label + ': fragments');
      result.fragments_checked = await checkScreenTargets(page, uri, scenario, structure.targets);
      if (scenario.label === 'mobile') {
        await phase(scenario.label + ': accessibility');
        result.accessibility_review = reviewSummary(await assertCandidateAccessible(page));
      }
      await page.evaluate(() => { location.hash = ''; window.scrollTo(0, 0); });
      // Retain a bounded first-screen proof; full-page captures can be enormous.
      await phase(scenario.label + ': screenshot');
      await page.screenshot({path: path.join(request.output, scenario.label + '.png')});
    });
    for (const [paper, size] of Object.entries(PAPERS)) await attempt(paper, async () => {
      await phase(paper + ': load');
      const width = Math.floor((size.width - 24) * 96 / 25.4);
      await page.setViewportSize({width, height: 900});
      await page.emulateMedia({media: 'print', colorScheme: 'light'});
      await page.goto(uri);
      await phase(paper + ': fonts');
      await page.evaluate(() => document.fonts.ready);
      await phase(paper + ': print geometry');
      const result = await inspectPrint(page, width);
      report.print[paper] = result;
      report.failures.push(...result.failures.map(message => paper + ': ' + message));
      await phase(paper + ': PDF');
      await page.pdf({path: path.join(request.output, paper + '.pdf'), format: paper, scale: 1,
        printBackground: true, displayHeaderFooter: false, preferCSSPageSize: false,
        margin: {top: '12mm', right: '12mm', bottom: '12mm', left: '12mm'}, timeout: 30000});
    });
    if (unexpected.length) report.failures.push('Candidate attempted ' + unexpected.length + ' unexpected network/resource requests');
    if (errors.length) report.failures.push('Candidate browser errors: ' + errors.slice(0, 3).join('; '));
  } finally {
    await phase('close browser');
    await context.close(); await browser.close();
  }
  await phase('complete');
  return report;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const request = JSON.parse(readFileSync(0, 'utf8'));
  try {
    const report = await qualifyCandidate(request);
    await writeFile(path.join(request.output, 'browser.json'), JSON.stringify(report, null, 2) + '\n');
    process.exitCode = report.failures.length ? 1 : 0;
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
