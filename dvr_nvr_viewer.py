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
  dvr-nvr-viewer                                            (set up the DVR in the browser)
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
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from types import SimpleNamespace
from urllib.parse import quote, urlsplit, urlunsplit

__version__ = "0.2.0"

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
    p.add_argument("--ip", default=os.environ.get("DVR_IP"),
                   help="DVR/NVR address, e.g. 192.168.1.64 (or leave it out and set it up in the browser)")
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
    p.add_argument("--no-local-admin", action="store_true",
                   help="require the admin link for settings even on this computer (for shared computers)")
    p.add_argument("--test", action="store_true", help="use fake test video instead of the DVR")
    args = p.parse_args()
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


class DvrUnreachable(Exception):
    """The DVR didn't answer at all (as opposed to rejecting the login)."""


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
            raise DvrUnreachable(f"The DVR at {host}:{port} didn't answer within 15 seconds (twice). "
                                 "It may be busy (too many viewers or streams at once) or restarting. Close other "
                                 "viewers (vendor apps, VLC, other copies of this), wait a minute and retry.")
        except OSError as e:
            hint = (" On macOS, check System Settings > Privacy & Security > Local Network for the app running this."
                    if "route" in str(e).lower() else "")
            raise DvrUnreachable(f"Can't reach the DVR at {host}:{port}: {e}.{hint}")
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
ADMIN_KEY_FILE = os.path.join(os.path.expanduser("~"), ".dvr_nvr_viewer_admin_key")
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".dvr_nvr_viewer.json")  # settings made on the setup page
BRAND_LABELS = [("dahua", "Dahua (Amcrest, Lorex, CP Plus, ...)"), ("hikvision", "Hikvision (HiLook, Annke, ...)"),
                ("xmeye", "XMEye / NetSurveillance"), ("custom", "Other: enter the RTSP address")]


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
        with self.cond:
            self.cond.notify_all()  # wake viewers so their streams end
        if self.proc:
            stop([self.proc])


def write_private(path, text):
    """Write a file only this user can read (keys, saved settings)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)  # in case the file already existed with looser permissions
    except OSError:
        pass


def load_or_create_key(path, given=None):
    """Returns (key, saved): a key given on the command line, else the saved one, else a new saved one."""
    if given:
        return given, False
    try:
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key, True
    except OSError:
        pass
    import secrets as pysecrets
    key = pysecrets.token_urlsafe(9)
    try:
        write_private(path, key)
        return key, True
    except OSError:
        return key, False  # still works, the key just changes next time


def parse_channels(text):
    """'1-4', '1 2 4' or '1,2,4' -> [1, 2, 4]."""
    chans = []
    for part in text.replace(",", " ").split():
        lo, sep, hi = part.partition("-")
        if not lo.isdigit() or (sep and not hi.isdigit()):
            raise ValueError(f"'{part}' isn't a channel number or range like 1-8")
        for ch in range(int(lo), int(hi if sep else lo) + 1):
            if not 1 <= ch <= 64:
                raise ValueError("Channel numbers go from 1 to 64")
            if ch not in chans:
                chans.append(ch)
    if not chans:
        raise ValueError("Enter at least one camera channel, e.g. 1-4")
    if len(chans) > 32:
        raise ValueError("At most 32 cameras at once")
    return chans


def make_settings(ip, brand, port, user, channels, url_template=None, main=False):
    """The DVR settings the viewer runs with (same attribute names as the command-line options)."""
    return SimpleNamespace(ip=ip, brand=brand, port=port, user=user, channels=channels, main=main, test=False,
                           url_template=url_template if brand == "custom" else BRANDS[brand])


def load_settings():
    try:
        with open(CONFIG_FILE) as f:
            d = json.load(f)
        brand = d.get("brand", "dahua")
        if brand not in BRANDS and brand != "custom":
            return None, ""
        cfg = make_settings(d["ip"], brand, int(d.get("port", 554)), d["user"], [int(c) for c in d["channels"]],
                            d.get("url_template"), bool(d.get("main")))
        return cfg, d.get("password") or ""
    except (OSError, ValueError, KeyError, TypeError):
        return None, ""


def save_settings(cfg, password, remember):
    d = {"ip": cfg.ip, "brand": cfg.brand, "port": cfg.port, "user": cfg.user, "channels": cfg.channels,
         "main": cfg.main}
    if cfg.brand == "custom":
        d["url_template"] = cfg.url_template
    if remember:
        d["password"] = password
    write_private(CONFIG_FILE, json.dumps(d, indent=2))


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


def run_server(args, cfg, password, draft=None):
    """Serve the viewer. cfg is None until the DVR is set up (then /setup asks for it).
    draft: saved settings to pre-fill the setup page with when they couldn't be used as they are."""
    import hmac
    import html
    import secrets as pysecrets
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs

    view_key, view_key_saved = load_or_create_key(KEY_FILE, args.web_key)
    admin_key, _ = load_or_create_key(ADMIN_KEY_FILE)
    form_token = pysecrets.token_urlsafe(16)  # proves a settings change came from our own setup page
    lock = threading.Lock()
    state = {"cfg": None, "password": "", "sources": {}, "draft": draft}
    links = {}

    def make_cmd_for(cfg, password, ch, quality):
        def make_cmd():
            *opts, src = source(cfg, password, ch, 0 if quality == "main" else default_sub())
            vf = "scale='min(1280,iw)':-2," if quality == "main" else ""
            return ["ffmpeg", "-hide_banner", "-loglevel", "error", *opts, "-i", src, "-an",
                    # old DVRs send frames with repeated timestamps, so renumber them in milliseconds
                    # (plain frame numbers get rounded to the same value and crash the JPEG encoder)
                    "-vf", vf + "setpts=N/(1000*TB)", "-enc_time_base:v", "1/1000", "-fps_mode", "passthrough",
                    "-c:v", "mjpeg", "-q:v", "6", "-f", "mjpeg", "-"]
        return make_cmd

    def apply(cfg, password):
        secrets_ = [quote(password, safe=""), password]
        new = {} if cfg is None else {
            (ch, q): JpegSource(ch, q, make_cmd_for(cfg, password, ch, q), secrets_)
            for ch in cfg.channels for q in ("sub", "main")}
        with lock:
            old = state["sources"]
            state.update(cfg=cfg, password=password, sources=new)
        # closing waits for each ffmpeg to finish, so don't hold up the request doing it
        threading.Thread(target=lambda: [s.close() for s in old.values()], daemon=True).start()

    apply(cfg, password)

    def snapshot():
        with lock:
            return state["cfg"], state["sources"]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def cookie(self, name):
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
            return ""

        @staticmethod
        def matches(given, key):
            return bool(given) and hmac.compare_digest(given.encode(), key.encode())

        def is_admin(self, query):
            # the computer running this is trusted; other devices need the admin link
            local = self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
            return ((local and not args.no_local_admin)
                    or self.matches(query.get("admin", [""])[0] or self.cookie("dvradmin"), admin_key))

        def can_view(self, query):
            return self.is_admin(query) or self.matches(query.get("key", [""])[0] or self.cookie("dvrkey"), view_key)

        def send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for h in extra:
                self.send_header(*h)
            self.end_headers()
            self.wfile.write(body)

        def redirect(self, where, cookie=None, code=302):
            extra = [("Location", where)]
            if cookie:
                extra.append(("Set-Cookie", f"{cookie}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict"))
            self.send(code, "", "text/plain", extra)

        def message_page(self, code, title, text):
            self.send(code, MESSAGE_PAGE.replace("__TITLE__", html.escape(title)).replace("__TEXT__", html.escape(text)))

        def do_GET(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if "admin" in query:
                if not self.matches(query["admin"][0], admin_key):
                    return self.message_page(403, "Wrong admin link", "Open the admin link exactly as dvr-nvr-viewer printed it.")
                # remember it in a cookie and drop it from the address bar
                return self.redirect("/setup", f"dvradmin={admin_key}")
            if "key" in query:
                if not self.matches(query["key"][0], view_key):
                    return self.message_page(403, "Wrong link", "Open the link exactly as dvr-nvr-viewer printed it.")
                return self.redirect("/", f"dvrkey={view_key}")
            cfg, sources = snapshot()
            if parts.path == "/setup":
                if not self.is_admin(query):
                    return self.message_page(403, "Settings are for the admin",
                                             "Changing the DVR settings needs the admin link that dvr-nvr-viewer "
                                             "printed when it started (or use the computer it runs on).")
                return self.setup_page()
            if not self.can_view(query):
                return self.message_page(403, "Access key missing or wrong",
                                         "Open the full link shown on the computer running dvr-nvr-viewer.")
            if parts.path == "/":
                if cfg is None:
                    if self.is_admin(query):
                        return self.redirect("/setup")
                    return self.message_page(200, "Not set up yet",
                                             "This viewer isn't connected to a DVR yet. Someone with the admin link "
                                             "(or the computer running dvr-nvr-viewer) needs to set it up first.")
                page = WEB_PAGE.replace("__CONFIG__", json.dumps({
                    "channels": cfg.channels, "title": "DVR test video" if cfg.test else f"DVR {cfg.ip}",
                    "allMain": cfg.main, "layout": args.layout, "admin": self.is_admin(query)}))
                return self.send(200, page)
            bits = parts.path.strip("/").split("/")
            if len(bits) == 3 and bits[0] == "stream" and bits[1].isdigit() and (int(bits[1]), bits[2]) in sources:
                return self.stream(sources[(int(bits[1]), bits[2])])
            if len(bits) == 2 and bits[0] == "status" and cfg is not None:
                info = {str(ch): {"ok": any(time.monotonic() - s.last_frame_at < STALE_AFTER
                                            for (c, _), s in sources.items() if c == ch and s.seq),
                                  "auth_failed": any(s.auth_failed for (c, _), s in sources.items() if c == ch)}
                        for ch in cfg.channels}
                return self.send(200, json.dumps(info), "application/json")
            self.send(404, "Not found", "text/plain")

        def setup_page(self, values=None, error=""):
            cfg, _ = snapshot()
            base = cfg if cfg is not None and not cfg.test else state["draft"]
            v = {"ip": "", "brand": "dahua", "port": "554", "user": "", "channels": "1-4", "url_template": "",
                 "main": False, "remember": True}
            if base is not None:
                v.update(ip=base.ip, brand=base.brand, port=str(base.port), user=base.user, main=base.main,
                         channels=" ".join(map(str, base.channels)),
                         url_template=base.url_template if base.brand == "custom" else "")
            if values:
                v.update(values)
            have_password = cfg is not None and not cfg.test and bool(state["password"])
            options = "".join(f'<option value="{b}"{" selected" if v["brand"] == b else ""}>{label}</option>'
                              for b, label in BRAND_LABELS)
            fill = {
                "__ERROR__": f'<p class="error">{html.escape(error)}</p>' if error else "",
                "__TOKEN__": form_token, "__IP__": html.escape(v["ip"]), "__PORT__": html.escape(v["port"]),
                "__USER__": html.escape(v["user"]), "__CHANNELS__": html.escape(v["channels"]),
                "__TEMPLATE__": html.escape(v["url_template"]), "__BRANDS__": options,
                "__PW_HINT__": "leave empty to keep the current one" if have_password else "",
                "__MAIN__": " checked" if v["main"] else "", "__REMEMBER__": " checked" if v["remember"] else "",
                "__BACK__": '<a href="/">Back to the cameras</a>' if cfg is not None else "",
                "__SHARE__": html.escape(links.get("view", "")), "__ADMIN__": html.escape(links.get("admin", "")),
            }
            page = SETUP_PAGE
            for k, val in fill.items():
                page = page.replace(k, val)
            self.send(200, page)

        def do_POST(self):
            if urlsplit(self.path).path != "/setup":
                return self.send(404, "Not found", "text/plain")
            if not self.is_admin({}):
                return self.message_page(403, "Settings are for the admin", "This needs the admin link.")
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.message_page(403, "Blocked", "Settings can only be changed from this viewer's own page.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > 20000:
                return self.message_page(413, "Too much data", "That form was far too big.")
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode(errors="replace")).items()}
            if not self.matches(form.get("token", ""), form_token):
                return self.message_page(403, "Blocked", "This form is out of date. Go back, reload the page and try again.")
            values = {k: form.get(k, "").strip() for k in ("ip", "brand", "port", "user", "channels", "url_template")}
            values.update(main=form.get("main") == "on", remember=form.get("remember") == "on")
            password = form.get("password", "")
            try:
                new = self.validate(values, password)
            except ValueError as e:
                return self.setup_page(values, str(e))
            if not password:
                password = state["password"]
            try:
                ok = check_login(new, password, new.channels[0])
            except DvrUnreachable as e:
                return self.setup_page(values, str(e))
            if ok is False:
                return self.setup_page(values, "The DVR rejected this username or password. Nothing else was tried. "
                                       "If it worked before, the account may be locked after several wrong tries: "
                                       "wait about 30 minutes (or restart the DVR) before trying again.")
            try:
                save_settings(new, password, values["remember"])
            except OSError as e:
                print(f"Couldn't save the settings ({e}); they'll only last until this stops.", file=sys.stderr)
            state["draft"] = None
            apply(new, password)
            print(f"Settings changed: cameras {' '.join(map(str, new.channels))} from {new.ip}")
            self.redirect("/", code=303)

        def validate(self, v, password):
            if not v["ip"] or len(v["ip"]) > 253 or not all(c.isalnum() or c in ".-:[]" for c in v["ip"]):
                raise ValueError("Enter the DVR's address, like 192.168.1.64")
            if v["brand"] not in BRANDS and v["brand"] != "custom":
                raise ValueError("Pick the DVR brand")
            if v["brand"] == "custom" and not (v["url_template"].startswith("rtsp://") and "{channel}" in v["url_template"]):
                raise ValueError("A custom address must start with rtsp:// and contain {channel}")
            if not v["port"].isdigit() or not 1 <= int(v["port"]) <= 65535:
                raise ValueError("The RTSP port is a number, usually 554")
            if not v["user"] or len(v["user"]) > 64:
                raise ValueError("Enter the DVR username")
            new = make_settings(v["ip"], v["brand"], int(v["port"]), v["user"], parse_channels(v["channels"]),
                                v["url_template"], v["main"])
            if not password:
                cfg, _ = snapshot()
                same_dvr = (cfg is not None and not cfg.test and state["password"] and cfg.ip == new.ip
                            and cfg.port == new.port and cfg.user == new.user and cfg.url_template == new.url_template)
                if not same_dvr:
                    # never send a kept password to a different address or account
                    raise ValueError("Enter the password (the saved one is only reused for the same DVR and user)")
            return new

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
                while not src.auth_failed and not src.stopping.is_set():
                    with src.cond:
                        src.cond.wait_for(lambda: src.seq != seen or src.auth_failed or src.stopping.is_set(),
                                          timeout=10)
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
    host = lan_ip(cfg.ip if cfg is not None and not cfg.test else "8.8.8.8") if shared else args.bind
    links["view"] = f"http://{host}:{args.web_port}/?key={view_key}"
    links["admin"] = f"http://{host}:{args.web_port}/?admin={admin_key}"
    local = f"http://{'127.0.0.1' if shared else host}:{args.web_port}"
    print(f"\nRunning. To watch on any phone, tablet or computer on this network, open:\n\n"
          f"    {links['view']}\n\n"
          f"To change the DVR settings from another device, use the admin link (keep it to yourself):\n\n"
          f"    {links['admin']}\n")
    if view_key_saved:
        print(f"Keys are saved in {os.path.dirname(KEY_FILE) or '.'} so these links keep working "
              f"(delete {os.path.basename(KEY_FILE)} or {os.path.basename(ADMIN_KEY_FILE)} for new ones).")
    if shared:
        print("Only use this on your home network: don't port-forward it to the internet.")
    print("Keep this running while people are watching; Ctrl+C stops it.")
    if not args.no_browser and can_open_browser():
        if args.no_local_admin:
            webbrowser.open(links["admin"] if cfg is None else f"{local}/?key={view_key}")
        else:
            webbrowser.open(f"{local}/setup" if cfg is None else f"{local}/?key={view_key}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for s in state["sources"].values():
            s.close()


PAGE_STYLE = r"""
  :root { --bg: #0b0d10; --panel: #15181d; --field: #0f1216; --line: #333a43; --text: #e8eaed;
    --muted: #9aa0a6; --accent: #4f8cff; --error: #ff6b6b; }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 560px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  p { color: var(--muted); margin: 0 0 16px; }
  a { color: var(--accent); }
  form { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 18px; }
  label { display: block; font-weight: 600; margin: 14px 0 4px; }
  label:first-child { margin-top: 0; }
  .hint { display: block; font-weight: 400; color: var(--muted); font-size: 13px; }
  input[type=text], input[type=password], select { width: 100%; padding: 10px 12px; font: inherit;
    color: var(--text); background: var(--field); border: 1px solid var(--line); border-radius: 6px; }
  input:focus, select:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
  .check { display: flex; gap: 8px; align-items: flex-start; font-weight: 400; margin-top: 12px; }
  .check input { margin-top: 4px; }
  .row { display: grid; grid-template-columns: 1fr 110px; gap: 12px; }
  button { margin-top: 18px; width: 100%; padding: 12px; font: inherit; font-weight: 600; color: #fff;
    background: var(--accent); border: 0; border-radius: 6px; cursor: pointer; }
  button[disabled] { opacity: .6; cursor: wait; }
  .error { color: var(--error); background: rgba(255,107,107,.1); border: 1px solid rgba(255,107,107,.4);
    border-radius: 6px; padding: 10px 12px; }
  .links { margin-top: 22px; font-size: 13px; color: var(--muted); word-break: break-all; }
  .links code { color: var(--text); }
  .hidden { display: none; }
"""

MESSAGE_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title><style>""" + PAGE_STYLE + r"""</style></head>
<body><main><h1>__TITLE__</h1><p>__TEXT__</p></main></body></html>
"""

SETUP_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DVR settings</title><style>""" + PAGE_STYLE + r"""</style></head>
<body><main>
<h1>Connect to your DVR / NVR</h1>
<p>Enter the details you'd use in the DVR's own app. The login is checked once; if the DVR rejects it,
nothing is retried, so the account can't get locked. __BACK__</p>
__ERROR__
<form method="post" action="/setup" id="f">
  <input type="hidden" name="token" value="__TOKEN__">
  <div class="row">
    <div><label for="ip">Address <span class="hint">IP or name, e.g. 192.168.1.64</span></label>
      <input type="text" id="ip" name="ip" value="__IP__" required autocomplete="off" inputmode="url"></div>
    <div><label for="port">RTSP port <span class="hint">usually 554</span></label>
      <input type="text" id="port" name="port" value="__PORT__" inputmode="numeric"></div>
  </div>
  <label for="brand">Brand</label>
  <select id="brand" name="brand">__BRANDS__</select>
  <div id="tpl" class="hidden">
    <label for="url_template">RTSP address <span class="hint">placeholders: {user} {password} {ip} {port}
      {channel}, and {sub} (0 = full quality, 1 = lighter) or {stream} (1 = full, 2 = lighter)</span></label>
    <input type="text" id="url_template" name="url_template" value="__TEMPLATE__" autocomplete="off"
      placeholder="rtsp://{user}:{password}@{ip}:{port}/ch{channel}/{stream}">
  </div>
  <label for="user">Username</label>
  <input type="text" id="user" name="user" value="__USER__" required autocomplete="username">
  <label for="password">Password <span class="hint">__PW_HINT__</span></label>
  <input type="password" id="password" name="password" autocomplete="current-password">
  <label for="channels">Cameras <span class="hint">channel numbers, e.g. 1-8 or 1 2 4 5 6</span></label>
  <input type="text" id="channels" name="channels" value="__CHANNELS__" required>
  <label class="check"><input type="checkbox" name="main"__MAIN__>
    <span>Full quality for every camera <span class="hint">Uses much more network and CPU. Otherwise only
    enlarged cameras are full quality.</span></span></label>
  <label class="check"><input type="checkbox" name="remember"__REMEMBER__>
    <span>Remember the password on this computer <span class="hint">So it reconnects after a restart.
    Stored in a file only your user account can read.</span></span></label>
  <button type="submit" id="go">Save and connect</button>
</form>
<div class="links">
  Watch link for other devices: <code>__SHARE__</code><br>
  Admin link (to change these settings from another device; keep it private): <code>__ADMIN__</code>
</div>
<script>
const brand = document.getElementById("brand"), tpl = document.getElementById("tpl");
function showTemplate() { tpl.classList.toggle("hidden", brand.value !== "custom"); }
brand.addEventListener("change", showTemplate); showTemplate();
document.getElementById("f").addEventListener("submit", () => {
  const b = document.getElementById("go"); b.disabled = true; b.textContent = "Checking the login…";
});
</script>
</main></body></html>
"""


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
  <button id="fs" title="Fullscreen">⛶</button>
  <button id="settings" class="hidden" title="DVR settings" onclick="location.href='/setup'">⚙</button></div>
</header>
<main id="wall"></main>
<footer>Tap a camera to make it big · double-tap for just that camera · ◀ ▶ for pages or cameras</footer>
<script>
const CFG = __CONFIG__;
const chans = CFG.channels, n = chans.length;
document.getElementById("title").textContent = CFG.title;
if (CFG.admin) document.getElementById("settings").classList.remove("hidden");
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

    cfg, password, draft = None, "", None
    if args.test:
        cfg = SimpleNamespace(test=True, ip=None, brand="test", port=args.port, user="", channels=args.channels,
                              url_template=args.url_template, main=args.main)
    elif args.ip:
        # set up on the command line
        cfg = SimpleNamespace(test=False, ip=args.ip, brand=args.brand if not args.url_template else "custom",
                              port=args.port, user=args.user, channels=args.channels,
                              url_template=args.url_template, main=args.main)
        if args.url_template == BRANDS[args.brand]:
            cfg.brand = args.brand
        while not cfg.user:
            cfg.user = input("DVR username: ").strip()
        password = os.environ.get("DVR_PASS") or getpass.getpass(f"DVR password for {cfg.user}: ")
        try:
            ok = check_login(cfg, password, cfg.channels[0])
        except DvrUnreachable as e:
            sys.exit(str(e))
        if ok is False:
            print("The DVR rejected this username/password. Nothing else was tried.\n"
                  "If it worked before, the account is probably locked after several wrong tries:\n"
                  "wait about 30 minutes (or restart the DVR) before trying again.", file=sys.stderr)
            # distinct exit code so a service manager can be told never to retry it (that would lock the account)
            sys.exit(EXIT_LOGIN_REJECTED)
        if ok:
            print("Login OK.")
    else:
        # set up in the browser, or use what was set up there last time
        saved, saved_password = load_settings()
        if saved is None:
            print("No DVR set up yet: the setup page will ask for its address, username and password.")
        elif not saved_password:
            draft = saved
            print(f"The password for {saved.ip} wasn't remembered: enter it on the setup page.")
        else:
            try:
                ok = check_login(saved, saved_password, saved.channels[0])
            except DvrUnreachable as e:
                ok = None  # start anyway; the cameras reconnect once the DVR is back
                print(f"Warning: {e}", file=sys.stderr)
            if ok is False:
                draft = saved
                print(f"The DVR at {saved.ip} rejected the saved password (nothing else was tried). "
                      "Enter the right one on the setup page.", file=sys.stderr)
            else:
                cfg, password = saved, saved_password
                print(f"Using the saved settings for {saved.ip}.")

    if cfg is not None:
        print(f"Cameras {' '.join(map(str, cfg.channels))} from {'test video' if cfg.test else cfg.ip}")
    run_server(args, cfg, password, draft)
    return 0


if __name__ == "__main__":
    sys.exit(main())
