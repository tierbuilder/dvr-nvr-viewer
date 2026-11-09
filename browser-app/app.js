// DVR Cameras: the setup page and the camera wall. Talks to the DVR directly, no server needed.
import { CameraPlayer } from "./player.js";
import { DirectSocketTransport, WebSocketTransport } from "./rtsp.js";

// key, label, RTSP address, usual port, hint. Keep in sync with BRAND_LIST in dvr_nvr_viewer.py.
export const BRAND_LIST = [
  ["dahua", "Dahua / Amcrest / Lorex / CP Plus / IMOU",
    "rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={sub}", 554,
    "DVRs, NVRs and cameras. Tested."],
  ["hikvision", "Hikvision / HiLook / HiWatch / Annke",
    "rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}0{stream}", 554,
    "DVRs, NVRs and cameras."],
  ["hikvision-legacy", "EZVIZ / older Hikvision",
    "rtsp://{user}:{password}@{ip}:{port}/h264/ch{channel}/{quality}/av_stream", 554,
    "EZVIZ: user admin, password = the 6-letter verification code on the label; turn on LAN Live View in the app."],
  ["tplink-nvr", "TP-Link VIGI NVR",
    "rtsp://{user}:{password}@{ip}:{port}/live/{channel}/{stream}/avm", 554,
    "Turn off SRTP on the NVR, or other apps can't receive its video."],
  ["tplink-camera", "TP-Link Tapo / VIGI camera",
    "rtsp://{user}:{password}@{ip}:{port}/stream{stream}", 554,
    "Single camera: use camera 1. Tapo needs a camera account made in the Tapo app (not your Tapo login)."],
  ["reolink", "Reolink NVR / camera",
    "rtsp://{user}:{password}@{ip}:{port}/Preview_{channel:02d}_{quality}", 554,
    "On a camera on its own, use camera 1. Avoid special characters in the password."],
  ["uniview", "Uniview (UNV) NVR",
    "rtsp://{user}:{password}@{ip}:{port}/unicast/c{channel}/s{sub}/live", 554, "NVRs."],
  ["hanwha", "Hanwha / Wisenet / Samsung NVR",
    "rtsp://{user}:{password}@{ip}:{port}/LiveChannel/{channel0}/media.smp", 554,
    "NVRs; one stream per camera. If it doesn't connect, the RTSP port may be the NVR's last device port."],
  ["axis", "Axis camera / encoder",
    "rtsp://{user}:{password}@{ip}:{port}/axis-media/media.amp?camera={channel}", 554,
    "One stream per camera; for encoders the camera number is the video input."],
  ["bosch", "Bosch camera",
    "rtsp://{user}:{password}@{ip}:{port}/?line={channel}&inst={stream}", 554,
    "Single camera: use camera 1 (multi-imager cameras: 1-4). RTSP must be enabled on the camera."],
  ["milesight", "Milesight camera",
    "rtsp://{user}:{password}@{ip}:{port}/{quality}", 554, "Single camera: use camera 1."],
  ["foscam", "Foscam camera",
    "rtsp://{user}:{password}@{ip}:{port}/video{Quality}", 88,
    "Single camera: use camera 1. The RTSP port is usually the web port, 88 (some models use 554)."],
  ["xmeye", "XMEye / NetSurveillance / generic Chinese DVR",
    "rtsp://{ip}:{port}/user={user}&password={password}&channel={channel}&stream={sub}.sdp", 554, "DVRs and NVRs."],
];
const BRANDS = Object.fromEntries(BRAND_LIST.map(([key, label, url, port, hint]) => [key, { label, url, port, hint }]));
BRANDS.custom = { label: "Other (type the RTSP address)", url: "", port: 554, hint: "Use the RTSP address from your camera's manual." };

export function parseChannels(text) {
  const out = [];
  for (const part of text.split(/[\s,]+/).filter(Boolean)) {
    const m = /^(\d+)(?:-(\d+))?$/.exec(part);
    if (!m) throw new Error(`"${part}" isn't a camera number`);
    const a = +m[1], b = +(m[2] || m[1]);
    if (a < 1 || b < a || b > 256) throw new Error(`"${part}" isn't a valid camera range`);
    for (let c = a; c <= b; c++) if (!out.includes(c)) out.push(c);
  }
  if (!out.length) throw new Error("Enter at least one camera number");
  return out;
}

export function cameraUrl(cfg, password, ch, sub) {
  const values = {
    user: encodeURIComponent(cfg.user), password: encodeURIComponent(password), ip: cfg.ip, port: cfg.port,
    channel: ch, channel0: ch - 1, sub, stream: sub + 1, quality: sub ? "sub" : "main", Quality: sub ? "Sub" : "Main",
  };
  return cfg.template.replace(/\{(\w+)(:02d)?\}/g, (all, k, pad) =>
    k in values ? (pad ? String(values[k]).padStart(2, "0") : String(values[k])) : all);
}

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const bridge = params.get("bridge"); // tests only: a WebSocket that forwards to TCP
const makeTransport = () => bridge ? new WebSocketTransport(bridge) : new DirectSocketTransport();
const KEY = "dvr-settings", PASS_KEY = "dvr-password";

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; }
}
let cfg = load();
let password = (() => { try { return localStorage.getItem(PASS_KEY); } catch (e) { return null; } })();

function show(id) { for (const s of ["setup", "nosockets", "viewer"]) $(s).classList.toggle("hidden", s !== id); }

function start() {
  if (!bridge && !DirectSocketTransport.available()) return show("nosockets");
  if (!cfg || password === null) return openSetup();
  openViewer();
}

// ---------- setup ----------
for (const [key, b] of Object.entries(BRANDS)) $("brand").append(new Option(b.label, key));
function brandChanged(resetPort) {
  const b = BRANDS[$("brand").value];
  $("brandhint").textContent = b.hint;
  const custom = $("brand").value === "custom";
  $("template").classList.toggle("hidden", !custom);
  $("templatelabel").classList.toggle("hidden", !custom);
  if (resetPort) $("port").value = b.port;
}
$("brand").onchange = () => brandChanged(true);

function openSetup(error) {
  closeViewer();
  const c = cfg || { brand: "dahua", ip: "", port: 554, user: "", channels: [1, 2, 3, 4], allMain: false, template: "" };
  $("brand").value = BRANDS[c.brand] ? c.brand : "dahua";
  brandChanged(false);
  $("template").value = c.brand === "custom" ? c.template : "";
  $("ip").value = c.ip; $("port").value = c.port; $("user").value = c.user;
  $("password").value = "";
  $("password").placeholder = cfg ? "the DVR password" : "";
  $("remember").checked = !!(cfg && password !== null && localStorage.getItem(PASS_KEY) !== null);
  $("channels").value = c.channels.join(" ");
  $("allmain").checked = !!c.allMain;
  $("cancel").classList.toggle("hidden", !(cfg && password !== null));
  setError(error);
  show("setup");
  ($("ip").value ? $("password") : $("ip")).focus();
}
function setError(text) { $("error").textContent = text || ""; $("error").classList.toggle("hidden", !text); }
$("cancel").onclick = () => openViewer();

$("form").onsubmit = e => {
  e.preventDefault();
  try {
    const brand = $("brand").value;
    const template = brand === "custom" ? $("template").value.trim() : BRANDS[brand].url;
    if (!/^rtsp:\/\//i.test(template)) throw new Error("The RTSP address must start with rtsp://");
    const ip = $("ip").value.trim();
    if (!/^[\w.\-:\[\]]+$/.test(ip)) throw new Error("Enter the DVR's IP address, e.g. 192.168.1.64");
    const port = +$("port").value;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("The port must be a number from 1 to 65535");
    const user = $("user").value.trim();
    if (!user) throw new Error("Enter the DVR username");
    const next = { brand, template, ip, port, user, channels: parseChannels($("channels").value), allMain: $("allmain").checked };
    // keep the old password only when it would go to the very same DVR account
    const same = cfg && ["template", "ip", "port", "user"].every(k => cfg[k] === next[k]);
    const pw = $("password").value || (same ? password : null);
    if (pw === null) throw new Error("Enter the DVR password");
    if (pw !== password || !same) loginOk = false;
    cfg = next; password = pw;
    localStorage.setItem(KEY, JSON.stringify(cfg));
    if ($("remember").checked) localStorage.setItem(PASS_KEY, pw); else localStorage.removeItem(PASS_KEY);
    openViewer();
  } catch (err) { setError(err.message); }
};

// ---------- cameras ----------
let tiles = [], layouts = [], st = {};
// until one camera has logged in, only one is opened: a wrong password then costs the DVR one
// failed login instead of one per camera (DVRs lock the account after a few)
let loginOk = false;
const wall = $("wall");

function openViewer() {
  closeViewer();
  show("viewer");
  const chans = cfg.channels, n = chans.length;
  $("title").textContent = `DVR ${cfg.ip}`;
  document.title = `DVR ${cfg.ip}`;
  tiles = chans.map((ch, i) => {
    const el = document.createElement("div");
    el.className = "tile";
    const canvas = document.createElement("canvas");
    const cap = document.createElement("span");
    cap.className = "cap";
    cap.textContent = `CH ${ch}`;
    el.append(canvas, cap);
    const player = new CameraPlayer({
      canvas, makeTransport,
      onStatus: (text, kind) => {
        cap.textContent = `CH ${ch}` + (text ? `  ${text}` : "");
        cap.className = "cap" + (kind ? ` ${kind}` : "");
        if (text === "login rejected") loginRejected(); // one wrong login is enough: don't use up the DVR's tries
      },
      onLogin: () => { if (!loginOk) { loginOk = true; render(); } }, // the login works: open the other cameras
    });
    let lastTap = 0;
    el.addEventListener("click", () => {
      const now = Date.now();
      if (now - lastTap < 350) { toggleSingle(i); lastTap = 0; return; }
      lastTap = now;
      setTimeout(() => { if (lastTap === now) makeBig(i); }, 360);
    });
    wall.append(el);
    return { el, cap, ch, player, want: null };
  });
  layouts = ["grid"];
  if (n > 4) layouts.push("2x2");
  if (n > 9) layouts.push("3x3");
  if (n >= 4) layouts.push("1+5");
  if (n >= 7) layouts.push("1+7");
  layouts.push("single");
  st = { layout: "grid", backTo: "grid", focus: 0, page: 0 };
  try { Object.assign(st, JSON.parse(localStorage.getItem("dvr-layout") || "{}")); } catch (e) {}
  if (!layouts.includes(st.layout)) st.layout = "grid";
  if (st.focus >= n) st.focus = 0;
  const lb = $("layouts");
  lb.replaceChildren(...layouts.map(name => {
    const b = document.createElement("button");
    b.textContent = name === "single" ? "1" : name;
    b.dataset.layout = name;
    b.onclick = () => setLayout(name);
    return b;
  }));
  render();
}

function loginRejected() {
  password = null;
  loginOk = false;
  try { localStorage.removeItem(PASS_KEY); } catch (e) {}
  openSetup("The DVR rejected this username or password. Check them before trying again: " +
            "DVRs lock the account after a few wrong tries.");
}
function stopAll() { for (const t of tiles) { t.player.stop(); t.want = null; } }
function closeViewer() { stopAll(); tiles = []; wall.replaceChildren(); }

function cells() {
  const n = tiles.length, portrait = innerWidth < innerHeight;
  const others = [...Array(n).keys()].filter(i => i !== st.focus);
  if (st.layout === "single") return [1, 1, [[st.focus, 0, 0, 1, 1]], 1];
  if (st.layout === "grid") {
    let cols = Math.ceil(Math.sqrt(n));
    if (portrait) cols = Math.min(2, n);
    return [cols, Math.ceil(n / cols), tiles.map((_, i) => [i, i % cols, Math.floor(i / cols), 1, 1]), 1];
  }
  if (st.layout === "1+5" || st.layout === "1+7") {
    const k = st.layout === "1+5" ? 3 : 4, slots = [];
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
  for (let k = 0, i = st.page * per; i < Math.min(n, (st.page + 1) * per); i++, k++) list.push([i, k % cols, Math.floor(k / cols), 1, 1]);
  return [cols, rows, list, pages];
}

function render() {
  if (!tiles.length) return;
  const [cols, rows, list, pages] = cells();
  wall.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  wall.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
  const shown = new Set(list.map(c => c[0]));
  const paused = document.hidden && hiddenLong;
  list.forEach(([i, c, r, cs, rs], k) => {
    const t = tiles[i];
    t.el.classList.remove("hidden");
    t.el.style.gridColumn = `${c + 1} / span ${cs}`;
    t.el.style.gridRow = `${r + 1} / span ${rs}`;
    const big = cs > 1 || st.layout === "single";
    t.el.classList.toggle("big", big && st.layout !== "single");
    // big tiles get the full-quality stream, small ones the lighter one
    const want = paused || (!loginOk && k > 0) ? null : cameraUrl(cfg, password, t.ch, big || cfg.allMain ? 0 : 1);
    if (t.want !== want) { t.want = want; t.player.play(want, cfg.user, password); }
  });
  tiles.forEach((t, i) => {
    if (shown.has(i)) return;
    t.el.classList.add("hidden");
    if (t.want) { t.want = null; t.player.play(null); } // hidden cameras free their DVR stream
  });
  $("layouts").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.layout === st.layout));
  const paged = st.layout === "single" || pages > 1;
  $("prev").classList.toggle("hidden", !paged);
  $("next").classList.toggle("hidden", !paged);
  try { localStorage.setItem("dvr-layout", JSON.stringify(st)); } catch (e) {}
}

function setLayout(name) { if (name !== "single") st.backTo = name; st.layout = name; st.page = 0; render(); }
function makeBig(i) {
  st.focus = i;
  if (st.layout === "single") return;
  if (st.layout === "1+5" || st.layout === "1+7") render(); else setLayout(tiles.length <= 6 ? "1+5" : "1+7");
}
function toggleSingle(i) { st.focus = i; setLayout(st.layout === "single" ? st.backTo : "single"); }
function step(d) { const n = tiles.length; if (st.layout === "single") st.focus = (st.focus + d + n) % n; else st.page += d; render(); }
$("prev").onclick = () => step(-1);
$("next").onclick = () => step(1);
$("settings").onclick = () => openSetup();
$("fs").onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen?.();
};
addEventListener("keydown", e => {
  if (!tiles.length || e.target.closest?.("form")) return;
  if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "Escape" && st.layout === "single") setLayout(st.backTo);
  else if (e.key.toLowerCase() === "l") setLayout(layouts[(layouts.indexOf(st.layout) + 1) % layouts.length]);
  else if (/^[1-9]$/.test(e.key)) { const i = cfg.channels.indexOf(+e.key); if (i >= 0) { st.focus = i; setLayout("single"); } }
});
addEventListener("resize", () => { if (st.layout === "grid") render(); });

// minimised or in the background for 30s: hang up, so the DVR isn't streaming to nobody
let hiddenLong = false, hideTimer;
document.addEventListener("visibilitychange", () => {
  clearTimeout(hideTimer);
  if (document.hidden) hideTimer = setTimeout(() => { hiddenLong = true; render(); }, 30000);
  else if (hiddenLong) { hiddenLong = false; render(); }
});
addEventListener("pagehide", stopAll);

start();
