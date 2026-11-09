// Cloudflare Worker for the DVR Viewer website. The pages are static files in ../public; this adds:
//  * /download/<platform>: short links to the newest release file on GitHub
//  * the site's own address in canonical links, the sitemap, robots.txt and llms.txt (written as
//    __ORIGIN__ in the files), so the same files work on workers.dev and on your own domain
//  * security and caching headers

const REPO = "tierbuilder/dvr-nvr-viewer";
export const DOWNLOADS = {
  windows: "dvr-nvr-viewer-windows-x64.exe",
  mac: "dvr-nvr-viewer-macos-arm64.zip",
  "mac-intel": "dvr-nvr-viewer-macos-x64.zip",
  linux: "dvr-nvr-viewer-linux-x64",
  "raspberry-pi": "dvr-nvr-viewer-linux-arm64",
};
const TEXT = /^(text\/|application\/(xml|json|ld\+json|javascript))/;
const SECURITY = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; " +
    "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const dl = /^\/download\/([a-z-]+)\/?$/.exec(url.pathname);
    if (dl) {
      const file = DOWNLOADS[dl[1]];
      if (!file) return Response.redirect(`${url.origin}/#download`, 302);
      return Response.redirect(`https://github.com/${REPO}/releases/latest/download/${file}`, 302);
    }
    if (url.pathname === "/download" || url.pathname === "/download/") {
      return Response.redirect(`${url.origin}/#download`, 301);
    }

    const res = await env.ASSETS.fetch(request);
    const headers = new Headers(res.headers);
    for (const [k, v] of Object.entries(SECURITY)) headers.set(k, v);
    const type = headers.get("Content-Type") || "";
    // pictures rarely change; pages, CSS and JS are kept short so a new deploy shows up within minutes
    headers.set("Cache-Control", type.startsWith("image/") ? "public, max-age=86400" : "public, max-age=300");
    if (!TEXT.test(type) || res.status === 304 || request.method === "HEAD") {
      return new Response(res.body, { status: res.status, headers });
    }
    const body = (await res.text()).replaceAll("__ORIGIN__", url.origin);
    headers.delete("Content-Length");
    headers.delete("ETag"); // the text changed, so the asset's tag no longer matches
    return new Response(body, { status: res.status, headers });
  },
};
