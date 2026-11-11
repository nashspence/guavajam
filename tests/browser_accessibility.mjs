import assert from 'node:assert/strict';
import axe from 'axe-core';

export function reviewSummary(findings) {
  return findings.map(({id, nodes}) => ({id, elements: nodes.length,
    reasons: [...new Set(nodes.map(node => node.failureSummary))],
    examples: nodes.slice(0, 3).map(node => node.target)}));
}

export async function assertAccessible(page, options = {}) {
  await page.evaluate(() => document.fonts.ready);
  // DevTools injection leaves the document's shipped CSP and runtime unchanged.
  await page.evaluate(axe.source);
  const result = await page.evaluate(async options => {
    const {violations, incomplete} = await axe.run(document, options);
    const concise = findings => findings.map(({id, impact, nodes}) => ({id, impact,
      nodes: nodes.map(({target, failureSummary}) => ({target, failureSummary}))}));
    return {violations: concise(violations), incomplete: concise(incomplete)};
  }, options);
  assert.deepEqual(result.violations, [], `Accessibility violations at ${page.url()}`);
  return result.incomplete;
}
