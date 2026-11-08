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
- **Tiny.** One Python file, no Python packages to install. Needs only Python 3.8+ and ffmpeg. Runs on macOS, Windows and Linux, including a Raspberry Pi.

## Quick start

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

## Development

```bash
python -m unittest discover -s tests -v
```

The tests use fake video (`--test`) and a fake DVR, so they need ffmpeg but no cameras. GitHub Actions runs them on Linux, macOS and Windows.

## License

MIT
