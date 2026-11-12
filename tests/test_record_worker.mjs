import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
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
  for (const file of ['SHA256SUMS', 'provenance.json', 'sample-work-v1.0.0.html', 'sample-work-v1.0.0-offline.zip']) {
    const response = await worker.fetch(new Request(address + '/' + file), env);
    assert.equal(response.headers.get('Cache-Control'), 'no-store, no-transform');
    assert.equal(response.headers.get('Location'), 'https://github.com/owner/press/releases/download/' +
                 encodeURIComponent('works/sample-work/v1.0.0') + '/' + file);
  }
  const backup = {...env, RECORD_TARGET_BASE: 'https://recovered.publisher.test'};
  for (const suffix of ['', '/read', '/provenance.json', '/sample-work-v1.0.0-offline.zip']) {
    const recovered = await worker.fetch(new Request(address + suffix), backup);
    assert.equal(recovered.headers.get('Cache-Control'), 'no-store, no-transform');
    assert.equal(recovered.headers.get('Location'), backup.RECORD_TARGET_BASE + '/works/sample-work/v1.0.0' + suffix);
  }
  const catalog = await worker.fetch(new Request('https://press.publisher.test/'), env);
  assert.equal(catalog.headers.get('Location'), 'https://github.com/owner/press/releases');
});

test('requests cannot choose another host, path, query destination, or write method', async () => {
  for (const url of ['https://other.test/works/sample-work/v1.0.0',
                     address + '?target=https://evil.test', address + '/',
                     address.replace('/works/', '/%77orks/'), address.replace('/works/', '//works/'),
                     address.replace('sample-work', 'elsewhere'), address.replace('v1.0.0', 'v01.0.0'),
                     address + '/sample-work-v2.0.0-offline.zip', address + '/stray.zip']) {
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

function readingArchive(t, html = '<h2 id="part">Part</h2>') {
  const tag = 'works/sample-work/v1.0.0';
  const file = 'sample-work-v1.0.0.html';
  const download = 'https://github.com/owner/press/releases/download/' + encodeURIComponent(tag) + '/' + file;
  const cdn = 'https://release-assets.githubusercontent.com/file?signed=example';
  const sha = createHash('sha256').update(html).digest('hex');
  const record = {schema: 'press.edition-registration.v1', work: 'sample-work', version: 'v1.0.0',
                  record_url: address, canonical_artifact: file, artifact_sha256: sha};
  const asset = {name: file, digest: 'sha256:' + sha, state: 'uploaded', size: html.length, browser_download_url: download};
  const release = {tag_name: tag, immutable: true, draft: false, prerelease: false, assets: [asset]};
  const archive = {location: cdn, apiStatus: 200, body: html};
  const urls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    urls.push(url);
    assert.equal(options.redirect, 'manual');
    assert.ok(!options.headers?.Cookie && !options.headers?.Authorization);
    if (url.endsWith('/index.html')) return new Response(null, {status: 404});
    if (url === 'https://raw.githubusercontent.com/owner/press/refs/heads/main/registrations/editions/sample-work/v1.0.0.json') return Response.json(record);
    if (url === 'https://api.github.com/repos/owner/press/releases/tags/' + encodeURIComponent(tag)) return Response.json(release, {status: archive.apiStatus});
    if (url === download) return new Response(null, {status: 302, headers: {Location: archive.location}});
    if (url === cdn) return new Response(options.method === 'HEAD' ? null :
      (typeof archive.body === 'function' ? archive.body() : archive.body), {
      status: archive.status ?? 200,
      headers: {'Content-Length': String(html.length), 'Content-Disposition': 'attachment',
                'Set-Cookie': 'secret', ...archive.headers}});
    throw new Error('unexpected URL');
  });
  return {archive, html, record, asset, release, tag, urls, file, download, cdn};
}

test('section citations serve only the registered immutable HTML inline, streaming the fixed public download', async t => {
  const {archive, html, record, asset, release, tag, urls, file} = readingArchive(t);
  const primary = {...env, MAX_HTML_BYTES: '150994944'};
  assert.equal((await worker.fetch(new Request(address + '/read'), primary)).status, 404);
  assert.equal(urls.some(url => url.startsWith('https://api.github.com/')), false);
  record.issuing = true;
  record.abandoned_run_id = '42';
  assert.equal((await worker.fetch(new Request(address + '/read'), primary)).status, 404);
  assert.equal(urls.some(url => url.startsWith('https://api.github.com/')), false);
  delete record.abandoned_run_id;
  const pending = await worker.fetch(new Request(address), primary);
  assert.equal(pending.headers.get('Cache-Control'), 'no-store, no-transform');
  assert.equal(pending.headers.get('Location'), 'https://github.com/owner/press/releases/tag/' + tag);
  record.published_at = '2026-11-04T00:00:00Z';
  assert.equal((await worker.fetch(new Request(address), primary)).status, 503);
  const read = method => worker.fetch(new Request(address + '/read#part', {method,
    headers: {Cookie: 'reader', Authorization: 'private'}}), {...env, MAX_HTML_BYTES: '150994944'});
  for (const method of ['GET', 'HEAD']) {
    const response = await read(method);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Content-Disposition'), 'inline');
    assert.equal(response.headers.get('Cache-Control'), 'public, max-age=31536000, immutable, no-transform');
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
    const failed = await read('GET');
    assert.equal(failed.status, 502);
    assert.equal(failed.headers.get('Cache-Control'), 'no-store, no-transform');
    assert.equal(urls.length, obj === record ? 1 : 2);
    if (original === undefined) delete obj[key]; else obj[key] = original;
  }
  archive.location = 'https://untrusted.test/redirect';
  assert.equal((await read('GET')).status, 502);
  assert.ok(!urls.includes(archive.location));
  const mirror = await worker.fetch(new Request(address + '/read#part'), {...env, RECORD_TARGET_BASE: 'https://archive.test/backups'});
  assert.equal(mirror.headers.get('Location'), 'https://archive.test/backups/' + tag + '/read');
});

function replicaFixture(t, content) {
  const {archive, html, record, urls, download, cdn} = readingArchive(t, content);
  record.issuing = true;
  record.published_at = '2026-11-04T00:00:00Z';
  const key = 'sha256/' + record.artifact_sha256 + '.html';
  const bucket = {
    object: null,
    get: t.mock.fn(k => k === key ? bucket.object : null),
    head: t.mock.fn(k => k === key ? bucket.object : null),
    put: t.mock.fn(async (k, stream, options) => {
      assert.equal(k, key);
      assert.equal(options.onlyIf.get('If-None-Match'), '*');
      assert.ok(stream instanceof ReadableStream);
      const bytes = Buffer.from(await new Response(stream).arrayBuffer());
      const digest = createHash('sha256').update(bytes).digest();
      assert.equal(options.sha256, digest.toString('hex'));
      if (bucket.object) return null;
      bucket.object = {body: bytes, size: bytes.length, checksums: {sha256: Uint8Array.from(digest).buffer}};
      return bucket.object;
    }),
  };
  const read = (method = 'GET') => worker.fetch(new Request(address + '/read', {method}),
    {...env, MAX_HTML_BYTES: '150994944', READING_COPIES: bucket});
  return {archive, bucket, html, read, record, urls, download, cdn};
}

test('concurrent first reads fill one digest-checked replica and warm reads bypass the Releases API', async t => {
  const {archive, bucket, html, read, urls} = replicaFixture(t);
  for (const response of await Promise.all([read(), read()])) {
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('X-Press-Reading-Source'), 'replica');
    assert.equal(await response.text(), html);
  }
  const puts = await Promise.all(bucket.put.mock.calls.map(call => call.result));
  assert.equal(puts.length, 2);
  assert.equal(puts.filter(Boolean).length, 1);

  archive.apiStatus = 503;
  urls.length = 0;
  for (const method of ['GET', 'HEAD']) {
    const response = await read(method);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Content-Length'), String(Buffer.byteLength(html)));
    assert.equal(response.headers.get('Content-Disposition'), 'inline');
    assert.equal(response.headers.get('X-Press-Reading-Source'), 'replica');
    assert.equal(await response.text(), method === 'HEAD' ? '' : html);
  }
  assert.equal(bucket.head.mock.callCount(), 1);
  assert.equal(bucket.put.mock.callCount(), 2);
  assert.deepEqual(urls.map(url => new URL(url).hostname),
    ['raw.githubusercontent.com', 'raw.githubusercontent.com']);
});

test('damaged bytes cannot populate the replica and a mismatched stored checksum fails closed', async t => {
  const {archive, bucket, html, read} = replicaFixture(t);
  archive.body = 'x'.repeat(html.length);
  const failed = await read();
  assert.equal(failed.status, 502);
  assert.equal(await failed.text(), '');
  assert.equal(bucket.put.mock.callCount(), 1);
  assert.equal(bucket.object, null);
  archive.body = html;
  assert.equal((await read()).status, 200);
  for (const checksum of [undefined, new ArrayBuffer(32)]) {
    bucket.object.checksums.sha256 = checksum;
    for (const method of ['GET', 'HEAD']) assert.equal((await read(method)).status, 502);
  }
});

test('R2 lookup failure still serves a verified release', async t => {
  const {bucket, html, read} = replicaFixture(t);
  bucket.head = bucket.get = async () => { throw Error('R2 unavailable'); };
  for (const method of ['GET', 'HEAD']) {
    const response = await read(method);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('X-Press-Reading-Source'), 'release');
    assert.equal(await response.text(), method === 'HEAD' ? '' : html);
  }
  assert.equal(bucket.put.mock.callCount(), 0);
});

test('documented operational write failures fall back to a fresh immutable release download', async t => {
  for (const code of [10001, 10002, 10003, 10006, 10042, 10043, 10058]) {
    await t.test(String(code), async t => {
      const {bucket, html, read, record, urls, download, cdn} = replicaFixture(t);
      bucket.put = t.mock.fn(async (key, stream, options) => {
        assert.equal(key, 'sha256/' + record.artifact_sha256 + '.html');
        assert.equal(options.onlyIf.get('If-None-Match'), '*');
        assert.equal(options.sha256, record.artifact_sha256);
        throw Error(`put: Storage unavailable (${code})`);
      });
      const response = await read();
      assert.equal(response.status, 200);
      assert.equal(response.headers.get('X-Press-Reading-Source'), 'release');
      assert.equal(response.headers.get('Set-Cookie'), null);
      assert.equal(await response.text(), html);
      assert.equal(bucket.put.mock.callCount(), 1);
      assert.equal(bucket.object, null);
      assert.equal(urls.filter(url => url === download).length, 2);
      assert.equal(urls.filter(url => url === cdn).length, 2);
    });
  }
});

test('replica failure after partial or complete stream consumption reopens the release', async t => {
  for (const consumed of ['partial', 'complete']) {
    await t.test(consumed, async t => {
      const {archive, bucket, html, read, urls, download} = replicaFixture(t);
      const bytes = new TextEncoder().encode(html);
      archive.body = () => {
        const chunks = [bytes.slice(0, 5), bytes.slice(5)];
        return new ReadableStream({pull(controller) {
          if (chunks.length) controller.enqueue(chunks.shift()); else controller.close();
        }}, {highWaterMark: 0});
      };
      bucket.put = t.mock.fn(async (key, stream) => {
        if (consumed === 'partial') {
          const reader = stream.getReader();
          assert.equal((await reader.read()).value.length, 5);
          await reader.cancel();
        } else {
          assert.equal(await new Response(stream).text(), html);
        }
        throw Error('put: Service unavailable (10043)');
      });
      const response = await read();
      assert.equal(response.status, 200);
      assert.equal(await response.text(), html);
      assert.equal(urls.filter(url => url === download).length, 2);
      assert.equal(bucket.put.mock.callCount(), 1);
    });
  }
});

test('operational readback failure after a completed upload reopens the release once', async t => {
  const {bucket, html, read, urls, download, cdn} = replicaFixture(t);
  bucket.get = t.mock.fn(() => {
    if (bucket.object) throw Error('get: Service unavailable (10043)');
    return null;
  });
  const response = await read();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('X-Press-Reading-Source'), 'release');
  assert.equal(await response.text(), html);
  // The normal put consumed and verified the entire first stream before get failed.
  assert.equal(bucket.object.body.toString(), html);
  assert.equal(bucket.put.mock.callCount(), 1);
  assert.equal(bucket.get.mock.callCount(), 2);
  assert.equal(urls.filter(url => url === download).length, 2);
  assert.equal(urls.filter(url => url === cdn).length, 2);
});

test('missing, damaged and unrecognized replica readback results stay fail-closed', async t => {
  const failures = {
    missing: () => null,
    'missing checksum': object => ({...object, checksums: {}}),
    'wrong checksum': object => ({...object, checksums: {sha256: new ArrayBuffer(32)}}),
    'invalid size': object => ({...object, size: 0}),
    'unknown error': () => { throw Error('get: R2 unavailable'); },
    'unknown code': () => { throw Error('get: Unknown failure (99999)'); },
    abort: () => { throw new DOMException('get: Service unavailable (10043)', 'AbortError'); },
  };
  for (const [name, failure] of Object.entries(failures)) {
    await t.test(name, async t => {
      const {bucket, html, read, urls, download, cdn} = replicaFixture(t);
      bucket.get = t.mock.fn(() => bucket.object ? failure(bucket.object) : null);
      const response = await read();
      assert.equal(response.status, 502);
      assert.equal(await response.text(), '');
      assert.equal(response.headers.get('Cache-Control'), 'no-store, no-transform');
      assert.equal(response.headers.get('X-Press-Reading-Source'), null);
      assert.equal(bucket.object.body.toString(), html);
      assert.equal(bucket.put.mock.callCount(), 1);
      assert.equal(bucket.get.mock.callCount(), 2);
      assert.equal(urls.filter(url => url === download).length, 1);
      assert.equal(urls.filter(url => url === cdn).length, 1);
    });
  }
});

test('fallback revalidates the redirect and every release response header', async t => {
  const invalid = {
    redirect: archive => { archive.location = 'https://untrusted.test/edition'; },
    length: archive => { archive.headers = {'Content-Length': '1'}; },
    encoding: archive => { archive.headers = {'Content-Encoding': 'gzip'}; },
    status: archive => { archive.status = 503; },
  };
  for (const [name, change] of Object.entries(invalid)) {
    await t.test(name, async t => {
      const {archive, bucket, read, urls, download} = replicaFixture(t);
      bucket.put = t.mock.fn(async (key, stream) => {
        await new Response(stream).arrayBuffer();
        change(archive);
        throw Error('put: Rate limit exceeded (10058)');
      });
      const response = await read();
      assert.equal(response.status, 502);
      assert.equal(await response.text(), '');
      assert.equal(response.headers.get('Cache-Control'), 'no-store, no-transform');
      assert.equal(urls.filter(url => url === download).length, 2);
      assert.ok(!urls.includes('https://untrusted.test/edition'));
    });
  }
});

test('body, checksum, abort and unrecognized write failures never fall back', async t => {
  const failures = [
    Error('put: Incomplete body (10013)'), Error('put: Invalid digest (10014)'),
    Error('put: Bad digest (10037)'), Error('put: Missing content length (10033)'),
    Error('R2 unavailable'), Error('put: Unknown failure (99999)'),
    Error('put: Bad digest (10037), earlier service error (10043) is unrelated'),
    new DOMException('put: Service unavailable (10043)', 'AbortError'),
  ];
  for (const failure of failures) {
    await t.test(failure.message, async t => {
      const {bucket, read, urls, download} = replicaFixture(t);
      bucket.put = t.mock.fn(async () => { throw failure; });
      const response = await read();
      assert.equal(response.status, 502);
      assert.equal(await response.text(), '');
      assert.equal(response.headers.get('Cache-Control'), 'no-store, no-transform');
      assert.equal(urls.filter(url => url === download).length, 1);
      assert.equal(bucket.object, null);
    });
  }
});

test('fallback streams large editions without cloning, teeing or reading ahead', async t => {
  const {archive, bucket, html, read, record} = replicaFixture(t, '<p>' + 'x'.repeat(2 * 1024 * 1024) + '</p>');
  const bytes = new TextEncoder().encode(html);
  const streams = [];
  archive.body = () => {
    const state = {offset: 0, cancelled: false};
    streams.push(state);
    return new ReadableStream({
      pull(controller) {
        if (state.offset === bytes.length) { controller.close(); return; }
        const end = Math.min(state.offset + 64 * 1024, bytes.length);
        controller.enqueue(bytes.subarray(state.offset, end));
        state.offset = end;
      },
      cancel() { state.cancelled = true; },
    }, {highWaterMark: 0});
  };
  t.mock.method(Response.prototype, 'clone', () => { throw Error('Edition responses must not be cloned'); });
  t.mock.method(ReadableStream.prototype, 'tee', () => { throw Error('Edition streams must not be teed'); });
  bucket.put = t.mock.fn(async () => { throw Error('put: Service unavailable (10043)'); });
  const response = await read();
  assert.equal(response.status, 200);
  assert.equal(streams.length, 2);
  assert.equal(streams[0].cancelled, true);
  assert.equal(streams[1].offset, 0);
  const digest = createHash('sha256');
  for await (const chunk of response.body) digest.update(chunk);
  assert.equal(digest.digest('hex'), record.artifact_sha256);
  assert.equal(streams[1].offset, bytes.length);
});

test('unpublished and abandoned claims cannot consume or seed replica objects', async t => {
  const {bucket, read, record} = replicaFixture(t);
  delete record.published_at;
  assert.equal((await read()).headers.get('X-Press-Reading-Source'), 'release');
  record.abandoned_run_id = '42';
  assert.equal((await read()).status, 404);
  assert.equal(bucket.get.mock.callCount(), 0);
  assert.equal(bucket.put.mock.callCount(), 0);
});
