"""Tests for dvr-nvr-viewer. Run with:  python -m unittest discover -s tests -v

The end-to-end tests use --test (fake video), so they need ffmpeg but no real DVR.
"""
import base64
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import dvr_nvr_viewer as app  # noqa: E402

SCRIPT = os.path.join(ROOT, "dvr_nvr_viewer.py")
SETUP = os.path.join(ROOT, "deploy", "linux", "setup.sh")


def parse(*argv):
    with mock.patch.object(sys, "argv", ["dvr-nvr-viewer", *argv]):
        return app.parse_args()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Arguments(unittest.TestCase):
    def test_ip_is_required_unless_testing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DVR_IP", None)
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                parse("1", "2")
        self.assertTrue(parse("--test").test)

    def test_brand_urls(self):
        cases = {
            "dahua": "rtsp://admin:p%40ss@10.0.0.5:554/cam/realmonitor?channel=3&subtype=1",
            "hikvision": "rtsp://admin:p%40ss@10.0.0.5:554/Streaming/Channels/302",
            "xmeye": "rtsp://10.0.0.5:554/user=admin&password=p%40ss&channel=3&stream=1.sdp",
        }
        for brand, expected in cases.items():
            args = parse("--ip", "10.0.0.5", "--user", "admin", "--brand", brand, "3")
            self.assertEqual(app.source(args, "p@ss", 3, sub=1)[-1], expected, brand)
        main = parse("--ip", "10.0.0.5", "--user", "admin", "--brand", "hikvision")
        self.assertTrue(app.source(main, "x", 2, sub=0)[-1].endswith("/Streaming/Channels/201"))

    def test_custom_template(self):
        args = parse("--ip", "10.0.0.5", "--user", "u", "--url-template", "rtsp://{ip}/live{channel}_{stream}")
        self.assertEqual(app.source(args, "pw", 4, sub=0)[-1], "rtsp://10.0.0.5/live4_1")


class Helpers(unittest.TestCase):
    def test_login_rejection_is_recognised_precisely(self):
        self.assertTrue(app.is_auth_error("Error opening input: Server returned 401 Unauthorized (authorization failed)"))
        # ffmpeg prints memory addresses that can contain "401"; that must not count as a rejected login
        self.assertFalse(app.is_auth_error("[CH 1 sub] [vost#0:0/mjpeg @ 0x147401c90] Error encoding a frame"))

    def test_retry_backs_off(self):
        self.assertEqual([app.retry_delay(n) for n in range(1, 7)], [3, 6, 12, 24, 30, 30])

    def test_password_is_masked(self):
        out = []
        r, w = os.pipe()
        os.write(w, b"Error opening rtsp://admin:p%40ss@10.0.0.5/x\n")
        os.close(w)
        with os.fdopen(r, "rb") as stream, mock.patch("sys.stderr") as err:
            err.write.side_effect = out.append
            app.relay_masked(stream, ["p%40ss", "p@ss"])
        self.assertEqual("".join(out), "Error opening rtsp://admin:****@10.0.0.5/x\n")


class FakeDvr:
    """A tiny RTSP server that answers one login check."""

    def __init__(self, good_user, good_pass, digest_only=False):
        self.good = "Basic " + base64.b64encode(f"{good_user}:{good_pass}".encode()).decode()
        self.digest_only = digest_only
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                req = conn.recv(4096).decode()
                if self.digest_only:
                    reply = 'RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\nWWW-Authenticate: Digest realm="x", nonce="n"\r\n\r\n'
                elif self.good in req:
                    reply = "RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n"
                else:
                    reply = 'RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\nWWW-Authenticate: Basic realm="x"\r\n\r\n'
                conn.sendall(reply.encode())

    def close(self):
        self.sock.close()


class LoginCheck(unittest.TestCase):
    def check(self, dvr, password):
        args = parse("--ip", "127.0.0.1", "--port", str(dvr.port), "--user", "admin")
        return app.check_login(args, password, 1)

    def test_right_and_wrong_password(self):
        dvr = FakeDvr("admin", "good")
        self.addCleanup(dvr.close)
        self.assertIs(self.check(dvr, "good"), True)
        self.assertIs(self.check(dvr, "bad"), False)

    def test_digest_only_dvr_is_unknown_not_rejected(self):
        dvr = FakeDvr("admin", "good", digest_only=True)
        self.addCleanup(dvr.close)
        self.assertIsNone(self.check(dvr, "good"))


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class WebServer(unittest.TestCase):
    """Runs the real app with fake video and talks to it like a browser."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        # keep the app's own output, so a failure to start explains itself
        cls.log = tempfile.TemporaryFile()
        cls.proc = subprocess.Popen(
            [sys.executable, "-u", SCRIPT, "1", "2", "4", "--test", "--no-browser", "--bind", "127.0.0.1",
             "--web-port", str(cls.port), "--web-key", "testkey", "--layout", "1+5"],
            stdout=cls.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60  # CI machines can be slow the first time a program runs
        while time.time() < deadline and cls.proc.poll() is None:
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            exited = cls.proc.poll()
            cls.proc.kill()
            cls.proc.wait()
            cls.log.seek(0)
            output = cls.log.read().decode(errors="replace")
            cls.log.close()
            state = f"exited with code {exited}" if exited is not None else "still not listening after 60s"
            raise RuntimeError(f"server didn't start ({state}). Its output:\n{output}")
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.log.close()

    def browser(self):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        opener.open(self.base + "/?key=testkey", timeout=10).read()
        return opener

    def status(self, url, opener=None):
        try:
            return (opener or urllib.request.build_opener()).open(url, timeout=10).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_access_key_is_required(self):
        self.assertEqual(self.status(self.base + "/"), 403)
        self.assertEqual(self.status(self.base + "/?key=wrong"), 403)
        self.assertEqual(self.status(self.base + "/stream/1/sub"), 403)

    def test_page_remembers_key_and_gets_settings(self):
        opener = self.browser()
        html = opener.open(self.base + "/", timeout=10).read().decode()
        config = json.loads(html.split("const CFG = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual(config["channels"], [1, 2, 4])
        self.assertEqual(config["layout"], "1+5")

    def test_stream_sends_live_jpeg_frames(self):
        opener = self.browser()
        for quality in ("sub", "main"):
            resp = opener.open(f"{self.base}/stream/2/{quality}", timeout=15)
            self.assertIn("multipart/x-mixed-replace", resp.headers["Content-Type"])
            data, t0 = b"", time.time()
            while time.time() - t0 < 4 and data.count(b"\xff\xd9") < 10:
                data += resp.read1(65536)
            resp.close()
            self.assertGreaterEqual(data.count(b"\xff\xd9"), 10, f"{quality}: too few frames")

    def test_status_and_unknown_camera(self):
        opener = self.browser()
        info = json.loads(opener.open(self.base + "/status/all", timeout=10).read())
        self.assertEqual(sorted(info), ["1", "2", "4"])
        self.assertEqual(self.status(self.base + "/stream/3/sub", opener), 404)


@unittest.skipIf(sys.platform == "win32" or not shutil.which("bash"), "installer is for Linux; needs bash")
class Installer(unittest.TestCase):
    def bash(self, script, *args):
        # load the installer's functions without running it; extra args become $1, $2, ...
        prelude = f'id() {{ echo 0; }}; DVR_SETUP_SELFTEST=1 source "{SETUP}"\n'
        return subprocess.run(["bash", "-c", prelude + script, "_", *args],
                              capture_output=True, text=True, check=True).stdout

    def test_syntax(self):
        subprocess.run(["bash", "-n", SETUP], check=True)

    def test_driver_choice(self):
        drivers = "\n".join([
            "drv:///sample.drv/generic.ppd Generic PCL Laser Printer",
            "drv:///brlaser.drv/br2540dw.ppd Brother DCP-L2540DW series, using brlaser v6",
            "drv:///hpcups.drv/hp-laserjet_pro_mfp_m125-m126.ppd HP LaserJet Pro MFP M125-M126, hpcups 3.22",
        ])
        out = self.bash(f'''
            D="{drivers}"
            printf '%s\\n' "$D" | pick_driver "usb://Brother/DCP-L2540DW%20series?serial=1"
            printf '%s\\n' "$D" | pick_driver "usb://HP/LaserJet%20Pro%20MFP%20M126nw?serial=2"
            printf '%s\\n' "$D" | pick_driver "usb://Canon/LBP2900?serial=3" || echo none
        ''').split()
        self.assertEqual(out, ["drv:///brlaser.drv/br2540dw.ppd",
                               "drv:///hpcups.drv/hp-laserjet_pro_mfp_m125-m126.ppd", "none"])

    def test_settings_survive_tricky_passwords(self):
        for pw in ['simple', 'p@ss "q" \\back$dollar', '`touch /tmp/should-not-exist`', 'a=b', 'end\\']:
            out = self.bash('''
                f=$(mktemp); echo "DVR_PASS=$(env_quote "$1")" > "$f"
                DVR_PASS=""; load_env "$f"; rm -f "$f"; printf '%s' "$DVR_PASS"
            ''', pw)
            self.assertEqual(out, pw)
        self.assertFalse(os.path.exists("/tmp/should-not-exist"))


if __name__ == "__main__":
    unittest.main()
