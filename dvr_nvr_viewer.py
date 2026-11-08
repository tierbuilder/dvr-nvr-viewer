#!/usr/bin/env python3
"""dvr-nvr-viewer: watch all your DVR / NVR / IP cameras at once in any web browser.

Runs on macOS, Windows and Linux (including Raspberry Pi). Only needs Python 3.8+ and ffmpeg.
https://github.com/tierbuilder/dvr-nvr-viewer

It connects to the DVR, turns each camera into a live stream every browser can show, and
serves a web page. That page opens automatically on this computer; phones, tablets and other
computers on the same network open the link it prints (it includes an access key).

Every camera has its own connection, so a slow or stuck camera only freezes its own tile;
the others keep playing live. Cameras are only streamed while someone is watching them,
and the DVR password never leaves this computer.

Layouts (buttons on the page, or --layout to choose the starting one):
  grid      every camera, as square a grid as fits (default)
  2x2 3x3   fixed grid; extra cameras go on more pages
  1+5 1+7   one big camera with 5 or 7 small ones around it
  1         one camera fills the page
Tap a camera to make it big, double-tap for just that camera. Keys: L layout, 1-9 channel,
left/right page or camera, Esc back. Big tiles automatically use the full-quality stream.

Install ffmpeg:
  macOS:   brew install ffmpeg
  Windows: winget install Gyan.FFmpeg   (then open a new terminal)
  Linux:   sudo apt install ffmpeg

Examples:
  dvr-nvr-viewer --ip 192.168.1.64 1 2 3 4                  (asks for username and password)
  dvr-nvr-viewer --ip 192.168.1.64 --user admin --layout 1+5 1 2 3 4 5 6
  dvr-nvr-viewer --ip 192.168.1.64 --brand hikvision 1 2 3 4
  dvr-nvr-viewer --test 1 2 3 4                             (fake video, checks ffmpeg works)

Settings can also come from env vars: DVR_IP, DVR_USER, DVR_PASS, DVR_SUB, DVR_WEB_KEY.
--brand picks the camera address style (dahua, hikvision, xmeye); --url-template handles anything else.
Stop it with Ctrl+C.
"""
import argparse
import base64
import getpass
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote, urlsplit, urlunsplit

__version__ = "0.1.0"

# RTSP address styles. {sub} is 0 for the full-quality stream, 1 for the lighter one;
# {stream} is the same thing counted from 1 (1 = full quality, 2 = lighter).
BRANDS = {
    # Dahua and its many rebrands (Amcrest, Lorex, CP Plus, ...): tested
    "dahua": "rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={sub}",
    # Hikvision and rebrands (HiLook, Annke, ...)
    "hikvision": "rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}0{stream}",
    # XMEye / "NetSurveillance" based DVRs
    "xmeye": "rtsp://{ip}:{port}/user={user}&password={password}&channel={channel}&stream={sub}.sdp",
}
LOW_LATENCY = ["-fflags", "nobuffer", "-flags", "low_delay"]
STALE_AFTER = 5     # seconds without a frame before a tile is marked as having no video
RETRY_AFTER = 3     # seconds to wait before reconnecting a camera that dropped
EXIT_LOGIN_REJECTED = 3  # the DVR said no: retrying would only lock the account
LAYOUT_CHOICES = ["grid", "2x2", "3x3", "1+5", "1+7", "single"]


def parse_args():
    p = argparse.ArgumentParser(prog="dvr-nvr-viewer",
                                description="Watch several DVR/NVR/IP cameras at once in any web browser.")
    p.add_argument("channels", nargs="*", type=int, default=[1, 2, 3, 4], help="channel numbers (default 1 2 3 4)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--ip", default=os.environ.get("DVR_IP"), help="DVR/NVR address, e.g. 192.168.1.64")
    p.add_argument("--brand", default="dahua", choices=sorted(BRANDS),
                   help="camera address style (default dahua); use --url-template for others")
    p.add_argument("--port", type=int, default=554, help="the DVR's RTSP port (default 554)")
    p.add_argument("--user", default=os.environ.get("DVR_USER"), help="DVR username (asked if not given)")
    p.add_argument("--main", action="store_true",
                   help="full quality for every tile (default: only big tiles; small ones use the lighter stream)")
    p.add_argument("--layout", choices=LAYOUT_CHOICES,
                   help="starting layout (default: grid, or whatever the browser last used)")
    p.add_argument("--url-template",
                   help="custom RTSP URL with {user} {password} {ip} {port} {channel} {sub} {stream} placeholders")
    p.add_argument("--web-port", type=int, default=8080, help="web page port (default 8080, or the next free one)")
    p.add_argument("--web-key", default=os.environ.get("DVR_WEB_KEY"),
                   help="access key for the page (default: a random key, saved so links keep working)")
    p.add_argument("--bind", default="0.0.0.0",
                   help="address to listen on (default: all, so other devices can connect; 127.0.0.1 = this computer only)")
    p.add_argument("--no-browser", action="store_true", help="don't open the page on this computer")
    p.add_argument("--test", action="store_true", help="use fake test video instead of the DVR")
    args = p.parse_args()
    if not args.ip and not args.test:
        p.error("--ip is required (the address of your DVR/NVR), or use --test for fake video")
    args.url_template = args.url_template or BRANDS[args.brand]
    return args


def default_sub():
    return int(os.environ.get("DVR_SUB", 1))


def source(args, password, ch, sub=None):
    """ffmpeg input options for one camera; the last item is the input itself."""
    if args.test:
        # 90 kHz timestamps, like real RTSP cameras
        return ["-re", "-f", "lavfi", "testsrc=size=704x576:rate=25,settb=1/90000"]
    if sub is None:
        sub = 0 if args.main else default_sub()
    url = args.url_template.format(user=quote(args.user or "", safe=""), password=quote(password, safe=""),
                                   ip=args.ip, port=args.port, channel=ch, sub=sub, stream=sub + 1)
    # give up on a camera that sends nothing for 10s, so it reconnects instead of hanging forever
    return ["-rtsp_transport", "tcp", "-timeout", "10000000", *LOW_LATENCY, url]


def check_login(args, password, ch):
    """Try the username/password once with a single RTSP request, before opening any camera.

    DVRs lock an account after a few wrong logins, and opening every camera with a wrong
    password would use up those tries at once. Returns True (accepted), False (rejected)
    or None (couldn't tell, e.g. the DVR wants Digest login).
    """
    url = source(args, password, ch)[-1]
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or 554
    bare = urlunsplit((parts.scheme, f"{host}:{port}", parts.path, parts.query, ""))
    token = base64.b64encode(f"{args.user}:{password}".encode()).decode()
    request = (f"DESCRIBE {bare} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n"
               f"Authorization: Basic {token}\r\nUser-Agent: dvr-nvr-viewer\r\n\r\n")
    reply = ""
    for attempt in (1, 2):
        try:
            with socket.create_connection((host, port), timeout=15) as s:
                s.settimeout(15)
                s.sendall(request.encode())
                reply = s.recv(4096).decode(errors="replace")
            break
        except socket.timeout:
            # a busy DVR can be slow to answer; a timeout isn't a rejected login, so one more try is safe
            if attempt == 1:
                print("The DVR is slow to answer, trying once more...")
                continue
            sys.exit(f"The DVR at {host}:{port} didn't answer within 15 seconds (twice).\n"
                     "It may be busy (too many viewers or streams at once) or restarting. Close other\n"
                     "viewers (SmartPSS, phone app, VLC, other copies of this script), wait a minute and retry.")
        except OSError as e:
            hint = ("\nIf you ran this inside the Claude app's terminal, use the Mac's Terminal app instead."
                    if "route" in str(e).lower() else "")
            sys.exit(f"Can't reach the DVR at {host}:{port}: {e}{hint}")
    status = reply.split("\r\n", 1)[0].split(" ")
    code = status[1] if len(status) > 1 else ""
    if code == "200":
        return True
    if code == "401" and "Digest" in reply and "Basic" not in reply:
        return None
    if code == "401":
        return False
    return None


def relay_masked(stream, secrets, prefix="", on_line=None):
    # ffmpeg prints the full RTSP URL on errors; hide the password before showing it
    for raw in iter(stream.readline, b""):
        line = raw.decode(errors="replace")
        if on_line:
            on_line(line)
        for s in secrets:
            if s:
                line = line.replace(s, "****")
        sys.stderr.write(prefix + line)
        sys.stderr.flush()


def start(cmd, secrets, prefix="", on_line=None, **kw):
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, **kw)
    threading.Thread(target=relay_masked, args=(proc.stderr, secrets, prefix, on_line), daemon=True).start()
    return proc


def is_auth_error(line):
    # match the DVR's actual rejection, not any "401" (ffmpeg's memory addresses often contain it)
    return "401 Unauthorized" in line or "authorization failed" in line


def retry_delay(failures):
    """3s, 6s, 12s ... up to 30s, so a camera that keeps failing doesn't hammer the DVR."""
    return min(30, RETRY_AFTER * 2 ** max(0, failures - 1))


def stop(procs):
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


# ---------------------------------------------------------------------------------------------
# Web server: every viewer (this computer, phones, other computers) watches in a browser.
# The DVR password stays on this computer; viewers only need the access key.

IDLE_STOP = 30      # seconds after the last viewer leaves before a camera stream is closed
WEB_FPS = 15        # most frames per second sent to each browser
KEY_FILE = os.path.join(os.path.expanduser("~"), ".dvr_nvr_viewer_web_key")


class JpegSource:
    """One ffmpeg turning one camera stream into JPEG frames, running only while someone watches."""

    def __init__(self, ch, quality, make_cmd, secrets):
        self.ch, self.quality, self.make_cmd, self.secrets = ch, quality, make_cmd, secrets
        self.jpeg, self.seq, self.last_frame_at = None, 0, 0.0
        self.viewers, self.idle_since = 0, time.monotonic()
        self.auth_failed = False
        self.stopping = threading.Event()
        self.cond = threading.Condition()
        self.proc = None
        self.thread = None
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            self.viewers += 1
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()

    def release(self):
        with self.lock:
            self.viewers -= 1
            if self.viewers == 0:
                self.idle_since = time.monotonic()

    def _idle(self):
        with self.lock:
            return self.viewers == 0 and time.monotonic() - self.idle_since > IDLE_STOP

    def _on_line(self, line):
        if is_auth_error(line):
            self.auth_failed = True

    def _run(self):
        failures = 0
        while not self.auth_failed and not self._idle() and not self.stopping.is_set():
            self.proc = start(self.make_cmd(), self.secrets, prefix=f"[CH {self.ch} {self.quality}] ",
                              on_line=self._on_line, stdout=subprocess.PIPE)
            buf = b""
            while not self._idle():
                chunk = self.proc.stdout.read1(65536)
                if not chunk:
                    break
                buf += chunk
                # each JPEG runs from FFD8 to FFD9; keep only the newest complete one
                while True:
                    start_at = buf.find(b"\xff\xd8")
                    end_at = buf.find(b"\xff\xd9", start_at + 2) if start_at >= 0 else -1
                    if end_at < 0:
                        break
                    frame, buf = buf[start_at:end_at + 2], buf[end_at + 2:]
                    with self.cond:
                        self.jpeg, self.seq, self.last_frame_at = frame, self.seq + 1, time.monotonic()
                        self.cond.notify_all()
                    failures = 0
                if len(buf) > 8_000_000:
                    buf = b""
            stop([self.proc])
            if not self._idle() and not self.auth_failed:
                failures += 1
                self.stopping.wait(retry_delay(failures))

    def close(self):
        self.stopping.set()  # so a camera about to reconnect doesn't start again during shutdown
        if self.proc:
            stop([self.proc])


def load_web_key(args):
    if args.web_key:
        return args.web_key
    try:
        with open(KEY_FILE) as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    import secrets as pysecrets
    key = pysecrets.token_urlsafe(9)
    try:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
    except OSError:
        pass  # still works, the key just changes next time
    return key


def lan_ip(towards):
    """This computer's address on the network that reaches the DVR (what other devices should use)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((towards, 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def can_open_browser():
    # on a Linux box without a desktop (Raspberry Pi, server), webbrowser would start a text browser
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def run_server(args, password, secrets):
    import hmac
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs

    key = load_web_key(args)

    def make_cmd_for(ch, quality):
        def make_cmd():
            *opts, src = source(args, password, ch, 0 if quality == "main" else default_sub())
            vf = "scale='min(1280,iw)':-2," if quality == "main" else ""
            return ["ffmpeg", "-hide_banner", "-loglevel", "error", *opts, "-i", src, "-an",
                    # old DVRs send frames with repeated timestamps, so renumber them in milliseconds
                    # (plain frame numbers get rounded to the same value and crash the JPEG encoder)
                    "-vf", vf + "setpts=N/(1000*TB)", "-enc_time_base:v", "1/1000", "-fps_mode", "passthrough",
                    "-c:v", "mjpeg", "-q:v", "6", "-f", "mjpeg", "-"]
        return make_cmd

    sources = {(ch, q): JpegSource(ch, q, make_cmd_for(ch, q), secrets)
               for ch in args.channels for q in ("sub", "main")}
    page = WEB_PAGE.replace("__CONFIG__", json.dumps({
        "channels": args.channels, "title": f"DVR {'test video' if args.test else args.ip}",
        "allMain": args.main, "layout": args.layout}))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def authorized(self, query):
            given = query.get("key", [""])[0]
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "dvrkey":
                    given = given or value
            return hmac.compare_digest(given.encode(), key.encode())

        def send_plain(self, code, text, extra=()):
            body = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for h in extra:
                self.send_header(*h)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if not self.authorized(query):
                return self.send_plain(403, "Access key missing or wrong. Open the full link shown on the computer running dvr-nvr-viewer.")
            if "key" in query:
                # remember the key in a cookie and drop it from the address bar
                return self.send_plain(302, "", [("Location", "/"), ("Set-Cookie",
                                       f"dvrkey={key}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict")])
            if parts.path == "/":
                body = page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(body)
            bits = parts.path.strip("/").split("/")
            if len(bits) == 3 and bits[0] == "stream" and bits[1].isdigit() and (int(bits[1]), bits[2]) in sources:
                return self.stream(sources[(int(bits[1]), bits[2])])
            if len(bits) == 2 and bits[0] == "status":
                info = {str(ch): {"ok": any(time.monotonic() - s.last_frame_at < STALE_AFTER
                                            for (c, _), s in sources.items() if c == ch and s.seq),
                                  "auth_failed": any(s.auth_failed for (c, _), s in sources.items() if c == ch)}
                        for ch in args.channels}
                body = json.dumps(info).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            self.send_plain(404, "Not found")

        def stream(self, src):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            src.acquire()
            seen, last_sent = 0, 0.0
            try:
                while not src.auth_failed:
                    with src.cond:
                        src.cond.wait_for(lambda: src.seq != seen or src.auth_failed, timeout=10)
                        jpeg, seq = src.jpeg, src.seq
                    if seq == seen or jpeg is None:
                        continue
                    # browsers that fall behind just get the newest frame next, never a backlog
                    wait = 1 / WEB_FPS - (time.monotonic() - last_sent)
                    if wait > 0:
                        time.sleep(wait)
                    seen, last_sent = seq, time.monotonic()
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                src.release()

    server = None
    for port in range(args.web_port, args.web_port + 20):
        try:
            server = ThreadingHTTPServer((args.bind, port), Handler)
            break
        except OSError as e:
            if e.errno not in (48, 98, 10048):  # "address already in use" on macOS / Linux / Windows
                raise
            print(f"Port {port} is already used by another program, trying {port + 1}...")
    if server is None:
        sys.exit(f"Ports {args.web_port}-{args.web_port + 19} are all in use. Pick another with --web-port.")
    args.web_port = port
    server.daemon_threads = True
    default_handle_error = server.handle_error

    def handle_error(request, client_address):
        # a phone or browser closing its connection isn't an error worth a traceback
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        default_handle_error(request, client_address)
    server.handle_error = handle_error
    shared = args.bind in ("0.0.0.0", "")
    host = lan_ip(args.ip if not args.test else "8.8.8.8") if shared else args.bind
    local_url = f"http://{'127.0.0.1' if shared else host}:{args.web_port}/?key={key}"
    if shared:
        print(f"\nRunning. On any phone, tablet or computer on this network, open:\n\n"
              f"    http://{host}:{args.web_port}/?key={key}\n")
        if not args.web_key:  # a key given with --web-key / DVR_WEB_KEY isn't stored anywhere
            print(f"The key is saved in {KEY_FILE} so this link keeps working (delete that file for a new key).")
        print("Only use this on your home network: don't port-forward it to the internet.")
    else:
        print(f"\nRunning. Open: {local_url}")
    print("Keep this running while people are watching; Ctrl+C stops it.")
    if not args.no_browser and can_open_browser():
        webbrowser.open(local_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for s in sources.values():
            s.close()


WEB_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>DVR Cameras</title>
<style>
  :root {
    --bg: #0b0d10; --panel: #15181d; --tile: #000; --text: #e8eaed; --muted: #9aa0a6;
    --accent: #4f8cff; --warn: #ffb020; --gap: 4px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
    font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { display: flex; flex-direction: column; }
  header { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
    padding-top: max(8px, env(safe-area-inset-top)); background: var(--panel); flex-wrap: wrap; }
  header h1 { font-size: 15px; font-weight: 600; margin: 0 auto 0 0; white-space: nowrap; }
  .btns { display: flex; gap: 4px; flex-wrap: wrap; }
  button { background: #252a31; color: var(--text); border: 1px solid #333a43; border-radius: 6px;
    padding: 6px 10px; font: inherit; cursor: pointer; }
  button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  #wall { flex: 1; display: grid; gap: var(--gap); padding: var(--gap); min-height: 0; }
  .tile { position: relative; background: var(--tile); overflow: hidden; border-radius: 4px;
    min-height: 0; cursor: pointer; }
  .tile img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .tile .cap { position: absolute; left: 6px; top: 6px; background: rgba(0,0,0,.6); padding: 2px 6px;
    border-radius: 4px; font-size: 12px; pointer-events: none; }
  .tile .cap.warn { color: var(--warn); }
  .tile.big { outline: 2px solid var(--accent); outline-offset: -2px; }
  .hidden { display: none !important; }
  footer { color: var(--muted); font-size: 12px; padding: 4px 12px;
    padding-bottom: max(4px, env(safe-area-inset-bottom)); }
  @media (max-width: 640px) { footer { display: none; } header h1 { width: 100%; } }
</style>
</head>
<body>
<header>
  <h1 id="title">DVR</h1>
  <div class="btns" id="layouts"></div>
  <div class="btns"><button id="prev" title="Previous">◀</button><button id="next" title="Next">▶</button>
  <button id="fs" title="Fullscreen">⛶</button></div>
</header>
<main id="wall"></main>
<footer>Tap a camera to make it big · double-tap for just that camera · ◀ ▶ for pages or cameras</footer>
<script>
const CFG = __CONFIG__;
const chans = CFG.channels, n = chans.length;
document.getElementById("title").textContent = CFG.title;
document.title = CFG.title;

const wall = document.getElementById("wall");
const tiles = chans.map((ch, i) => {
  const el = document.createElement("div");
  el.className = "tile";
  el.innerHTML = `<img alt="CH ${ch}"><span class="cap">CH ${ch}</span>`;
  const img = el.querySelector("img");
  img.addEventListener("error", () => { // reconnect a dropped stream after a moment
    const want = img.dataset.want; img.removeAttribute("src");
    setTimeout(() => { if (img.dataset.want === want && want) img.src = want + "?t=" + Date.now(); }, 3000);
  });
  let lastTap = 0;
  el.addEventListener("click", () => {
    const now = Date.now();
    if (now - lastTap < 350) { toggleSingle(i); lastTap = 0; return; }
    lastTap = now;
    setTimeout(() => { if (lastTap === now) makeBig(i); }, 360);
  });
  wall.appendChild(el);
  return { el, img, cap: el.querySelector(".cap"), ch };
});

const layouts = ["grid"];
if (n > 4) layouts.push("2x2");
if (n > 9) layouts.push("3x3");
if (n >= 4) layouts.push("1+5");
if (n >= 7) layouts.push("1+7");
layouts.push("single");
const st = { layout: "grid", backTo: "grid", focus: 0, page: 0 };
try { Object.assign(st, JSON.parse(localStorage.getItem("dvr-layout") || "{}")); } catch (e) {}
if (CFG.layout) { st.layout = CFG.layout; st.page = 0; if (CFG.layout !== "single") st.backTo = CFG.layout; } // --layout wins
if (!layouts.includes(st.layout)) st.layout = "grid";
if (st.focus >= n) st.focus = 0;

const lb = document.getElementById("layouts");
layouts.forEach(name => {
  const b = document.createElement("button");
  b.textContent = name === "single" ? "1" : name;
  b.dataset.layout = name;
  b.onclick = () => setLayout(name);
  lb.appendChild(b);
});

function cells() {
  // returns [cols, rows, [[index, col, row, colspan, rowspan]...], pages]
  const portrait = innerWidth < innerHeight;
  const others = [...Array(n).keys()].filter(i => i !== st.focus);
  if (st.layout === "single") return [1, 1, [[st.focus, 0, 0, 1, 1]], 1];
  if (st.layout === "grid") {
    let cols = Math.ceil(Math.sqrt(n));
    if (portrait) cols = Math.min(2, n);
    const rows = Math.ceil(n / cols);
    return [cols, rows, chans.map((_, i) => [i, i % cols, Math.floor(i / cols), 1, 1]), 1];
  }
  if (st.layout === "1+5" || st.layout === "1+7") {
    const k = st.layout === "1+5" ? 3 : 4;
    const slots = [];
    for (let r = 0; r < k - 1; r++) slots.push([k - 1, r]);
    for (let c = 0; c < k; c++) slots.push([c, k - 1]);
    const pages = Math.max(1, Math.ceil(others.length / slots.length));
    st.page = ((st.page % pages) + pages) % pages;
    const chosen = others.slice(st.page * slots.length, (st.page + 1) * slots.length);
    return [k, k, [[st.focus, 0, 0, k - 1, k - 1], ...chosen.map((i, j) => [i, ...slots[j], 1, 1])], pages];
  }
  const [cols, rows] = st.layout.split("x").map(Number), per = cols * rows;
  const pages = Math.max(1, Math.ceil(n / per));
  st.page = ((st.page % pages) + pages) % pages;
  const list = [];
  for (let k = 0, i = st.page * per; i < Math.min(n, (st.page + 1) * per); i++, k++)
    list.push([i, k % cols, Math.floor(k / cols), 1, 1]);
  return [cols, rows, list, pages];
}

function render() {
  const [cols, rows, list, pages] = cells();
  wall.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  wall.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
  const shown = new Set(list.map(c => c[0]));
  list.forEach(([i, c, r, cs, rs]) => {
    const t = tiles[i];
    t.el.classList.remove("hidden");
    t.el.style.gridColumn = `${c + 1} / span ${cs}`;
    t.el.style.gridRow = `${r + 1} / span ${rs}`;
    const big = cs > 1 || st.layout === "single";
    t.el.classList.toggle("big", big && st.layout !== "single");
    // big tiles get the full-quality stream, small ones the lighter one (unless --main: all full quality)
    const want = `/stream/${t.ch}/${big || CFG.allMain ? "main" : "sub"}`;
    if (t.img.dataset.want !== want) { t.img.dataset.want = want; t.img.src = want; }
  });
  tiles.forEach((t, i) => {
    if (shown.has(i)) return;
    t.el.classList.add("hidden");
    if (t.img.dataset.want) { t.img.dataset.want = ""; t.img.removeAttribute("src"); } // stop hidden streams
  });
  lb.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.layout === st.layout));
  const paged = st.layout === "single" || pages > 1;
  document.getElementById("prev").classList.toggle("hidden", !paged);
  document.getElementById("next").classList.toggle("hidden", !paged);
  try { localStorage.setItem("dvr-layout", JSON.stringify(st)); } catch (e) {}
}

function setLayout(name) { if (name !== "single") st.backTo = name; st.layout = name; st.page = 0; render(); }
function makeBig(i) {
  st.focus = i;
  if (st.layout === "single") return;
  if (st.layout === "1+5" || st.layout === "1+7") render(); else setLayout(n <= 6 ? "1+5" : "1+7");
}
function toggleSingle(i) { st.focus = i; setLayout(st.layout === "single" ? st.backTo : "single"); }
function step(d) { if (st.layout === "single") st.focus = (st.focus + d + n) % n; else st.page += d; render(); }
document.getElementById("prev").onclick = () => step(-1);
document.getElementById("next").onclick = () => step(1);
document.getElementById("fs").onclick = () => {
  const d = document;
  if (d.fullscreenElement || d.webkitFullscreenElement) (d.exitFullscreen || d.webkitExitFullscreen).call(d);
  else { const e = d.documentElement; (e.requestFullscreen || e.webkitRequestFullscreen || (() => {})).call(e); }
};
addEventListener("keydown", e => {
  if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "Escape" && st.layout === "single") setLayout(st.backTo);
  else if (e.key.toLowerCase() === "l") setLayout(layouts[(layouts.indexOf(st.layout) + 1) % layouts.length]);
  else if (/^[1-9]$/.test(e.key)) { const i = chans.indexOf(+e.key); if (i >= 0) { st.focus = i; setLayout("single"); } }
});
addEventListener("resize", () => { if (st.layout === "grid") render(); });

async function poll() { // captions show cameras that have stopped sending video
  try {
    const s = await (await fetch("/status/all", { cache: "no-store" })).json();
    tiles.forEach(t => {
      const info = s[t.ch] || {};
      const watching = !!t.img.dataset.want;
      const bad = info.auth_failed ? "login rejected" : (watching && !info.ok ? "connecting…" : "");
      t.cap.textContent = `CH ${t.ch}` + (bad ? `  ${bad}` : "");
      t.cap.classList.toggle("warn", !!bad);
    });
  } catch (e) {}
  setTimeout(poll, 3000);
}
render();
poll();
</script>
</body>
</html>
"""


def quit_on_signal(signum, frame):
    # being killed (or the terminal closing) is handled like Ctrl+C, so every camera
    # connection is closed properly instead of being left open on the DVR
    raise KeyboardInterrupt


def main():
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):  # Windows has no SIGHUP
            signal.signal(getattr(signal, name), quit_on_signal)
    args = parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found. Install it (macOS: brew install ffmpeg, Windows: winget install Gyan.FFmpeg,\n"
                 "Linux: sudo apt install ffmpeg) and try again.")

    password = ""
    if not args.test:
        while not args.user:
            args.user = input("DVR username: ").strip()
        password = os.environ.get("DVR_PASS") or getpass.getpass(f"DVR password for {args.user}: ")
        ok = check_login(args, password, args.channels[0])
        if ok is False:
            print("The DVR rejected this username/password. Nothing else was tried.\n"
                  "If it worked before, the account is probably locked after several wrong tries:\n"
                  "wait about 30 minutes (or restart the DVR) before trying again.", file=sys.stderr)
            # distinct exit code so a service manager can be told never to retry it (that would lock the account)
            sys.exit(EXIT_LOGIN_REJECTED)
        if ok:
            print("Login OK.")
    secrets = [quote(password, safe=""), password]
    src = "test video" if args.test else args.ip
    print(f"Cameras {' '.join(map(str, args.channels))} from {src}")
    run_server(args, password, secrets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
