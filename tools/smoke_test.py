"""Starts the download built by build_exe.py (in dist/release/) and checks that it really works:
the page answers, the bundled ffmpeg makes video, and the Stop button shuts everything down.

    python tools/smoke_test.py
"""
import glob
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, n=None, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read(n) if n else r.read()


def main():
    built = glob.glob(os.path.join(ROOT, "dist", "release", "dvr-nvr-viewer-*"))
    if len(built) != 1:
        sys.exit(f"expected one download in dist/release, found {built}")
    tmp = tempfile.mkdtemp()
    exe = built[0]
    if exe.endswith(".zip"):  # macOS: run the program inside the .app
        subprocess.run(["ditto", "-x", "-k", exe, tmp], check=True)
        exe = os.path.join(tmp, "DVR Viewer.app", "Contents", "MacOS", "DVR Viewer")
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    env = dict(os.environ, HOME=home, USERPROFILE=home)  # keep its keys and log out of the real home
    port = free_port()
    proc = subprocess.Popen([exe, "--no-browser", "--test", "--bind", "127.0.0.1", "--web-port", str(port), "1"], env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):  # the one-file builds unpack themselves first, which can take a while
            try:
                if get(base + "/hello") == b"dvr-nvr-viewer":
                    break
            except OSError:
                pass
            if proc.poll() is not None:
                sys.exit(f"it exited with code {proc.returncode}")
            time.sleep(1)
        else:
            sys.exit("the page never answered")
        video = get(base + "/stream/1/sub", 20000, timeout=60)
        if b"\xff\xd8" not in video:
            sys.exit("no JPEG frames: the bundled ffmpeg didn't work")
        token = re.search(r'name="token" value="([^"]+)"', get(base + "/setup").decode()).group(1)
        req = urllib.request.Request(base + "/quit", data=urllib.parse.urlencode({"token": token}).encode())
        get(req)
        proc.wait(timeout=30)
        print(f"OK: {os.path.basename(built[0])} served the page, made video and stopped cleanly")
    finally:
        if proc.poll() is None:
            proc.kill()
        log = os.path.join(home, ".dvr_nvr_viewer.log")
        if os.path.exists(log):
            print("--- its log:\n" + open(log, encoding="utf-8", errors="replace").read())
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
