// Read-only public B2 proxy. Integrity verification runs in GitHub Actions.
const MAX_BYTES = 10 * 1024 * 1024;
const MIME = {png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
              webp: 'image/webp', svg: 'image/svg+xml', bin: 'application/octet-stream'};
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
        !/^\/sha256\/[0-9a-f]{64}\.(png|jpg|jpeg|webp|svg|bin)$/.test(url.pathname))
      return new Response("Not found", {status: 404});
    if (!["GET", "HEAD"].includes(request.method))
      return new Response("Method not allowed", {status: 405, headers: {Allow: "GET, HEAD"}});
    let upstream;
    try {
      upstream = await fetch(env.B2_PUBLIC_BASE + url.pathname, {
        method: request.method, redirect: "manual",
        headers: {"Accept-Encoding": "identity"},
        signal: AbortSignal.timeout(30000),
        cf: {cacheEverything: true, cacheTtlByStatus: {"200-299": 31536000, "300-599": -1}}
      });
    } catch {
      return new Response("Asset unavailable", {status: 502});
    }
    if (upstream.status !== 200)
      return new Response("Asset unavailable", {status: upstream.status === 404 ? 404 : 502,
                                               headers: {"Cache-Control": "no-store"}});
    const extension = url.pathname.split('.').at(-1);
    const type = MIME[extension];
    const rawLength = upstream.headers.get('Content-Length');
    const length = rawLength === null ? null : Number(rawLength);
    if ((rawLength !== null && (!/^\d+$/.test(rawLength) || !Number.isSafeInteger(length) || length > MAX_BYTES)) ||
        (extension !== 'bin' && (length === 0 ||
          upstream.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== type)))
      return new Response('Asset unavailable', {status: 502, headers: {'Cache-Control': 'no-store'}});
    const headers = new Headers({
      "Cache-Control": "public, max-age=31536000, immutable, no-transform",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'none'; sandbox",
      "Access-Control-Allow-Origin": "*"
    });
    headers.set('Content-Type', type);
    if (length !== null) headers.set('Content-Length', String(length));
    if (extension === 'bin') headers.set('Content-Disposition', 'attachment; filename="' + url.pathname.split('/').at(-1) + '"');
    return new Response(request.method === 'HEAD' ? null : bounded(upstream.body, length), {status: 200, headers});
  }
};
