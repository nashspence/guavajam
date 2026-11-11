import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {chromium} from 'playwright';
import {assertCandidateAccessible, inspectPrint, inspectScreen, inspectStructure, qualifyCandidate} from '../scripts/qualify_edition.mjs';
import {assertAccessible} from './browser_accessibility.mjs';

const css = await readFile(new URL('../styles/edition-v1.css', import.meta.url), 'utf8');
const canonical = 'https://press.example.org/works/sample/v1.0.0/read';
function document(body, head = '') {
  return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Qualification fixture</title>' +
    head + '<style>' + css + '</style><body><main><article class="press-edition"><h1 id="title">Candidate</h1>' +
    body + '</article></main></body></html>';
}

test('candidate outline honors independent quotations and rejects real heading/fragment faults', {timeout: 45000}, async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    const correct = '<h2 class="press-record-heading" id="record">Evidence</h2>' +
      '<h3 id="outer-3">Section</h3><h4 id="outer-4">Subsection</h4><h5 id="outer-5">Detail</h5>' +
      '<figure class="press-turn" id="turn"><blockquote><h3 id="quote">Response heading</h3><p>Quoted.</p></blockquote><figcaption>Correspondent</figcaption></figure>' +
      '<h5 id="resumed">Resumed authorial detail</h5><p><a href="#resumed">Local</a> ' +
      '<a href="' + canonical + '#quote">Permanent</a></p>';
    await page.setContent(document(correct));
    assert.deepEqual((await inspectStructure(page, canonical)).failures, []);
    // This valid independent quotation resets to H3 and then resumes its
    // surrounding author's H5. The default axe flat outline must still detect
    // that apparent jump; only candidate qualification delegates this rule to
    // the scoped structure check above.
    await assert.rejects(() => assertAccessible(page), error =>
      error.actual?.some(finding => finding.id === 'heading-order'));
    assert.ok(Array.isArray(await assertCandidateAccessible(page)));
    for (const [body, diagnostic] of [
      [correct.replace('<h3 id="quote">Response heading</h3>', '<h5 id="quote">Response heading</h5>'), /attributed quotation/],
      ['<h3 id="skipped">Skipped section</h3>', /authorial heading/],
      ['<h2 id="duplicate">Section</h2><p id="duplicate">Duplicate</p>', /Duplicate/],
      ['<p><a href="#absent">Missing</a></p>', /Missing fragment destination/],
    ]) {
      await page.setContent(document(body));
      assert.match((await inspectStructure(page, canonical)).failures.join('\n'), diagnostic);
    }
  } finally { await browser.close(); }
});

test('candidate screen check distinguishes scrollable wide content from broken reflow', {timeout: 45000}, async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 320, height: 900}});
    await page.setContent(document('<h2 id="code">Code</h2><pre tabindex="0"><code>' + 'literal_'.repeat(100) + '</code></pre>'));
    const valid = await inspectScreen(page);
    assert.deepEqual(valid.failures, []);
    assert.equal(valid.overflowing, 1);
    assert.ok(valid.samples.code);
    await page.locator('pre').evaluate(node => node.removeAttribute('tabindex'));
    assert.match((await inspectScreen(page)).failures.join('\n'), /not keyboard scrollable/);
    await page.addStyleTag({content: 'pre {overflow: visible !important}'});
    assert.match((await inspectScreen(page)).failures.join('\n'), /overflows the viewport/);
  } finally { await browser.close(); }
});

test('candidate print gate detects wide and hidden mathematical content before print scaling can mask it', {timeout: 45000}, async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 702, height: 900}});
    await page.emulateMedia({media: 'print'});
    const equation = contents => '<h2 id="equation-1">Mathematics</h2><div class="press-scroll press-math-scroll" tabindex="0">' +
      '<p><math display="block"><mrow>' + contents + '</mrow></math></p></div>';
    await page.setContent(document(equation('<mi>x</mi><mo>=</mo><mn>1</mn>')));
    assert.deepEqual((await inspectPrint(page, 702)).failures, []);
    await page.setContent(document(equation('<mi>x</mi><mo>+</mo>'.repeat(100) + '<mn>1</mn>')));
    assert.match((await inspectPrint(page, 702)).failures.join('\n'), /Mathematics exceeds printable width/);
    await page.addStyleTag({content: 'math {display:none !important}'});
    assert.match((await inspectPrint(page, 702)).failures.join('\n'), /Mathematics is hidden/);
  } finally { await browser.close(); }
});

test('whole candidate qualification completes while its shipped CSP blocks document scripts', {timeout: 45000}, async () => {
  const base = await mkdtemp(path.join(tmpdir(), 'press-qualification-'));
  try {
    const template = await readFile(new URL('../templates/edition-v1.html', import.meta.url), 'utf8');
    const policy = template.match(/<meta http-equiv="Content-Security-Policy"[^>]+>/)?.[0];
    assert.ok(policy, 'Use the actual shipped edition policy');
    const source = document('<h2 id="csp-sentinel">Policy proof</h2>' +
      '<p><a href="#csp-sentinel">Read the proof</a>. Equation <math><mi>x</mi><mo>=</mo><mn>1</mn></math>.</p>' +
      '<script>document.getElementById("csp-sentinel").id = "document-script-ran";</script>', policy);
    const file = path.join(base, 'candidate.html'), output = path.join(base, 'qualification');
    await writeFile(file, source);
    await mkdir(output);
    const request = {file, output, canonical_url: canonical, provenance_sha256: 'a'.repeat(64),
      html_sha256: createHash('sha256').update(source).digest('hex')};
    const report = await qualifyCandidate(request);
    assert.deepEqual(report.failures, []);
    assert.deepEqual(report.structure.targets, ['csp-sentinel'], 'The inline document script must remain blocked');
    assert.deepEqual(Object.keys(report.screens), ['desktop', 'mobile', 'mobile-dark', 'mobile-enlarged']);
    assert.ok(Array.isArray(report.screens.mobile.accessibility_review), 'The asynchronous axe scan must complete');
    assert.deepEqual(Object.keys(report.print), ['A4', 'Letter']);
    for (const paper of Object.keys(report.print)) assert.ok((await readFile(path.join(output, paper + '.pdf'))).length > 0);
    assert.equal((await readFile(file, 'utf8')), source, 'Qualification must not rewrite its candidate');
    assert.deepEqual(JSON.parse(await readFile(path.join(output, 'progress.json'), 'utf8')), {
      schema: 'press.candidate-progress.v1', html_sha256: request.html_sha256,
      provenance_sha256: request.provenance_sha256, phase: 'complete'});
  } finally { await rm(base, {recursive: true, force: true}); }
});
