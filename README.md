# dvr-nvr-viewer

Watch all your DVR / NVR / IP cameras at once, in any web browser: on the computer running it, and on every phone, tablet and computer in your home. No browser plugins and no vendor app.

Made for older DVRs whose own web page still asks for an NPAPI / ActiveX plugin that today's browsers can no longer run.

<!-- Add a screenshot here: docs/screenshot.png -->

- **Any browser, any device.** Chrome, Safari, Firefox, Edge, on desktop and mobile.
- **Set up in the browser.** Enter the DVR's address, brand, username and password on a setup page. No command line needed.
- **Layouts:** grid, 2×2, 3×3, 1 big + 5, 1 big + 7, single camera. Tap a camera to make it big, double-tap for just that camera.
- **Each camera is independent.** A slow or stuck camera only freezes its own tile; the others keep playing live, and it reconnects on its own.
- **Kind to old DVRs.** Cameras are streamed only while someone is watching, small tiles use the lighter stream, and a rejected password is never retried (DVRs lock the account after a few wrong tries).
- **Your DVR password stays on one computer.** Viewers only need a link with an access key.
- **Nothing to install.** Download one file for Windows, macOS or Linux (including a Raspberry Pi) and double-click it: Python and ffmpeg are inside. Or run the single Python file yourself.

## Download (easiest)

Get the file for your computer from the [latest release](https://github.com/tierbuilder/dvr-nvr-viewer/releases/latest):

| Computer | File | How to start it |
|---|---|---|
| Windows 10/11 | `dvr-nvr-viewer-windows-x64.exe` | Double-click. |
| Mac with Apple silicon (M1 and later) | `dvr-nvr-viewer-macos-arm64.zip` | Unzip, move **DVR Viewer** to Applications, double-click. |
| Mac with Intel | `dvr-nvr-viewer-macos-x64.zip` | Same as above. |
| Linux PC | `dvr-nvr-viewer-linux-x64` | Make it executable (`chmod +x`) and run it. |
| Raspberry Pi (64-bit Raspberry Pi OS) | `dvr-nvr-viewer-linux-arm64` | Same as above. |

Your browser opens the setup page: pick the brand, enter the DVR's address, username, password and cameras, and click **Save and connect**. Everything else is done on that page too: the links for other devices are shown there, and **Stop dvr-nvr-viewer** at the bottom shuts it down (on Windows and macOS it runs without a window). Double-clicking it again while it runs just opens the page again.

**The first time only**, your computer warns about an app from an unknown developer, because these downloads aren't signed with a paid Apple or Microsoft certificate:
- **Windows:** in "Windows protected your PC", click **More info**, then **Run anyway**. Allow it on **private networks** if the firewall asks, so phones can connect.
- **macOS:** double-click it once (it gets blocked), then open **System Settings > Privacy & Security**, scroll down and click **Open Anyway**. When asked, allow it to find devices on your **local network**.

If something doesn't work, the log is in `.dvr_nvr_viewer.log` in your home folder.

## Quick start (with Python)

1. Install **Python 3.8+** and **ffmpeg**:

   | | |
   |---|---|
   | macOS | `brew install ffmpeg` |
   | Windows | `winget install Python.Python.3.12` and `winget install Gyan.FFmpeg` (then open a new terminal) |
   | Debian / Ubuntu / Raspberry Pi OS | `sudo apt install python3 ffmpeg` |

2. Install dvr-nvr-viewer:

   ```bash
   pip install git+https://github.com/tierbuilder/dvr-nvr-viewer
   ```

   Or just download `dvr_nvr_viewer.py` and run it with `python3 dvr_nvr_viewer.py ...`.

3. Run it:

   ```bash
   dvr-nvr-viewer
   ```

   Your browser opens a setup page: enter the DVR's address, brand, username, password and camera numbers, and click **Save and connect**. The login is checked once; if the DVR rejects it, nothing is retried. The settings are remembered, so next time it connects straight away.

   It also prints two links:
   - a **watch link** like `http://192.168.1.20:8080/?key=...`: open it on any phone, tablet or computer on your network;
   - an **admin link** (`...?admin=...`): opens the settings from another device. Keep it to yourself.

   Prefer the command line? `dvr-nvr-viewer --ip 192.168.1.64 --user admin 1 2 3 4` asks for the password in the terminal instead and doesn't save anything.

Try it without a DVR: `dvr-nvr-viewer --test 1 2 3 4` shows fake video.

## Camera brands

Pick the brand on the setup page, or use `--brand` on the command line. Many DVRs are sold under other names but use the same software as one of these, so try the matching family.

| `--brand` | For | Address format from | Notes |
|---|---|---|---|
| `dahua` (default) | Dahua, Amcrest, Lorex, CP Plus, IMOU and other Dahua-based | Vendor docs | **Tested on real hardware** |
| `hikvision` | Hikvision, HiLook, HiWatch, Annke and other Hikvision-based | Vendor docs | |
| `hikvision-legacy` | EZVIZ, older Hikvision | Community | EZVIZ: user `admin`, password = 6-letter code on the label; turn on LAN Live View |
| `tplink-nvr` | TP-Link VIGI NVR | Vendor docs | Turn off SRTP on the NVR |
| `tplink-camera` | TP-Link Tapo and VIGI cameras | Vendor docs | Single camera (use `1`); Tapo needs a camera account made in the app |
| `reolink` | Reolink NVRs and cameras | Vendor docs | |
| `uniview` | Uniview (UNV) NVRs | Vendor docs | |
| `hanwha` | Hanwha Vision, Wisenet, Samsung NVRs | Vendor docs | One stream per camera |
| `axis` | Axis cameras and encoders | Vendor docs | One stream per camera |
| `bosch` | Bosch cameras | Vendor docs | Single camera (`1`); multi-imager cameras `1-4` |
| `milesight` | Milesight cameras | Vendor docs | Single camera (use `1`) |
| `foscam` | Foscam cameras | Vendor docs | Single camera (use `1`); port is usually 88 |
| `xmeye` | XMEye / NetSurveillance and many generic DVRs | Community | |

Only Dahua has been tested on real hardware so far. If yours works (or doesn't), please open an issue.

Anything else: choose **Other** on the setup page (or use `--url-template`) and enter the camera's RTSP address with these placeholders:

| Placeholder | Becomes |
|---|---|
| `{user}` `{password}` `{ip}` `{port}` | Your login and the DVR's address |
| `{channel}` | Camera number from 1; `{channel0}` counts from 0; `{channel:02d}` is zero-padded (`01`) |
| `{sub}` | `0` = full quality, `1` = lighter |
| `{stream}` | `1` = full quality, `2` = lighter |
| `{quality}` / `{Quality}` | `main` / `sub`, or `Main` / `Sub` |

For example:

```bash
dvr-nvr-viewer --ip 192.168.1.64 --url-template "rtsp://{user}:{password}@{ip}:{port}/ch{channel}/{stream}" 1 2 3 4
```

Single cameras on different IP addresses can't be combined in one view yet; each run shows the cameras of one device.

## Options

| Option | |
|---|---|
| `--ip ADDRESS` | DVR/NVR address. Leave it out to set up in the browser |
| `--user NAME` | DVR username (asked if not given; with `--ip`) |
| `--brand NAME` | Address style, see above |
| `--layout NAME` | Starting layout: `grid`, `2x2`, `3x3`, `1+5`, `1+7`, `single` |
| `--main` | Full quality for every tile (default: only big tiles) |
| `--web-port N` | Web page port (default 8080, or the next free one) |
| `--bind 127.0.0.1` | Only this computer can watch (default: your whole network) |
| `--web-key KEY` | Choose the access key (default: random, saved in `~/.dvr_nvr_viewer_web_key`) |
| `--no-browser` | Don't open the page automatically |
| `--no-local-admin` | Require the admin link for settings even on this computer (for shared computers) |
| `--port N` | The DVR's RTSP port (default: the brand's usual one, mostly 554) |
| `--test` | Fake video, no DVR needed |

Environment variables `DVR_IP`, `DVR_PORT`, `DVR_USER`, `DVR_PASS`, `DVR_SUB` and `DVR_WEB_KEY` work too, which is handy for services.

On the page: **L** cycles layouts, **1–9** shows that channel alone, **← →** change page or camera, **Esc** goes back.

## Chrome app (experimental)

`browser-app/` is a second way to watch, with **nothing running on the computer besides Chrome**: no server, no ffmpeg. It's a small Chrome app that talks RTSP to the DVR itself and decodes the H.264/H.265 video with the browser's built-in (hardware) decoder, so you get full quality with almost no CPU.

Normal web pages aren't allowed to open the kind of connection RTSP needs. Chrome only allows it for an installed **Isolated Web App** using its Direct Sockets API, and for now only if you turn on developer mode for those apps:

1. In Chrome, open `chrome://flags/#enable-isolated-web-app-dev-mode`, set it to **Enabled** and restart Chrome.
2. Download `dvr-cameras.swbn` from the [Releases](https://github.com/tierbuilder/dvr-nvr-viewer/releases) page.
3. Open `chrome://web-app-internals`, click **Install IWA from Signed Web Bundle** and pick the file.
4. Open **DVR Cameras** from your apps (Launchpad / Start menu / `chrome://apps`), enter the DVR's address, brand, username and password, and the cameras appear.

It has the same brands, layouts and care for the DVR as the Python app: it checks the login on one camera before opening the others, never retries a rejected password, and hangs up cameras that are hidden or when the window has been in the background for 30 seconds. The password is only stored if you tick **Remember the password**, and then only inside the app's own storage.

**Limits:** Chrome only (Chrome, Edge and other Chromium browsers on desktop), it can't share to phones, and Google still lists Isolated Web Apps as being in development, so the install steps may change.

Building it yourself (needs Node.js 22.13+): `cd tools && npm install && npm run build-app` makes `dist/dvr-cameras.swbn`. The first build creates `tools/signing-key.pem`. Keep that key: it decides the app's identity, and a build signed with another key installs as a different app.

## Run it all the time (Raspberry Pi or an old PC)

On Raspberry Pi OS, Debian or Ubuntu, the installer sets it up as a service that starts at boot:

```bash
git clone https://github.com/tierbuilder/dvr-nvr-viewer
cd dvr-nvr-viewer
sudo bash deploy/linux/setup.sh
```

It asks for the DVR details, stores the password in `/etc/dvr-nvr-viewer.env` (readable only by root and the service), and prints the link to open. Run it again to change settings.

**Optional: share a USB printer from the same box.** `sudo bash deploy/linux/setup.sh --printer` installs CUPS with the open-source Brother (brlaser) and HP (hpcups) drivers, detects a printer plugged in by USB, and shares it on your network. iPhones, iPads and Macs find it automatically (AirPrint). Use `--all` to set up cameras and printer together.

### How much hardware do you need?

Rough estimates, scaled from measurements on an Apple M3. Not yet measured on a Pi; reports welcome.

| | 5 cameras, small tiles + 1 big | All 5 in full quality |
|---|---|---|
| Raspberry Pi 4 (1–2 GB) | ✅ ~10–35% CPU, ~500 MB RAM | ❌ not enough CPU |
| Raspberry Pi 5 | ✅ | ⚠️ probably, ~60% CPU |
| Old PC (Core i3/i5, 2012+) | ✅ | ✅ ~2 cores |

Each viewer watching 5 small tiles uses roughly 8–10 Mbit/s of network, so use a cable for the Pi if several people watch at once.

## Security

- **Who can do what.** Anyone with the watch link can watch. Changing the DVR settings needs the admin link, or the computer it runs on (turn that off with `--no-local-admin`). The setup form only accepts submissions from its own page, so other websites can't change it behind your back.
- **The DVR password** is sent from the setup page to the app once and never back to any browser. A saved password is only reused for the same DVR address and username. With "Remember the password" ticked, it's stored in `~/.dvr_nvr_viewer.json`, readable only by your user account. Untick it and you'll be asked again after a restart.
- **Use it on your home network only.** Don't port-forward it to the internet. It serves plain HTTP, and the access key is the only protection. To watch from outside, use a VPN such as Tailscale or WireGuard.
- To revoke all watch links, delete `~/.dvr_nvr_viewer_web_key` and restart. For the admin link, delete `~/.dvr_nvr_viewer_admin_key`. For the service, they're in `/var/lib/dvr-nvr-viewer/`.
- ffmpeg needs the DVR password inside the camera address, so other user accounts on the same computer can see it in the process list. On a shared computer, prefer a Pi or a dedicated user.
- Error messages are printed with the password replaced by `****`.

## Troubleshooting

| Problem | Fix |
|---|---|
| "The DVR rejected this username or password" | Check them on the DVR's own screen. If they worked before, the account is probably locked after too many wrong tries: wait ~30 minutes or restart the DVR. |
| "didn't answer within 15 seconds" | The DVR is busy. Close other viewers (vendor apps, VLC, other copies of this) and wait a minute. Old DVRs only allow a few streams at once. |
| "No route to host" on macOS | The app running it lacks the Local Network permission: System Settings → Privacy & Security → Local Network. Running it from Terminal usually works. |
| A tile says "connecting…" forever | That channel may have no camera, or the brand/URL is wrong. Try the address in VLC (Media → Open Network Stream). |
| Picture stutters | Use fewer full-quality tiles (don't use `--main`), or a wired connection. |

## How it works

For each camera that someone is watching, it runs one ffmpeg that reads the DVR's RTSP stream and turns it into JPEG frames. A small built-in web server sends those as MJPEG, which every browser plays natively in a plain `<img>` tag. Each viewer only ever gets the newest frame, so a slow phone can't build up delay, and nobody's viewing slows anyone else down. Cameras with no viewers are closed after 30 seconds.

The trade-off is that MJPEG uses more network bandwidth than the original H.264 video, and converting it costs CPU. That's why small tiles use the DVR's lighter stream.

## Similar projects

- **[go2rtc](https://github.com/AlexxIT/go2rtc)**: a powerful streaming server that converts RTSP to WebRTC/MSE without re-encoding, so it uses very little CPU. Its built-in page is aimed at single streams and setup rather than a camera wall. A good choice if you need full quality on all cameras on small hardware.
- **[Frigate](https://frigate.video), [Viseron](https://viseron.netlify.app)**: full NVRs with recording and object detection. Much more capable, and much heavier to set up and run.
- **[RTSPtoWeb](https://github.com/deepch/RTSPtoWeb)**: an RTSP-to-WebRTC gateway. You build the viewer page yourself.
- **CamViewerPlus**: a browser-based RTSP grid viewer (Docker / Node.js).
- **Vendor apps** (SmartPSS, iVMS-4200, ...): full-featured, but desktop-only and brand-specific.

dvr-nvr-viewer aims at the simple case: "show me all my cameras in a browser on any device", with one file and no setup.

**Not included:** recording, playback, audio, PTZ control, motion detection.

## Website

`website/` is the download site (home, setup guide, brand list with RTSP addresses), made to be found by search engines and AI assistants. It's served by a Cloudflare Worker, and the free plan is enough: see [website/README.md](website/README.md) to deploy it.

## Building the downloads

`.github/workflows/release.yml` builds them on GitHub for every platform when a version tag is pushed (`git tag v0.3.0 && git push origin v0.3.0`) and attaches them to the release. To build the one for your own computer:

```bash
pip install pyinstaller imageio-ffmpeg pillow
python tools/build_exe.py      # makes dist/release/dvr-nvr-viewer-<platform>
python tools/smoke_test.py     # starts it and checks the page, the video and the Stop button
```

The downloads include an ffmpeg build from [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) (FFmpeg is licensed under the LGPL/GPL; its source is at [ffmpeg.org](https://ffmpeg.org)).

## Development

```bash
python -m unittest discover -s tests -v
```

The tests use fake video (`--test`) and a fake DVR, so they need ffmpeg but no cameras. GitHub Actions runs them on Linux, macOS and Windows.

To try the Chrome app in an ordinary tab, run `python3 tests/browser_harness.py` and open the link it prints. It starts a fake DVR that sends real H.264 over RTSP with Digest login (user `admin`, password `demo123`) and a WebSocket bridge standing in for Direct Sockets.

## License

MIT
