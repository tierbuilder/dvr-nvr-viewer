"""Builds the download for this computer's platform: dvr-nvr-viewer with Python and ffmpeg inside,
so people don't need to install anything.

    pip install pyinstaller imageio-ffmpeg pillow
    python tools/build_exe.py

Makes one file in dist/release/:
    Windows  dvr-nvr-viewer-windows-x64.exe      double-click; runs with no window, the browser opens
    macOS    dvr-nvr-viewer-macos-arm64.zip       unzip, double-click "DVR Viewer"
    Linux    dvr-nvr-viewer-linux-x64             chmod +x and run (also Raspberry Pi: linux-arm64)

GitHub Actions runs this on every platform for each release (.github/workflows/release.yml).
ffmpeg comes from the imageio-ffmpeg package, which ships ready-made ffmpeg builds for each platform.
"""
import os
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build", "exe")
OUT = os.path.join(ROOT, "dist", "release")
ICON = os.path.join(ROOT, "browser-app", "icon-512.png")


def target():
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return system, arch


def bundled_ffmpeg():
    import imageio_ffmpeg
    src = imageio_ffmpeg.get_ffmpeg_exe()
    folder = os.path.join(BUILD, "ffmpeg")
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)
    # the app needs these: reading RTSP and making JPEGs
    info = subprocess.run([dst, "-hide_banner", "-demuxers"], capture_output=True, text=True).stdout
    enc = subprocess.run([dst, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if " rtsp " not in info or " mjpeg " not in enc:
        sys.exit(f"{src} can't read RTSP or make MJPEG")
    return dst


def main():
    system, arch = target()
    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    ffmpeg = bundled_ffmpeg()
    name = "DVR Viewer" if system == "macos" else "dvr-nvr-viewer"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", name,
           "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
           "--specpath", BUILD, "--add-binary", f"{ffmpeg}{os.pathsep}.", "--icon", ICON]
    if system == "macos":
        # an .app with no Dock icon or window: it opens the browser, and the setup page has a Stop button
        cmd += ["--windowed", "--onedir", "--osx-bundle-identifier", "io.github.tierbuilder.dvr-nvr-viewer"]
    elif system == "windows":
        cmd += ["--onefile", "--noconsole"]
    else:
        cmd += ["--onefile"]
    subprocess.run(cmd + [os.path.join(ROOT, "dvr_nvr_viewer.py")], check=True, cwd=ROOT)

    dist = os.path.join(BUILD, "dist")
    if system == "macos":
        app = os.path.join(dist, f"{name}.app")
        plist_path = os.path.join(app, "Contents", "Info.plist")
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        plist.update(LSUIElement=True,
                     NSLocalNetworkUsageDescription="DVR Viewer connects to your DVR or cameras on this network.")
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)
        # changing Info.plist breaks PyInstaller's signature; sign again (ad hoc: there's no Apple ID here)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app], check=True)
        out = os.path.join(OUT, f"dvr-nvr-viewer-macos-{arch}.zip")
        if os.path.exists(out):
            os.remove(out)
        subprocess.run(["ditto", "-c", "-k", "--keepParent", app, out], check=True)
    else:
        src = os.path.join(dist, name + (".exe" if system == "windows" else ""))
        out = os.path.join(OUT, f"dvr-nvr-viewer-{system}-{arch}" + (".exe" if system == "windows" else ""))
        shutil.copy2(src, out)
    print(f"built {out} ({os.path.getsize(out) // (1024 * 1024)} MB)")


if __name__ == "__main__":
    main()
