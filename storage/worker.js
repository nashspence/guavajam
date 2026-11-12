// Read-only public B2 proxy. Integrity verification runs in GitHub Actions.
const MAX_BYTES = 10 * 1024 * 1024;
const MIME = {png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
              webp: 'image/webp', svg: 'image/svg+xml', bin: 'application/octet-stream',
              mp3: 'audio/mpeg', mp4: 'video/mp4', vtt: 'text/vtt'};
const STREAMING = new Set(['mp3', 'mp4']);
const RANGE = /^bytes=(?:([0-9]+)-([0-9]*)|-([0-9]+))$/;
function bounded(body, length) {
  if (!body) return null;
  const reader = body.getReader();
  let count = 0;
  return new ReadableStream({
    async pull(controller) {
      try {
        const part = await reader.read();
        if (part.done) {
          if (length !== null && count !== length) throw new Error('Asset length mismatch');
          controller.close();
        } else {
          count += part.value.byteLength;
          if (count > MAX_BYTES) throw new Error('Asset exceeds size limit');
          controller.enqueue(part.value);
        }
      } catch (error) {
        await reader.cancel().catch(() => {});
        controller.error(error);
      }
    },
    cancel(reason) { return reader.cancel(reason); }
  });
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== env.ASSET_HOST || url.search || url.hash ||
        !/^\/sha256\/[0-9a-f]{64}\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)$/.test(url.pathname))
      return new Response("Not found", {status: 404});
    if (!["GET", "HEAD"].includes(request.method))
      return new Response("Method not allowed", {status: 405, headers: {Allow: "GET, HEAD"}});
    const extension = url.pathname.split('.').at(-1);
    const type = MIME[extension];
    const isStream = STREAMING.has(extension);
    const wanted = request.headers.get('Range');
    if (wanted && (!isStream || request.method !== 'GET' || !RANGE.test(wanted)))
      return new Response('Range unsupported', {status: 416, headers: {'Cache-Control': 'no-store'}});
    let upstream;
    try {
      upstream = await fetch(env.B2_PUBLIC_BASE + url.pathname, {
        method: request.method, redirect: 'manual',
        // Explicit range forwarding prevents a partial seek from silently
        // downloading an entire movie on the first CDN cache miss.
        headers: {"Accept-Encoding": "identity", ...(wanted ? {Range: wanted} : {})},
        signal: AbortSignal.timeout(30000),
        ...(wanted ? {} : {cf: {cacheEverything: true, cacheTtlByStatus:
          {"200-299": 31536000, "300-599": -1}}})
      });
    } catch {
      return new Response("Asset unavailable", {status: 502});
    }
    if (wanted && isStream && upstream.status === 416) {
      const position = upstream.headers.get('Content-Range') || '';
      if (!/^bytes \*\/[0-9]+$/.test(position))
        return new Response('Asset unavailable', {status: 502, headers: {'Cache-Control': 'no-store'}});
      return new Response(null, {status: 416, headers: {'Content-Range': position,
        'Accept-Ranges': 'bytes', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store'}});
    }
    if ((wanted ? upstream.status !== 206 : upstream.status !== 200))
      return new Response("Asset unavailable", {status: upstream.status === 404 ? 404 : 502,
                                               headers: {"Cache-Control": "no-store"}});
    const rawLength = upstream.headers.get('Content-Length');
    const length = rawLength === null ? null : Number(rawLength);
    if ((rawLength !== null && (!/^\d+$/.test(rawLength) || !Number.isSafeInteger(length) ||
        (!isStream && length > MAX_BYTES))) ||
        (extension !== 'bin' && (length === 0 ||
          upstream.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== type)) ||
        (upstream.headers.get('Content-Encoding') && upstream.headers.get('Content-Encoding') !== 'identity'))
      return new Response('Asset unavailable', {status: 502, headers: {'Cache-Control': 'no-store'}});
    const headers = new Headers({
      "Cache-Control": "public, max-age=31536000, immutable, no-transform",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'none'; sandbox",
      "Access-Control-Allow-Origin": "*"
    });
    headers.set('Content-Type', type);
    if (length !== null) headers.set('Content-Length', String(length));
    if (isStream) {
      headers.set('Accept-Ranges', 'bytes');
      if (wanted) {
        const part = upstream.headers.get('Content-Range') || '';
        const match = /^bytes ([0-9]+)-([0-9]+)\/([0-9]+)$/.exec(part);
        if (!match || !Number.isSafeInteger(Number(match[3])) ||
            !Number.isSafeInteger(Number(match[1])) || !Number.isSafeInteger(Number(match[2])) ||
            Number(match[1]) > Number(match[2]) || Number(match[2]) >= Number(match[3]) ||
            (length !== null && length !== Number(match[2]) - Number(match[1]) + 1))
          return new Response('Invalid upstream range', {status: 502, headers: {'Cache-Control': 'no-store'}});
        // Sanity-check a specified start: do not return unrelated upstream bytes.
        const requestMatch = RANGE.exec(wanted);
        if (requestMatch[1] !== undefined && Number(requestMatch[1]) !== Number(match[1]))
          return new Response('Invalid upstream range', {status: 502, headers: {'Cache-Control': 'no-store'}});
        headers.set('Content-Range', part);
      }
    }
    if (extension === 'bin') headers.set('Content-Disposition', 'attachment; filename="' + url.pathname.split('/').at(-1) + '"');
    return new Response(request.method === 'HEAD' ? null : (isStream ? upstream.body : bounded(upstream.body, length)),
                        {status: wanted ? 206 : 200, headers});
  }
};
