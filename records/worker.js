// This address can move between archives without changing issued HTML.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                     'Referrer-Policy': 'no-referrer'};
    const error = status => new Response(null, {status, headers});
    if (url.hostname !== env.RECORD_HOST || url.port || url.search) return error(404);
    if (!['GET', 'HEAD'].includes(request.method)) {
      return new Response(null, {status: 405, headers: {...headers, Allow: 'GET, HEAD'}});
    }
    let target;
    try {
      const base = new URL(env.RECORD_TARGET_BASE);
      if (base.protocol !== 'https:' || base.username || base.password || base.port ||
          base.search || base.hash || env.RECORD_TARGET_BASE.endsWith('/') ||
          typeof env.RECORD_TAG_PATTERN !== 'string' || !env.RECORD_TAG_PATTERN) return error(503);
      const prefix = base.href.replace(/\/$/, '');
      if (url.pathname === '/') {
        target = prefix.endsWith('/releases/tag') ? prefix.slice(0, -4) : prefix;
      } else {
        const tag = url.pathname.slice(1);
        const pattern = new RegExp('^(?:' + env.RECORD_TAG_PATTERN + ')$', 'u');
        if (!pattern.test(tag)) return error(404);
        target = prefix + '/' + tag;
      }
    } catch { return error(503); }
    // Temporary redirects and no-store let the issuer repoint a recovered edition.
    return new Response(null, {status: 302, headers: {...headers, Location: target}});
  },
};
