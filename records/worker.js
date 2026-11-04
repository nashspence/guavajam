// Keep the cited addresses stable when the publisher changes archive providers.
const headers = {'Cache-Control': 'no-store, no-transform', 'X-Content-Type-Options': 'nosniff',
                 'Referrer-Policy': 'no-referrer'};
const error = status => new Response(null, {status, headers});
const redirect = target => new Response(null, {status: 302, headers: {...headers, Location: target}});

async function githubReadingCopy(request, env, repository, tag, slug, version) {
  const filename = slug + '-' + version + '.html';
  const options = {redirect: 'manual', headers: {'User-Agent': 'Press-edition-reader',
                   'Accept': 'application/json'}, cf: {cacheEverything: true, cacheTtlByStatus: {'200': 300, '400-599': 0}}};
  // Only spend anonymous GitHub API quota for a tag the publisher has authorized.
  const registration = await fetch('https://raw.githubusercontent.com' + repository +
    '/refs/heads/main/registrations/editions/' + slug + '/' + version + '.json', options);
  if (registration.status === 404) return error(404);
  if (registration.status !== 200) return error(502);
  const record = await registration.json();
  if (record.schema !== 'press.edition-registration.v1' || record.work !== slug || record.version !== version ||
      record.record_url !== 'https://' + env.RECORD_HOST + '/' + tag || record.canonical_artifact !== filename ||
      !/^[0-9a-f]{64}$/.test(record.artifact_sha256) || 'cancellation_reason' in record) return error(502);

  const release = await fetch('https://api.github.com/repos' + repository + '/releases/tags/' +
    encodeURIComponent(tag), {...options, cf: {cacheEverything: true, cacheTtlByStatus: {'200': 3600, '400-599': 0}}});
  if (release.status === 404) return error(404);
  if (release.status !== 200) return error(502);
  const issued = await release.json();
  if (issued.tag_name !== tag || issued.immutable !== true || issued.draft !== false ||
      issued.prerelease !== false) return error(502);
  const assets = issued.assets.filter(a => a.name === filename);
  const asset = assets[0];
  if (assets.length !== 1 || asset.state !== 'uploaded' || asset.digest !== 'sha256:' + record.artifact_sha256 ||
      !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > Number(env.MAX_HTML_BYTES)) return error(502);

  // An immutable asset's GitHub digest binds this download to the reviewed registration.
  // Stream the CDN response: a valid edition can exceed the Worker's memory limit.
  const download = new URL(asset.browser_download_url);
  if (download.origin !== 'https://github.com' || download.username || download.password || download.search ||
      download.hash || decodeURIComponent(download.pathname) !== repository + '/releases/download/' + tag + '/' + filename) {
    return error(502);
  }
  const transfer = {method: request.method, redirect: 'manual',
                    headers: {'User-Agent': 'Press-edition-reader', 'Accept-Encoding': 'identity'}};
  let response = await fetch(download.href, transfer);
  if ([301, 302, 303, 307, 308].includes(response.status)) {
    const location = new URL(response.headers.get('Location'));
    if (location.protocol !== 'https:' || location.username || location.password || location.port ||
        !['release-assets.githubusercontent.com', 'objects.githubusercontent.com'].includes(location.hostname)) return error(502);
    response = await fetch(location.href, transfer);
  }
  if (response.status !== 200 || response.headers.get('Content-Length') !== String(asset.size) ||
      ![null, 'identity'].includes(response.headers.get('Content-Encoding'))) return error(502);
  return new Response(response.body, {headers: {...headers, 'Content-Type': 'text/html; charset=utf-8',
    'Content-Disposition': 'inline', 'Content-Security-Policy':
      "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}});
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.protocol !== 'https:' || url.hostname !== env.RECORD_HOST || url.port || url.search) return error(404);
    if (!['GET', 'HEAD'].includes(request.method)) {
      return new Response(null, {status: 405, headers: {...headers, Allow: 'GET, HEAD'}});
    }
    try {
      const base = new URL(env.RECORD_TARGET_BASE);
      if (base.protocol !== 'https:' || base.username || base.password || base.port ||
          base.search || base.hash || env.RECORD_TARGET_BASE.endsWith('/') ||
          typeof env.RECORD_TAG_PATTERN !== 'string' || !env.RECORD_TAG_PATTERN) return error(503);
      const prefix = base.href.replace(/\/$/, '');
      const github = base.hostname === 'github.com' && /^(\/[\w.-]+\/[\w.-]+)\/releases\/tag$/.exec(base.pathname);
      if (url.pathname === '/') return redirect(github ? prefix.slice(0, -4) : prefix);
      const reading = url.pathname.endsWith('/read');
      const tag = url.pathname.slice(1, reading ? -5 : undefined);
      const match = new RegExp('^(?:' + env.RECORD_TAG_PATTERN + ')$', 'u').exec(tag);
      if (!match) return error(404);
      if (!reading || !github) return redirect(prefix + '/' + tag + (reading ? '/read' : ''));
      if (!Number.isSafeInteger(Number(env.MAX_HTML_BYTES)) || Number(env.MAX_HTML_BYTES) <= 0) return error(503);
      return await githubReadingCopy(request, env, github[1], tag, match[1], match[2]);
    } catch { return error(502); }
  },
};
