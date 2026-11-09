import assert from 'node:assert/strict';

const type = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight', 'color'];
const rhythm = ['marginTop', 'marginBottom', 'paddingTop', 'paddingBottom', 'borderTopWidth', 'borderTopColor'];
const probes = {
  page: ['body', [...type, 'backgroundColor', 'paddingLeft', 'paddingRight']],
  record: ['.press-record-heading', [...type, ...rhythm]],
  provenance: ['.press-record-provenance', [...type, ...rhythm, 'display', 'columnGap']],
  firstMessage: ['.press-first-passage', [...type, ...rhythm]],
  laterMessage: ['.press-passage-heading:not(.press-first-passage)', [...type, ...rhythm]],
  metadata: ['.press-passage-datetime', [...type, 'marginTop', 'marginBottom']],
  prose: ['.press-supporting-element > p', [...type, 'marginBottom']],
  table: ['.press-supporting-element .press-table-scroll > table', ['fontSize', 'lineHeight', 'borderCollapse', 'borderTopWidth', 'borderBottomWidth']],
  tableHead: ['.press-supporting-element .press-table-scroll > table th', [...type, 'paddingTop', 'paddingBottom', 'borderBottomWidth']],
  math: ['.press-supporting-element .press-math-scroll', ['marginTop', 'marginBottom', 'overflowX']],
  code: ['.press-supporting-element pre', [...type, 'backgroundColor', 'paddingTop', 'paddingLeft', 'overflowX']],
  permalink: ['.press-supporting-element .press-permalink', ['fontFamily', 'fontSize', 'color', 'opacity', 'textDecorationLine']],
};

export async function presentationSnapshot(page) {
  await page.evaluate(() => document.fonts.ready);
  return page.evaluate(probes => {
    const facts = {};
    for (const [role, [selector, properties]] of Object.entries(probes)) {
      const node = document.querySelector(selector);
      if (!node) throw new Error('Missing presentation probe: ' + role);
      facts[role + '.tag'] = node.localName;
      const style = getComputedStyle(node);
      for (const property of properties) {
        const value = style[property];
        facts[role + '.' + property] = /^-?[0-9.]+px$/.test(value) ? Number(parseFloat(value).toFixed(2)) : value;
      }
    }
    const body = document.body, style = getComputedStyle(body), rect = body.getBoundingClientRect();
    const contentWidth = rect.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    for (const [role, selector] of Object.entries({record: '.press-record-heading', metadata: '.press-passage-datetime', prose: '.press-supporting-element'})) {
      const box = document.querySelector(selector).getBoundingClientRect();
      facts[role + '.contentWidthRatio'] = Number((box.width / contentWidth).toFixed(3));
      facts[role + '.contentInset'] = Number((box.left - rect.left - parseFloat(style.paddingLeft)).toFixed(2));
    }
    facts['page.horizontalOverflow'] = document.documentElement.scrollWidth > innerWidth + 1;
    return facts;
  }, probes);
}

export function assertPresentation(actual, expected, label) {
  assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), label + ': probe coverage changed');
  for (const [key, value] of Object.entries(expected)) {
    if (typeof value === 'number') assert.ok(Math.abs(actual[key] - value) <= (key.endsWith('Ratio') ? 0.002 : 0.1),
      `${label}: ${key}: expected ${value}, received ${actual[key]}`);
    else assert.equal(actual[key], value, label + ': ' + key);
  }
}
