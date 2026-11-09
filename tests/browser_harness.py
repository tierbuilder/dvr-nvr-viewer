"""Test bench for the browser app (browser-app/), using nothing but Python and ffmpeg.

It runs three things:
  * a fake DVR: a real RTSP server with Digest login that sends live H.264 test video
    (made by ffmpeg) over the RTSP connection, like a Dahua DVR does
  * a WebSocket-to-TCP bridge, so a normal browser tab can reach that fake DVR (the installed
    app uses Chrome's Direct Sockets instead and needs no bridge)
  * a web server for browser-app/

    python3 tests/browser_harness.py [--http-port 8093] [--bind 127.0.0.1]

Then open the printed link. The bridge only connects to this computer, so it can't be used
to reach anything else on the network.
"""
import argparse
import base64
import hashlib
import mimetypes
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "browser-app")
USER, PASSWORD, REALM = "admin", "demo123", "Login to fake DVR"
MISSING = {3, 7, 8}  # like the DVR this was built for: these cameras don't exist
PATTERNS = ["testsrc2", "smptebars", "testsrc", "rgbtestsrc", "mandelbrot", "life", "cellauto", "yuvtestsrc"]
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---------------------------------------------------------------- fake DVR (RTSP)

def h264_frames(channel, sub):
    """Yields (is_key, [NAL units]) per frame of live test video from ffmpeg."""
    size = "640x360" if sub else "1280x720"
    pattern = PATTERNS[(channel - 1) % len(PATTERNS)]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
           "-i", f"{pattern}=size={size}:rate=25", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
           "-tune", "zerolatency", "-g", "50", "-x264-params", "aud=1:repeat-headers=1:sliced-threads=0",
           "-f", "h264", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    buf, nals = b"", []
    try:
        while True:
            chunk = proc.stdout.read1(65536)  # whatever has arrived, not a full 64 KB
            if not chunk:
                return
            buf += chunk
            # split on start codes; keep the last (maybe unfinished) piece
            parts = buf.replace(b"\x00\x00\x00\x01", b"\x00\x00\x01").split(b"\x00\x00\x01")
            buf = b"\x00\x00\x01" + parts.pop()
            for nal in parts:
                nal = nal.rstrip(b"\x00")
                if not nal:
                    continue
                if nal[0] & 0x1f == 9:  # access unit delimiter: a new frame starts
                    if nals:
                        yield any(n[0] & 0x1f == 5 for n in nals), nals
                    nals = []
                    continue
                nals.append(nal)
    finally:
        proc.kill()
        proc.wait()


def rtp_packets(nals, seq, ts, ssrc, mtu=1400):
    """RTP packets (RFC 6184) for one frame; the last one has the marker bit."""
    out = []
    for i, nal in enumerate(nals):
        last_nal = i == len(nals) - 1
        if len(nal) <= mtu:
            pieces = [nal]
        else:
            head, body = nal[0], nal[1:]
            chunks = [body[j:j + mtu] for j in range(0, len(body), mtu)]
            pieces = []
            for k, c in enumerate(chunks):
                fu = (head & 0x1f) | (0x80 if k == 0 else 0) | (0x40 if k == len(chunks) - 1 else 0)
                pieces.append(bytes([(head & 0xe0) | 28, fu]) + c)
        for k, p in enumerate(pieces):
            marker = 0x80 if last_nal and k == len(pieces) - 1 else 0
            out.append(struct.pack("!BBHII", 0x80, marker | 96, seq & 0xffff, ts & 0xffffffff, ssrc) + p)
            seq += 1
    return out, seq


class FakeDvrSession(threading.Thread):
    def __init__(self, conn, log):
        super().__init__(daemon=True)
        self.conn, self.log = conn, log
        self.nonce = secrets.token_hex(8)
        self.playing = None
        self.lock = threading.Lock()

    def send(self, data):
        with self.lock:
            self.conn.sendall(data)

    def reply(self, cseq, status="200 OK", headers=(), body=""):
        lines = [f"RTSP/1.0 {status}", f"CSeq: {cseq}", *headers]
        if body:
            lines += ["Content-Type: application/sdp", f"Content-Length: {len(body.encode())}"]
        self.send(("\r\n".join(lines) + "\r\n\r\n" + body).encode())

    def authorized(self, method, headers):
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("digest "):
            return False
        f = dict((k, v.strip('"')) for k, v in
                 (p.strip().split("=", 1) for p in auth[7:].split(",") if "=" in p))
        ha1 = hashlib.md5(f"{USER}:{REALM}:{PASSWORD}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{f.get('uri')}".encode()).hexdigest()
        want = hashlib.md5(f"{ha1}:{self.nonce}:{ha2}".encode()).hexdigest()
        return f.get("username") == USER and f.get("nonce") == self.nonce and f.get("response") == want

    def run(self):
        buf = b""
        try:
            while True:
                data = self.conn.recv(65536)
                if not data:
                    return
                buf += data
                while b"\r\n\r\n" in buf:
                    head, buf = buf.split(b"\r\n\r\n", 1)
                    self.handle(head.decode(errors="replace"))
        except OSError:
            pass
        finally:
            self.playing = None
            self.conn.close()

    def handle(self, head):
        first, *rest = head.split("\r\n")
        method, url, _ = first.split(" ", 2)
        headers = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in rest if ":" in l)}
        cseq = headers.get("cseq", "0")
        self.log(f"RTSP {method} {url}")
        if method == "OPTIONS":
            return self.reply(cseq, headers=["Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN, GET_PARAMETER"])
        if not self.authorized(method, headers):
            if "authorization" in headers:
                print("LOGIN REJECTED", flush=True)
            return self.reply(cseq, "401 Unauthorized",
                              [f'WWW-Authenticate: Digest realm="{REALM}", nonce="{self.nonce}"',
                               f'WWW-Authenticate: Basic realm="{REALM}"'])
        q = dict(p.split("=", 1) for p in url.split("?", 1)[-1].split("&") if "=" in p)
        if method == "DESCRIBE":
            self.channel, self.sub = int(q.get("channel", 1)), int(q.get("subtype", 0))
            if self.channel in MISSING:
                return self.reply(cseq, "404 Not Found")
            sdp = ("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Fake DVR\r\nt=0 0\r\n"
                   "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                   "a=fmtp:96 packetization-mode=1\r\na=control:trackID=0\r\n")
            return self.reply(cseq, headers=[f"Content-Base: {url}/"], body=sdp)
        if method == "SETUP":
            return self.reply(cseq, headers=["Transport: RTP/AVP/TCP;unicast;interleaved=0-1",
                                             "Session: 12345678;timeout=60"])
        if method == "PLAY":
            self.reply(cseq, headers=["Session: 12345678"])
            token = self.playing = object()
            threading.Thread(target=self.stream, args=(token,), daemon=True).start()
            return
        if method == "TEARDOWN":
            self.playing = None
            return self.reply(cseq)
        return self.reply(cseq)  # GET_PARAMETER keep-alive etc.

    def stream(self, token):
        seq, ssrc = 1, 0x1234
        try:
            for n, (_, nals) in enumerate(h264_frames(self.channel, self.sub)):
                if self.playing is not token:
                    return
                pkts, seq = rtp_packets(nals, seq, n * 3600, ssrc)
                self.send(b"".join(b"$\x00" + struct.pack("!H", len(p)) + p for p in pkts))
        except OSError:
            pass


def run_fake_dvr(bind, port, log):
    srv = socket.create_server((bind, port))
    port = srv.getsockname()[1]

    def loop():
        while True:
            conn, _ = srv.accept()
            FakeDvrSession(conn, log).start()
    threading.Thread(target=loop, daemon=True).start()
    return port


# ---------------------------------------------------------------- web server + WebSocket bridge

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=APP_DIR, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/tcp/"):
            return self.bridge()
        return super().do_GET()

    def bridge(self):
        _, _, host, port = self.path.split("/", 3)
        if host not in ("127.0.0.1", "localhost"):
            return self.send_error(403, "the test bridge only connects to this computer")
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
        try:
            tcp = socket.create_connection(("127.0.0.1", int(port)), timeout=5)
            tcp.settimeout(None)
        except OSError:
            return self.send_error(502)
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        super().end_headers()
        ws = self.connection
        done = threading.Event()

        def tcp_to_ws():
            try:
                while not done.is_set():
                    data = tcp.recv(65536)
                    if not data:
                        break
                    n = len(data)
                    head = bytes([0x82, n]) if n < 126 else (
                        bytes([0x82, 126]) + struct.pack("!H", n) if n < 65536 else bytes([0x82, 127]) + struct.pack("!Q", n))
                    ws.sendall(head + data)
            except OSError:
                pass
            done.set()
            try:
                ws.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        threading.Thread(target=tcp_to_ws, daemon=True).start()
        rf = self.rfile
        try:
            while not done.is_set():
                h = rf.read(2)
                if len(h) < 2:
                    break
                op, n = h[0] & 0x0f, h[1] & 0x7f
                if n == 126:
                    n = struct.unpack("!H", rf.read(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", rf.read(8))[0]
                mask = rf.read(4) if h[1] & 0x80 else b"\0\0\0\0"
                data = bytearray(rf.read(n))
                for i in range(len(data)):
                    data[i] ^= mask[i % 4]
                if op == 8:
                    break
                if op in (1, 2):
                    tcp.sendall(data)
        except OSError:
            pass
        done.set()
        tcp.close()
        self.close_connection = True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--http-port", type=int, default=8093)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--rtsp-port", type=int, default=0, help="fake DVR port (default: any free one)")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    mimetypes.add_type("text/javascript", ".js")
    log = (lambda m: None) if a.quiet else (lambda m: print(m, flush=True))
    rtsp_port = run_fake_dvr("127.0.0.1", a.rtsp_port, log)
    web = ThreadingHTTPServer((a.bind, a.http_port), Handler)
    host = "localhost" if a.bind in ("127.0.0.1", "0.0.0.0") else a.bind
    print(f"fake DVR: rtsp://127.0.0.1:{rtsp_port}  user {USER}  password {PASSWORD}", flush=True)
    print(f"open: http://{host}:{a.http_port}/?bridge=ws://{host}:{a.http_port}/tcp", flush=True)
    try:
        web.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
