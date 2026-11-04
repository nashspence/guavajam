import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../records/worker.js', import.meta.url), 'utf8');
const worker = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;
const env = {RECORD_HOST: 'press.publisher.test',
             RECORD_TARGET_BASE: 'https://github.com/owner/press/releases/tag',
             RECORD_TAG_PATTERN: 'works/(sample-work)/(v1\\.0\\.0)'};
const address = 'https://press.publisher.test/works/sample-work/v1.0.0';

test('edition addresses redirect without fetching and can move to a recovered archive', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('record routing must not fetch edition bytes'); };
  try {
    for (const method of ['GET', 'HEAD']) {
      const response = await worker.fetch(new Request(address, {method}), env);
      assert.equal(response.status, 302);
      assert.equal(response.headers.get('Location'), env.RECORD_TARGET_BASE + '/works/sample-work/v1.0.0');
      assert.equal(response.headers.get('Cache-Control'), 'no-store, no-transform');
      assert.equal(await response.text(), '');
    }
    const recovered = await worker.fetch(new Request(address), {...env, RECORD_TARGET_BASE: 'https://recovered.publisher.test'});
    assert.equal(recovered.headers.get('Location'), 'https://recovered.publisher.test/works/sample-work/v1.0.0');
    const catalog = await worker.fetch(new Request('https://press.publisher.test/'), env);
    assert.equal(catalog.headers.get('Location'), 'https://github.com/owner/press/releases');
  } finally { globalThis.fetch = previous; }
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
    assert.ok(!options.headers.Cookie && !options.headers.Authorization);
    if (url === 'https://raw.githubusercontent.com/owner/press/refs/heads/main/registrations/editions/sample-work/v1.0.0.json') return Response.json(record);
    if (url === 'https://api.github.com/repos/owner/press/releases/tags/' + encodeURIComponent(tag)) return Response.json(release);
    if (url === download) return new Response(null, {status: 302, headers: {Location: location}});
    if (url === cdn) return new Response(options.method === 'HEAD' ? null : html, {
      headers: {'Content-Length': String(html.length), 'Content-Disposition': 'attachment', 'Set-Cookie': 'secret'}});
    throw new Error('unexpected URL');
  });
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
