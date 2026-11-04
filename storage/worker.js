// Read-only public B2 proxy. Integrity verification runs in GitHub Actions.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== env.ASSET_HOST || url.search || url.hash ||
        !/^\/sha256\/[0-9a-f]{64}\.(png|jpg|jpeg|webp|svg)$/.test(url.pathname))
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
    const headers = new Headers({
      "Cache-Control": "public, max-age=31536000, immutable, no-transform",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'none'; sandbox",
      "Access-Control-Allow-Origin": "*"
    });
    for (const name of ["Content-Type", "Content-Length"])
      if (upstream.headers.has(name)) headers.set(name, upstream.headers.get(name));
    return new Response(upstream.body, {status: 200, headers});
  }
};
