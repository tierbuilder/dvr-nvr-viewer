# DVR Viewer website

The download site for DVR Viewer: plain HTML and CSS in `public/`, served by a small Cloudflare Worker (`src/worker.js`). It has no frameworks, trackers or third-party scripts, so it loads fast and scores well with search engines.

| Path | What it is |
|---|---|
| `/` | Home: what it is, downloads (the right one is picked for the visitor's computer), features, FAQ |
| `/guide/` | Step-by-step setup and troubleshooting |
| `/brands/` | Every supported brand with its RTSP address (made from the app's brand list) |
| `/download/<platform>` | Short links to the newest release file on GitHub: `windows`, `mac`, `mac-intel`, `linux`, `raspberry-pi` |
| `/llms.txt` | Plain-text summary for AI assistants |
| `/robots.txt`, `/sitemap.xml` | For search engines; AI crawlers are explicitly welcome |

**Search and AI friendliness:**
- Each page has a short title and description, a canonical link, Open Graph and Twitter tags, and a share image (`og.png`).
- Structured data (JSON-LD): a free `SoftwareApplication` (price 0), `FAQPage`, `HowTo` and breadcrumbs.
- `llms.txt` follows the [llms.txt](https://llmstxt.org) convention.
- Pages are fully readable without JavaScript.

## Deploy to Cloudflare (free plan is enough)

You need a free Cloudflare account and Node.js 20 or newer.

```bash
cd website
npm install
npx wrangler login
npm run deploy
```

It's published at `https://dvr-viewer.<your-account>.workers.dev`. To use your own domain, add it in the Cloudflare dashboard: Workers & Pages › dvr-viewer › Settings › Domains & Routes. Canonical links, the sitemap and `llms.txt` use whatever address the site is opened on, so nothing needs changing.

After deploying, submit `https://<your-domain>/sitemap.xml` in [Google Search Console](https://search.google.com/search-console) and [Bing Webmaster Tools](https://www.bing.com/webmasters) (Bing also feeds ChatGPT and Copilot search).

The download buttons point to the latest GitHub release, so they work once the release workflow (`.github/workflows/release.yml`) has published one. Push a version tag to make one.

## Editing

- Change pages in `public/` directly. The brands page, `llms.txt` and the sitemap are generated: edit `build.py` (or the brand list in `dvr_nvr_viewer.py`) and run `python3 build.py`. The tests fail if they're out of date.
- Preview locally with Cloudflare's own runtime: `npm run dev`, then open http://localhost:8787.
- Update `UPDATED` in `build.py` and `softwareVersion` in `public/index.html` with each release.
