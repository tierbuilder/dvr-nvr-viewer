// RTSP client that runs in the browser: talks RTSP over a plain TCP connection, unpacks the
// H.264/H.265 video from RTP and hands whole frames to the caller. No server, no plugin.
//
// The TCP connection comes from a "transport":
//   DirectSocketTransport - Chrome's Direct Sockets API (only inside an installed Isolated Web App)
//   WebSocketTransport    - a WebSocket that forwards bytes to TCP (used by the tests)

export class LoginRejected extends Error {}

export class DirectSocketTransport {
  static available() { return typeof globalThis.TCPSocket === "function"; }
  async open(host, port) {
    this.socket = new TCPSocket(host, port, { noDelay: true });
    const { readable, writable } = await this.socket.opened;
    this.reader = readable.getReader();
    this.writer = writable.getWriter();
  }
  async read() {
    const { value, done } = await this.reader.read();
    return done ? null : value;
  }
  write(bytes) { return this.writer.write(bytes); }
  close() {
    // cancelling both streams closes the connection; the DVR sees the camera hang up at once
    this.reader?.cancel().catch(() => {});
    this.writer?.abort().catch(() => {});
  }
}

export class WebSocketTransport {
  constructor(bridge) { this.bridge = bridge; this.queue = []; this.waiting = null; this.ended = false; }
  open(host, port) {
    return new Promise((resolve, reject) => {
      const ws = this.ws = new WebSocket(`${this.bridge}/${encodeURIComponent(host)}/${port}`);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("can't reach the bridge"));
      ws.onmessage = e => this.push(new Uint8Array(e.data));
      ws.onclose = () => { this.ended = true; this.push(null); };
    });
  }
  push(chunk) {
    if (this.waiting) { const w = this.waiting; this.waiting = null; w(chunk); }
    else this.queue.push(chunk);
  }
  read() {
    if (this.queue.length) return Promise.resolve(this.queue.shift());
    if (this.ended) return Promise.resolve(null);
    return new Promise(r => { this.waiting = r; });
  }
  write(bytes) { this.ws.send(bytes); return Promise.resolve(); }
  close() { try { this.ws?.close(); } catch (e) {} }
}

const enc = new TextEncoder(), dec = new TextDecoder();

export class RtspClient {
  // onFrame({codec, key, timestamp (µs), data (Annex B bytes)}) is called for every video frame;
  // onLogin() once the DVR has accepted the login (even if this camera then turns out to be missing)
  constructor({ url, user, password, transport, onFrame, onLogin, timeoutMs = 10000 }) {
    const u = new URL(url.replace(/^rtsp:/i, "http:"));
    this.host = u.hostname.replace(/^\[|\]$/g, "");
    this.port = +u.port || 554;
    // the address sent to the DVR has no login in it (some brands still put it in the path)
    this.url = `rtsp://${u.host}${u.pathname}${u.search}`;
    this.user = user ?? decodeURIComponent(u.username);
    this.password = password ?? decodeURIComponent(u.password);
    this.transport = transport;
    this.onFrame = onFrame;
    this.onLogin = onLogin;
    this.timeoutMs = timeoutMs;
    this.cseq = 0;
    this.pending = null;
    this.buf = new Uint8Array(0);
    this.closed = false;
  }

  // Connects and plays. Resolves when the stream ends, rejects on errors (LoginRejected for a wrong login).
  async run() {
    await this.withTimeout(this.transport.open(this.host, this.port), "the DVR didn't answer");
    this.reading = this.readLoop();
    this.reading.catch(() => {});
    const options = await this.request("OPTIONS", this.url);
    const desc = await this.request("DESCRIBE", this.url, { Accept: "application/sdp" });
    const base = desc.headers["content-base"] || desc.headers["content-location"] || this.url;
    const track = parseSdp(desc.body);
    if (!track) throw new Error("the camera sent no H.264/H.265 video");
    this.depacketizer = new Depacketizer(track, f => this.onFrame?.(f));
    const setup = await this.request("SETUP", resolveControl(base, track.control),
                                     { Transport: "RTP/AVP/TCP;unicast;interleaved=0-1" });
    const session = setup.headers["session"] || "";
    this.session = session.split(";")[0].trim();
    const timeout = +(/timeout=(\d+)/.exec(session) || [, 60])[1];
    const keepAlive = /GET_PARAMETER/.test(options.headers["public"] || "") ? "GET_PARAMETER" : "OPTIONS";
    await this.request("PLAY", base, { Range: "npt=0.000-" });
    this.lastData = Date.now();
    // DVRs drop a session that goes quiet, so say something every so often
    this.keepTimer = setInterval(() => this.send(keepAlive, base, {}), Math.max(5, timeout / 2) * 1000);
    // and give up on a camera that stops sending, so the caller can reconnect
    this.watchdog = setInterval(() => {
      if (Date.now() - this.lastData > this.timeoutMs) this.fail(new Error("no video for a while"));
    }, 1000);
    await this.reading;
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    clearInterval(this.keepTimer);
    clearInterval(this.watchdog);
    // be polite: tell the DVR we're done so it frees the stream straight away
    if (this.session) try { this.send("TEARDOWN", this.url, {}); } catch (e) {}
    setTimeout(() => this.transport.close(), 50);
    this.pending?.reject(new Error("closed"));
  }

  fail(err) {
    this.error = err;
    this.pending?.reject(err);
    this.close();
  }

  withTimeout(promise, message) {
    let t;
    return Promise.race([promise, new Promise((_, rej) => { t = setTimeout(() => rej(new Error(message)), this.timeoutMs); })])
      .finally(() => clearTimeout(t));
  }

  send(method, url, headers) {
    const lines = [`${method} ${url} RTSP/1.0`, `CSeq: ${++this.cseq}`, "User-Agent: dvr-nvr-viewer"];
    if (this.session) lines.push(`Session: ${this.session}`);
    if (this.auth) lines.push(`Authorization: ${this.auth(method, url)}`);
    for (const [k, v] of Object.entries(headers)) lines.push(`${k}: ${v}`);
    this.transport.write(enc.encode(lines.join("\r\n") + "\r\n\r\n")).catch(e => this.fail(e));
    return this.cseq;
  }

  async request(method, url, headers = {}, retried = false) {
    const res = await this.withTimeout(new Promise((resolve, reject) => {
      this.pending = { resolve, reject };
      this.send(method, url, headers);
    }), "the DVR stopped answering");
    this.pending = null;
    if (res.status === 401) {
      // try the login once; a wrong password is never retried, or the DVR may lock the account
      if (retried || !this.user) throw new LoginRejected("login rejected");
      this.auth = makeAuth(res.headers["www-authenticate"] || "", this.user, this.password);
      if (!this.auth) throw new Error("the DVR wants a login method this app doesn't know");
      return this.request(method, url, headers, true);
    }
    if (method === "DESCRIBE") this.onLogin?.(); // any answer but 401 means the login is fine
    if (res.status === 404) throw new Error("no camera on this channel");
    if (res.status >= 300) throw new Error(`the DVR said ${res.status} ${res.reason} to ${method}`);
    return res;
  }

  async readLoop() {
    while (!this.closed) {
      let chunk;
      try { chunk = await this.transport.read(); } catch (e) { if (!this.closed) this.fail(e); break; }
      if (!chunk) { if (!this.closed) this.fail(this.error || new Error("the DVR closed the connection")); break; }
      this.append(chunk);
      this.parse();
    }
    if (this.error) throw this.error;
  }

  append(chunk) {
    const b = new Uint8Array(this.buf.length + chunk.length);
    b.set(this.buf); b.set(chunk, this.buf.length);
    this.buf = b;
  }

  parse() {
    let p = 0;
    const b = this.buf;
    while (p < b.length) {
      if (b[p] === 0x24) { // "$": a video packet sent inside the RTSP connection
        if (b.length - p < 4) break;
        const ch = b[p + 1], len = (b[p + 2] << 8) | b[p + 3];
        if (b.length - p < 4 + len) break;
        if (ch === 0) { this.lastData = Date.now(); this.depacketizer?.rtp(b.subarray(p + 4, p + 4 + len)); }
        p += 4 + len;
        continue;
      }
      const end = indexOf(b, p, [13, 10, 13, 10]);
      if (end < 0) {
        if (b.length - p > 65536) { this.fail(new Error("garbled answer from the DVR")); return; }
        break;
      }
      const head = dec.decode(b.subarray(p, end));
      if (!/^RTSP\//.test(head)) { // not an answer: skip to the next packet marker
        const next = b.indexOf(0x24, p + 1);
        p = next < 0 ? b.length : next;
        continue;
      }
      const res = parseResponse(head);
      const clen = +(res.headers["content-length"] || 0);
      if (b.length - (end + 4) < clen) break;
      res.body = dec.decode(b.subarray(end + 4, end + 4 + clen));
      p = end + 4 + clen;
      // answers to keep-alives arrive here too; only the request we're waiting on cares
      if (this.pending && +res.headers["cseq"] === this.cseq) this.pending.resolve(res);
    }
    this.buf = p ? b.slice(p) : b;
  }
}

function indexOf(b, from, pat) {
  outer: for (let i = from; i <= b.length - pat.length; i++) {
    for (let j = 0; j < pat.length; j++) if (b[i + j] !== pat[j]) continue outer;
    return i;
  }
  return -1;
}

function parseResponse(head) {
  const [first, ...rest] = head.split("\r\n");
  const m = /^RTSP\/\d\.\d\s+(\d+)\s*(.*)$/.exec(first) || [];
  const headers = {};
  for (const line of rest) {
    const i = line.indexOf(":");
    if (i < 0) continue;
    const k = line.slice(0, i).trim().toLowerCase(), v = line.slice(i + 1).trim();
    // several WWW-Authenticate lines: keep the Digest one, it's the safer choice
    if (k === "www-authenticate" && headers[k] && !/^digest/i.test(v)) continue;
    headers[k] = v;
  }
  return { status: +m[1] || 0, reason: m[2] || "", headers };
}

export function makeAuth(header, user, password) {
  const kind = header.split(/\s+/)[0].toLowerCase();
  if (kind === "basic") {
    const token = btoa(String.fromCharCode(...enc.encode(`${user}:${password}`)));
    return () => `Basic ${token}`;
  }
  if (kind !== "digest") return null;
  const p = {};
  header.replace(/(\w+)=(?:"([^"]*)"|([^,\s]*))/g, (_, k, q, u) => { p[k.toLowerCase()] = q ?? u; });
  const qop = (p.qop || "").split(",").map(s => s.trim()).includes("auth") ? "auth" : "";
  let nc = 0;
  return (method, uri) => {
    const ha1 = md5(`${user}:${p.realm}:${password}`), ha2 = md5(`${method}:${uri}`);
    let fields = `username="${user}", realm="${p.realm}", nonce="${p.nonce}", uri="${uri}"`;
    let response;
    if (qop) {
      const ncs = (++nc).toString(16).padStart(8, "0");
      const cnonce = Math.random().toString(16).slice(2, 10);
      response = md5(`${ha1}:${p.nonce}:${ncs}:${cnonce}:auth:${ha2}`);
      fields += `, qop=auth, nc=${ncs}, cnonce="${cnonce}"`;
    } else response = md5(`${ha1}:${p.nonce}:${ha2}`);
    fields += `, response="${response}"`;
    if (p.opaque) fields += `, opaque="${p.opaque}"`;
    if (p.algorithm) fields += `, algorithm=${p.algorithm}`;
    return `Digest ${fields}`;
  };
}

export function parseSdp(sdp) {
  // finds the first H.264 or H.265 video track
  const sections = sdp.split(/\r?\n(?=m=)/);
  for (const s of sections) {
    if (!/^m=video/.test(s)) continue;
    const rtpmap = /a=rtpmap:(\d+)\s+(H264|H265|HEVC)\/(\d+)/i.exec(s);
    if (!rtpmap) continue;
    const pt = rtpmap[1];
    const fmtp = (new RegExp(`a=fmtp:${pt}\\s+(.*)`).exec(s) || [, ""])[1];
    const params = {};
    fmtp.split(";").forEach(kv => { const i = kv.indexOf("="); if (i > 0) params[kv.slice(0, i).trim().toLowerCase()] = kv.slice(i + 1).trim(); });
    const control = (/a=control:\s*(\S+)/.exec(s) || [, "*"])[1];
    const codec = /264/.test(rtpmap[2]) ? "h264" : "h265";
    const sets = [];
    const b64 = v => { try { return Uint8Array.from(atob(v), c => c.charCodeAt(0)); } catch (e) { return null; } };
    if (codec === "h264") (params["sprop-parameter-sets"] || "").split(",").forEach(v => v && sets.push(b64(v)));
    else ["sprop-vps", "sprop-sps", "sprop-pps"].forEach(k => (params[k] || "").split(",").forEach(v => v && sets.push(b64(v))));
    return { codec, payloadType: +pt, clock: +rtpmap[3], control, parameterSets: sets.filter(Boolean) };
  }
  return null;
}

export function resolveControl(base, control) {
  if (!control || control === "*") return base;
  if (/^rtsp:\/\//i.test(control)) return control;
  return base.replace(/\/?$/, "/") + control.replace(/^\//, "");
}

const START = new Uint8Array([0, 0, 0, 1]);

// Turns RTP packets back into whole frames (Annex B: each NAL unit starts with 00 00 00 01).
export class Depacketizer {
  constructor(track, onFrame) {
    this.track = track;
    this.hevc = track.codec === "h265";
    this.onFrame = onFrame;
    this.nals = [];
    this.fu = null;
    this.ts = null;
    this.seq = null;
    this.broken = false;
    this.params = {};
    for (const nal of track.parameterSets) this.remember(nal);
    this.wrap = 0;
    this.lastTs = null;
  }

  type(nal) { return this.hevc ? (nal[0] >> 1) & 0x3f : nal[0] & 0x1f; }

  remember(nal) {
    const t = this.type(nal);
    if (this.hevc ? t >= 32 && t <= 34 : t === 7 || t === 8) this.params[t] = nal;
  }

  rtp(pkt) {
    if (pkt.length < 12 || pkt[0] >> 6 !== 2) return;
    const cc = pkt[0] & 0x0f, ext = pkt[0] & 0x10, pad = pkt[0] & 0x20;
    const marker = pkt[1] & 0x80, pt = pkt[1] & 0x7f;
    if (pt !== this.track.payloadType) return;
    const seq = (pkt[2] << 8) | pkt[3];
    const ts = ((pkt[4] << 24) | (pkt[5] << 16) | (pkt[6] << 8) | pkt[7]) >>> 0;
    let off = 12 + cc * 4, end = pkt.length;
    if (ext) { if (end < off + 4) return; off += 4 + ((pkt[off + 2] << 8) | pkt[off + 3]) * 4; }
    if (pad) end -= pkt[end - 1];
    if (off >= end) return;
    if (this.seq !== null && seq !== ((this.seq + 1) & 0xffff)) { this.broken = true; this.fu = null; } // lost packets
    this.seq = seq;
    if (this.ts !== null && ts !== this.ts) this.flush();
    this.ts = ts;
    this.payload(pkt.subarray(off, end));
    if (marker) this.flush();
  }

  payload(p) {
    if (this.hevc) {
      const t = (p[0] >> 1) & 0x3f;
      if (t === 48) { // aggregation packet
        for (let i = 2; i + 2 <= p.length;) { const n = (p[i] << 8) | p[i + 1]; i += 2; this.add(p.slice(i, i + n)); i += n; }
      } else if (t === 49) { // fragment
        const fh = p[2], start = fh & 0x80, stop = fh & 0x40;
        if (start) this.fu = [new Uint8Array([(p[0] & 0x81) | ((fh & 0x3f) << 1), p[1]]), p.slice(3)];
        else if (this.fu) this.fu.push(p.slice(3));
        if (stop && this.fu) { this.add(concat(this.fu)); this.fu = null; }
      } else this.add(p.slice());
      return;
    }
    const t = p[0] & 0x1f;
    if (t === 24) { // STAP-A: several small NAL units in one packet
      for (let i = 1; i + 2 <= p.length;) { const n = (p[i] << 8) | p[i + 1]; i += 2; this.add(p.slice(i, i + n)); i += n; }
    } else if (t === 28) { // FU-A: one NAL unit split over several packets
      const fh = p[1], start = fh & 0x80, stop = fh & 0x40;
      if (start) this.fu = [new Uint8Array([(p[0] & 0xe0) | (fh & 0x1f)]), p.slice(2)];
      else if (this.fu) this.fu.push(p.slice(2));
      if (stop && this.fu) { this.add(concat(this.fu)); this.fu = null; }
    } else if (t >= 1 && t <= 23) this.add(p.slice());
  }

  add(nal) { if (nal.length) { this.remember(nal); this.nals.push(nal); } }

  flush() {
    const nals = this.nals;
    this.nals = [];
    this.fu = null;
    if (!nals.length) return;
    const types = nals.map(n => this.type(n));
    const key = this.hevc ? types.some(t => t >= 16 && t <= 21) : types.includes(5);
    const hasPicture = this.hevc ? types.some(t => t < 32) : types.some(t => t >= 1 && t <= 5);
    if (!hasPicture) return; // parameter sets on their own: kept for the next key frame
    if (this.broken && !key) return; // a frame with missing pieces would smear the picture
    this.broken = false;
    // the decoder needs the parameter sets in front of every key frame
    let out = nals;
    if (key) {
      const need = this.hevc ? [32, 33, 34] : [7, 8];
      out = [...need.filter(t => !types.includes(t) && this.params[t]).map(t => this.params[t]), ...nals];
    }
    const parts = [];
    for (const n of out) parts.push(START, n);
    // RTP time is a 32-bit counter (90 kHz); unwrap it into microseconds
    if (this.lastTs !== null && this.ts < this.lastTs && this.lastTs - this.ts > 0x80000000) this.wrap += 0x100000000;
    this.lastTs = this.ts;
    const timestamp = Math.round(((this.wrap + this.ts) * 1e6) / (this.track.clock || 90000));
    this.onFrame({ codec: this.codecString(), key, timestamp, data: concat(parts) });
  }

  codecString() {
    if (!this.hevc) {
      const sps = this.params[7];
      if (!sps || sps.length < 4) return "avc1.42e01f";
      return "avc1." + [sps[1], sps[2], sps[3]].map(x => x.toString(16).padStart(2, "0")).join("");
    }
    const sps = this.params[33] && unescape(this.params[33]);
    if (!sps || sps.length < 15) return "hev1.1.6.L93.B0";
    const ptl = sps[3], profile = ptl & 0x1f, tier = ptl & 0x20 ? "H" : "L";
    const compat = ((sps[4] << 24) | (sps[5] << 16) | (sps[6] << 8) | sps[7]) >>> 0;
    let rev = 0;
    for (let i = 0; i < 32; i++) if (compat & (1 << i)) rev |= 1 << (31 - i);
    const level = sps[14];
    const space = ["", "A", "B", "C"][ptl >> 6];
    return `hev1.${space}${profile}.${(rev >>> 0).toString(16)}.${tier}${level}.B0`;
  }
}

function unescape(nal) { // removes the 00 00 03 "emulation prevention" bytes
  const out = [];
  for (let i = 0; i < nal.length; i++) {
    if (i >= 2 && nal[i] === 3 && nal[i - 1] === 0 && nal[i - 2] === 0) continue;
    out.push(nal[i]);
  }
  return Uint8Array.from(out);
}

function concat(parts) {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

// MD5, needed for the Digest login most DVRs use (the browser's crypto API has no MD5)
export function md5(str) {
  const bytes = enc.encode(str);
  const n = ((bytes.length + 8) >> 6) + 1, words = new Uint32Array(n * 16);
  for (let i = 0; i < bytes.length; i++) words[i >> 2] |= bytes[i] << ((i % 4) * 8);
  words[bytes.length >> 2] |= 0x80 << ((bytes.length % 4) * 8);
  words[n * 16 - 2] = bytes.length * 8;
  const S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  const K = Array.from({ length: 64 }, (_, i) => Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0);
  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  for (let blk = 0; blk < words.length; blk += 16) {
    let a = a0, b = b0, c = c0, d = d0;
    for (let i = 0; i < 64; i++) {
      let f, g;
      if (i < 16) { f = (b & c) | (~b & d); g = i; }
      else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
      else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
      else { f = c ^ (b | ~d); g = (7 * i) % 16; }
      const tmp = d; d = c; c = b;
      const x = (a + f + K[i] + words[blk + g]) >>> 0, s = S[(i >> 4) * 4 + (i % 4)];
      b = (b + ((x << s) | (x >>> (32 - s)))) >>> 0;
      a = tmp;
    }
    a0 = (a0 + a) >>> 0; b0 = (b0 + b) >>> 0; c0 = (c0 + c) >>> 0; d0 = (d0 + d) >>> 0;
  }
  return [a0, b0, c0, d0].map(v => [0, 8, 16, 24].map(s => ((v >>> s) & 0xff).toString(16).padStart(2, "0")).join("")).join("");
}
