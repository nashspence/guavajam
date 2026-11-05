import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../records/worker.js', import.meta.url), 'utf8');
const worker = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;
const env = {RECORD_HOST: 'press.publisher.test',
             RECORD_TARGET_BASE: 'https://github.com/owner/press/releases/tag',
             RECORD_SLUG_PATTERN: '[a-z0-9]+(?:-[a-z0-9]+)*',
             RECORD_TAG_PATTERN: 'works/(sample-work)/(v1\\.0\\.0)'};
const address = 'https://press.publisher.test/works/sample-work/v1.0.0';

test('permanent work address serves the approved work page and preserves its path on recovery', async t => {
  const work = 'https://press.publisher.test/works/sample-work';
  let status = 200;
  const upstream = t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'https://raw.githubusercontent.com/owner/press/refs/heads/main/records/works/sample-work/index.html');
    assert.equal(options.redirect, 'manual');
    assert.equal(options.cf.cacheTtl, 0);
    return new Response(options.method === 'HEAD' ? null : '<p>Work record</p>', {status});
  });
  for (const method of ['GET', 'HEAD']) {
    const res = await worker.fetch(new Request(work, {method}), env);
    assert.equal(res.status, 200);
    assert.match(res.headers.get('Content-Type'), /^text\/html/);
    assert.equal(res.headers.get('Cache-Control'), 'no-store, no-transform');
    assert.equal(await res.text(), method === 'HEAD' ? '' : '<p>Work record</p>');
  }
  for (const [upstreamStatus, expected] of [[404, 404], [301, 502], [503, 502]]) {
    status = upstreamStatus;
    assert.equal((await worker.fetch(new Request(work), env)).status, expected);
  }
  const calls = upstream.mock.callCount();
  const base = 'https://archive.test/backup';
  const recovered = await worker.fetch(new Request(work), {...env, RECORD_TARGET_BASE: base});
  assert.equal(recovered.status, 302);
  assert.equal(recovered.headers.get('Location'), base + '/works/sample-work');
  for (const url of [work + '/', work + '?v=1', work + '/read', work.replace('sample-work', 'Sample'),
                     work.replace('sample-work', 'sample%2Dwork'), work.replace('sample-work', 'a--b')]) {
    assert.equal((await worker.fetch(new Request(url), env)).status, 404, url);
  }
  assert.equal((await worker.fetch(new Request(work, {method: 'POST'}), env)).status, 405);
  assert.equal((await worker.fetch(new Request(work), {...env, RECORD_SLUG_PATTERN: undefined})).status, 503);
  assert.equal(upstream.mock.callCount(), calls);
});

test('receipted records serve the reviewed main page, even when the release mirror is unavailable', async t => {
  const raw = 'https://raw.githubusercontent.com/owner/press/refs/heads/main/records/editions/sample-work/v1.0.0/index.html';
  const page = '<p>2026-11-05 Withdrawal: See the replacement edition.</p>';
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, raw);
    return new Response(options.method === 'HEAD' ? null : page);
  });
  for (const method of ['GET', 'HEAD']) {
    const response = await worker.fetch(new Request(address, {method}), env);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('Content-Type'), /^text\/html/);
    assert.equal(response.headers.get('Cache-Control'), 'no-store, no-transform');
    assert.equal(await response.text(), method === 'HEAD' ? '' : page);
  }
  for (const file of ['SHA256SUMS', 'provenance.json', 'sample-work-v1.0.0.html']) {
    const response = await worker.fetch(new Request(address + '/' + file), env);
    assert.equal(response.headers.get('Location'), 'https://github.com/owner/press/releases/download/' +
                 encodeURIComponent('works/sample-work/v1.0.0') + '/' + file);
  }
  const backup = {...env, RECORD_TARGET_BASE: 'https://recovered.publisher.test'};
  for (const suffix of ['', '/read', '/provenance.json']) {
    const recovered = await worker.fetch(new Request(address + suffix), backup);
    assert.equal(recovered.headers.get('Location'), backup.RECORD_TARGET_BASE + '/works/sample-work/v1.0.0' + suffix);
  }
  const catalog = await worker.fetch(new Request('https://press.publisher.test/'), env);
  assert.equal(catalog.headers.get('Location'), 'https://github.com/owner/press/releases');
});

test('requests cannot choose another host, path, query destination, or write method', async () => {
  for (const url of ['https://other.test/works/sample-work/v1.0.0',
                     address + '?target=https://evil.test', address + '/',
                     address.replace('/works/', '/%77orks/'), address.replace('/works/', '//works/'),
                     address.replace('sample-work', 'elsewhere'), address.replace('v1.0.0', 'v01.0.0')]) {
    const response = await worker.fetch(new Request(url), env);
    assert.equal(response.status, 404, url);
    assert.equal(response.headers.get('Location'), null);
  }
  const response = await worker.fetch(new Request(address, {method: 'POST'}), env);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('Allow'), 'GET, HEAD');
});

test('invalid archive configuration cannot redirect readers', async () => {
  for (const RECORD_TARGET_BASE of ['http://archive.test', 'https://user:password@archive.test',
                                    'https://archive.test:8443', 'https://archive.test?q=x',
                                    'https://archive.test#x', 'https://archive.test/']) {
    const response = await worker.fetch(new Request(address), {...env, RECORD_TARGET_BASE});
    assert.equal(response.status, 503);
    assert.equal(response.headers.get('Location'), null);
  }
  assert.equal((await worker.fetch(new Request(address), {...env, RECORD_TAG_PATTERN: undefined})).status, 503);
});

test('section citations serve only the registered immutable HTML inline, streaming the fixed public download', async t => {
  const tag = 'works/sample-work/v1.0.0';
  const file = 'sample-work-v1.0.0.html';
  const html = '<h2 id="part">Part</h2>';
  const download = 'https://github.com/owner/press/releases/download/' + encodeURIComponent(tag) + '/' + file;
  const cdn = 'https://release-assets.githubusercontent.com/file?signed=example';
  const sha = 'a'.repeat(64);
  const record = {schema: 'press.edition-registration.v1', work: 'sample-work', version: 'v1.0.0',
                  record_url: address, canonical_artifact: file, artifact_sha256: sha};
  const asset = {name: file, digest: 'sha256:' + sha, state: 'uploaded', size: html.length, browser_download_url: download};
  const release = {tag_name: tag, immutable: true, draft: false, prerelease: false, assets: [asset]};
  let location = cdn;
  const urls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    urls.push(url);
    assert.equal(options.redirect, 'manual');
    assert.ok(!options.headers?.Cookie && !options.headers?.Authorization);
    if (url.endsWith('/index.html')) return new Response(null, {status: 404});
    if (url === 'https://raw.githubusercontent.com/owner/press/refs/heads/main/registrations/editions/sample-work/v1.0.0.json') return Response.json(record);
    if (url === 'https://api.github.com/repos/owner/press/releases/tags/' + encodeURIComponent(tag)) return Response.json(release);
    if (url === download) return new Response(null, {status: 302, headers: {Location: location}});
    if (url === cdn) return new Response(options.method === 'HEAD' ? null : html, {
      headers: {'Content-Length': String(html.length), 'Content-Disposition': 'attachment', 'Set-Cookie': 'secret'}});
    throw new Error('unexpected URL');
  });
  const primary = {...env, MAX_HTML_BYTES: '150994944'};
  const pending = await worker.fetch(new Request(address), primary);
  assert.equal(pending.headers.get('Location'), 'https://github.com/owner/press/releases/tag/' + tag);
  record.published_at = '2026-11-04T00:00:00Z';
  assert.equal((await worker.fetch(new Request(address), primary)).status, 503);
  delete record.published_at;
  const read = method => worker.fetch(new Request(address + '/read#part', {method,
    headers: {Cookie: 'reader', Authorization: 'private'}}), {...env, MAX_HTML_BYTES: '150994944'});
  for (const method of ['GET', 'HEAD']) {
    const response = await read(method);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Content-Disposition'), 'inline');
    assert.match(response.headers.get('Cache-Control'), /(?:^|, )no-transform(?:,|$)/);
    assert.match(response.headers.get('Content-Type'), /^text\/html/);
    assert.match(response.headers.get('Content-Security-Policy'), /default-src 'none'/);
    assert.equal(response.headers.get('Set-Cookie'), null);
    assert.equal(await response.text(), method === 'GET' ? html : '');
  }
  for (const [obj, key, value] of [[release, 'immutable', false], [release, 'tag_name', 'other'],
         [asset, 'digest', 'sha256:' + 'b'.repeat(64)], [record, 'cancellation_reason', 'retired'],
         [asset, 'browser_download_url', 'https://other.test/' + file], [asset, 'size', 150994945]]) {
    const original = obj[key];
    obj[key] = value;
    urls.length = 0;
    assert.equal((await read('GET')).status, 502);
    assert.equal(urls.length, obj === record ? 1 : 2);
    if (original === undefined) delete obj[key]; else obj[key] = original;
  }
  location = 'https://untrusted.test/redirect';
  assert.equal((await read('GET')).status, 502);
  assert.ok(!urls.includes(location));
  const mirror = await worker.fetch(new Request(address + '/read#part'), {...env, RECORD_TARGET_BASE: 'https://archive.test/backups'});
  assert.equal(mirror.headers.get('Location'), 'https://archive.test/backups/' + tag + '/read');
});
