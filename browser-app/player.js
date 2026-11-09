// One camera tile: keeps an RTSP connection open, decodes the video with the browser's own
// decoder (WebCodecs, hardware accelerated where possible) and paints the newest frame.
import { RtspClient, LoginRejected } from "./rtsp.js";

export class CameraPlayer {
  // makeTransport() returns a fresh TCP transport; onStatus(text, kind) reports "", "connecting…" etc.
  // onLogin() is called when the DVR accepts the login
  constructor({ canvas, makeTransport, onStatus, onLogin }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.makeTransport = makeTransport;
    this.onStatus = onStatus || (() => {});
    this.onLogin = onLogin || (() => {});
    this.url = null;
    this.live = false;
    this.failures = 0;
    this.loginRejected = false;
  }

  // play(url) switches to another address; play(null) stops and frees the DVR's stream
  play(url, user, password) {
    if (url === this.url) return;
    this.stop();
    this.url = url;
    this.user = user;
    this.password = password;
    if (!url) return;
    this.failures = 0;
    this.loginRejected = false;
    this.connect();
  }

  stop() {
    this.url = null;
    clearTimeout(this.retryTimer);
    this.client?.close();
    this.client = null;
    this.closeDecoder();
  }

  async connect() {
    const url = this.url;
    this.onStatus("connecting…", "warn");
    this.waitingForKey = true;
    this.live = false;
    const client = this.client = new RtspClient({
      url, user: this.user, password: this.password, transport: this.makeTransport(),
      onFrame: f => { if (this.client === client) this.decode(f); },
      onLogin: () => { if (this.client === client) this.onLogin(); },
    });
    try {
      await client.run();
      throw client.error || new Error("the camera stopped");
    } catch (e) {
      if (this.client !== client || this.url !== url) return; // stopped or switched meanwhile
      client.close();
      this.closeDecoder();
      if (e instanceof LoginRejected) {
        // never retry a rejected login: the DVR would lock the account
        this.loginRejected = true;
        this.onStatus("login rejected", "error");
        return;
      }
      this.failures++;
      const wait = Math.min(30, 3 * 2 ** (this.failures - 1)); // back off, so a dead camera isn't hammered
      this.onStatus(`${e.message} · retrying in ${wait}s`, "warn");
      this.retryTimer = setTimeout(() => { if (this.url === url) this.connect(); }, wait * 1000);
    }
  }

  decode(f) {
    if (f.key && (!this.decoder || this.codec !== f.codec)) this.openDecoder(f.codec);
    if (!this.decoder || this.decoder.state !== "configured") return;
    // if the computer can't keep up, skip ahead to the next key frame instead of falling behind
    if (this.decoder.decodeQueueSize > 8 && !f.key) { this.waitingForKey = true; return; }
    if (this.waitingForKey && !f.key) return;
    this.waitingForKey = false;
    try {
      this.decoder.decode(new EncodedVideoChunk({ type: f.key ? "key" : "delta", timestamp: f.timestamp, data: f.data }));
    } catch (e) {
      this.closeDecoder();
    }
  }

  openDecoder(codec) {
    this.closeDecoder();
    this.codec = codec;
    this.decoder = new VideoDecoder({
      output: frame => this.draw(frame), // painted straight away: nothing queues up, so no growing delay
      error: e => {
        this.decoder = null; // a fresh decoder is made at the next key frame
        this.onStatus(`video error: ${e.message}`, "warn");
      },
    });
    // no "description": frames arrive in Annex B form, parameter sets in front of each key frame
    this.decoder.configure({ codec, optimizeForLatency: true });
  }

  closeDecoder() {
    try { if (this.decoder && this.decoder.state !== "closed") this.decoder.close(); } catch (e) {}
    this.decoder = null;
  }

  draw(f) {
    const w = f.displayWidth, h = f.displayHeight;
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
    this.ctx.drawImage(f, 0, 0, w, h);
    f.close();
    if (!this.live) { this.live = true; this.failures = 0; this.onStatus("", ""); }
  }
}
