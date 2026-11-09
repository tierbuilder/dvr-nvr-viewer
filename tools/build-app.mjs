// Packs browser-app/ into dist/dvr-cameras.swbn: a signed web bundle that Chrome installs as an
// "Isolated Web App", the only kind of web app allowed to open TCP connections (to reach the DVR).
//
//   cd tools && npm install && npm run build-app
//
// The bundle is signed with tools/signing-key.pem (made on first run, never committed). The key
// decides the app's identity: keep it, or updates install as a different app with no settings.
import * as fs from "node:fs";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as wbn from "wbn";
import * as wbnSign from "wbn-sign";

const here = path.dirname(fileURLToPath(import.meta.url));
const appDir = path.join(here, "..", "browser-app");
const outDir = path.join(here, "..", "dist");
const keyFile = process.env.DVR_SIGNING_KEY || path.join(here, "signing-key.pem");

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png",
  ".webmanifest": "application/manifest+json", ".json": "application/json", ".svg": "image/svg+xml" };
// what Chrome expects every Isolated Web App response to carry
const HEADERS = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Content-Security-Policy": "base-uri 'none'; default-src 'self'; object-src 'none'; frame-src 'self' https: blob: data:; " +
    "connect-src 'self' https: wss: blob: data:; script-src 'self' 'wasm-unsafe-eval'; img-src 'self' https: blob: data:; " +
    "media-src 'self' https: blob: data:; font-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; " +
    "require-trusted-types-for 'script'; frame-ancestors 'self';",
};

function addDir(builder, dir, base) {
  for (const name of fs.readdirSync(dir).sort()) {
    const file = path.join(dir, name);
    if (fs.statSync(file).isDirectory()) { addDir(builder, file, base + name + "/"); continue; }
    const type = TYPES[path.extname(name)];
    if (!type) continue; // only app files go in the bundle
    const headers = { "Content-Type": type, ...HEADERS };
    const body = fs.readFileSync(file);
    if (name === "index.html") {
      builder.addExchange(base, 200, headers, body);
      builder.addExchange(base + name, 301, { Location: "./", ...HEADERS }, "");
    } else builder.addExchange(base + name, 200, headers, body);
  }
}

if (!fs.existsSync(keyFile)) {
  execFileSync("openssl", ["genpkey", "-algorithm", "ed25519", "-out", keyFile]);
  fs.chmodSync(keyFile, 0o600);
  console.log(`made a new signing key: ${keyFile} (keep it safe and private)`);
}
const builder = new wbn.BundleBuilder();
addDir(builder, appDir, ""); // relative addresses, as Chrome's own bundle tool makes them
const signed = await wbnSign.SignedWebBundle.fromWebBundle(
  builder.createBundle(), [new wbnSign.NodeCryptoSigningStrategy(wbnSign.parsePemKey(fs.readFileSync(keyFile, "utf-8")))]);
fs.mkdirSync(outDir, { recursive: true });
const out = path.join(outDir, "dvr-cameras.swbn");
fs.writeFileSync(out, signed.getSignedWebBundleBytes());
const version = JSON.parse(fs.readFileSync(path.join(appDir, ".well-known", "manifest.webmanifest"))).version;
console.log(`built ${out} (version ${version})`);
console.log(`app address: isolated-app://${signed.getWebBundleId()}/`);
