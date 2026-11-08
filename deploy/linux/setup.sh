#!/bin/bash
# dvr-nvr-viewer installer for Debian-based Linux: Raspberry Pi OS, Debian, Ubuntu (Pi or old PC).
# Installs the camera web viewer as a service that starts at boot. Optionally also shares a USB
# printer on the network (AirPrint), handy when the same Pi/PC sits next to the printer.
#
# Usage (from the repository folder, which holds dvr_nvr_viewer.py):
#   sudo bash deploy/linux/setup.sh             install / reconfigure the camera viewer
#   sudo bash deploy/linux/setup.sh --printer   share a USB printer (run again after plugging one in)
#   sudo bash deploy/linux/setup.sh --all       both
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR=/opt/dvr-nvr-viewer
APP_USER=dvr-nvr-viewer
APP_HOME=/var/lib/dvr-nvr-viewer
ENV_FILE=/etc/dvr-nvr-viewer.env
SERVICE=dvr-nvr-viewer
PRINTER_NAME=USB_Printer

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*"; }

do_cameras=1; do_printer=0
# camera address styles, as in dvr_nvr_viewer.py --brand (the tests check these stay in sync)
BRANDS="dahua hikvision hikvision-legacy tplink-nvr tplink-camera reolink uniview hanwha axis bosch milesight foscam xmeye"

install_packages() {
  local pkgs=()
  [ $do_cameras -eq 1 ] && pkgs+=(ffmpeg python3)
  # brlaser = Brother laser driver, hpcups = HP driver; avahi lets phones/Macs find the printer (AirPrint)
  [ $do_printer -eq 1 ] && pkgs+=(cups cups-filters avahi-daemon printer-driver-brlaser printer-driver-hpcups)
  say "Installing: ${pkgs[*]}"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}"
}

# ---------------------------------------------------------------- cameras
env_quote() { # value safe inside double quotes in a systemd EnvironmentFile (systemd doesn't expand $ there)
  local v=${1//\\/\\\\}; v=${v//\"/\\\"}; printf '"%s"' "$v"
}

load_env() { # read our settings back as plain text; never run the file as shell code (a $ or ` in the password would)
  local line k v
  while IFS= read -r line || [ -n "$line" ]; do
    k=${line%%=*}; v=${line#*=}
    v=${v#\"}; v=${v%\"}
    v=${v//\\\\/$'\001'}; v=${v//\\\"/\"}; v=${v//$'\001'/\\}
    case $k in DVR_IP|DVR_BRAND|DVR_PORT|DVR_USER|DVR_PASS|DVR_CHANNELS|WEB_PORT) printf -v "$k" '%s' "$v" ;; esac
  done < "$1"
}

setup_cameras() {
  [ -f "$REPO/dvr_nvr_viewer.py" ] || { echo "dvr_nvr_viewer.py not found in $REPO"; exit 1; }

  # keep earlier answers as defaults when re-running
  local DVR_IP="" DVR_BRAND=dahua DVR_PORT="" DVR_USER="" DVR_CHANNELS="1 2 3 4" WEB_PORT=8080 DVR_PASS=""
  if [ -f "$ENV_FILE" ]; then load_env "$ENV_FILE"; fi

  say "DVR camera viewer"
  read -r -p "DVR/NVR address${DVR_IP:+ [$DVR_IP]}: " v;  DVR_IP=${v:-$DVR_IP}
  echo "Brands: $BRANDS"
  read -r -p "Brand [$DVR_BRAND]: " v; DVR_BRAND=${v:-$DVR_BRAND}
  read -r -p "RTSP port${DVR_PORT:+ [$DVR_PORT]} (Enter = the brand's usual port): " v; DVR_PORT=${v:-$DVR_PORT}
  read -r -p "DVR username${DVR_USER:+ [$DVR_USER]}: " v; DVR_USER=${v:-$DVR_USER}
  read -r -s -p "DVR password${DVR_PASS:+ (Enter = keep current)}: " v; echo; DVR_PASS=${v:-$DVR_PASS}
  read -r -p "Camera channels [$DVR_CHANNELS]: " v;  DVR_CHANNELS=${v:-$DVR_CHANNELS}
  read -r -p "Web page port [$WEB_PORT]: " v;        WEB_PORT=${v:-$WEB_PORT}
  [ -n "$DVR_IP" ] && [ -n "$DVR_USER" ] && [ -n "$DVR_PASS" ] || { echo "Address, username and password are required."; exit 1; }
  if [ -n "$DVR_PORT" ] && ! [[ $DVR_PORT =~ ^[0-9]+$ ]]; then echo "The RTSP port is a number"; exit 1; fi
  case " $BRANDS " in *" $DVR_BRAND "*) ;; *) echo "Unknown brand: $DVR_BRAND (choose one of: $BRANDS)"; exit 1 ;; esac

  id "$APP_USER" &>/dev/null || useradd --system --home-dir "$APP_HOME" --create-home --shell /usr/sbin/nologin "$APP_USER"
  install -d -m 755 "$APP_DIR"
  install -m 644 "$REPO/dvr_nvr_viewer.py" "$APP_DIR/dvr_nvr_viewer.py"

  # the password lives only in this file, readable by root and the service user
  umask 077
  {
    echo "DVR_IP=$(env_quote "$DVR_IP")"
    echo "DVR_BRAND=$(env_quote "$DVR_BRAND")"
    echo "DVR_PORT=$(env_quote "$DVR_PORT")"
    echo "DVR_USER=$(env_quote "$DVR_USER")"
    echo "DVR_PASS=$(env_quote "$DVR_PASS")"
    echo "DVR_CHANNELS=$(env_quote "$DVR_CHANNELS")"
    echo "WEB_PORT=$(env_quote "$WEB_PORT")"
  } > "$ENV_FILE"
  umask 022
  chown root:"$APP_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"

  cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=dvr-nvr-viewer camera web page
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_HOME
EnvironmentFile=$ENV_FILE
# \$DVR_CHANNELS without braces is split into one argument per channel
ExecStart=/usr/bin/python3 -u $APP_DIR/dvr_nvr_viewer.py \$DVR_CHANNELS --no-browser --ip \${DVR_IP} --brand \${DVR_BRAND} --web-port \${WEB_PORT}
Restart=on-failure
RestartSec=30
# exit code 3 = the DVR rejected the login; restarting would lock the account
RestartPreventExitStatus=3
Nice=5
MemoryMax=700M

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now $SERVICE >/dev/null 2>&1 || true
  systemctl restart $SERVICE

  say "Waiting for the camera viewer to start..."
  local key_file="$APP_HOME/.dvr_nvr_viewer_web_key" i
  for i in $(seq 1 40); do
    [ -s "$key_file" ] && systemctl is-active --quiet $SERVICE && break
    systemctl is-failed --quiet $SERVICE && break
    sleep 1
  done
  if systemctl is-active --quiet $SERVICE && [ -s "$key_file" ]; then
    local ip; ip=$(hostname -I | awk '{print $1}')
    echo "Camera viewer is running. Open this on any device on your network:"
    echo
    echo "    http://$ip:$WEB_PORT/?key=$(cat "$key_file")"
    echo "    (or http://$(hostname).local:$WEB_PORT/?key=$(cat "$key_file") )"
  else
    warn "The camera viewer didn't start. Last messages:"
    journalctl -u $SERVICE -n 15 --no-pager | sed -E 's#(rtsp://[^:]+:)[^@]+@#\1****@#'
    if [ "$(systemctl show -p ExecMainStatus --value $SERVICE)" = "3" ]; then
      warn "The DVR rejected the login. Fix it with: sudo bash $0"
    fi
  fi
}

# ---------------------------------------------------------------- printer
# Picks the best driver for a USB printer URI from "lpinfo -m" output (passed on stdin).
pick_driver() {
  local uri=$1 model token best="" drivers search result
  model=$(printf '%s' "$uri" | sed -E 's#^usb://[^/]+/([^?]+).*#\1#; s/%20/ /g')
  # the most specific word of the model name, e.g. DCP-L2540DW or M126nw
  for token in $model; do
    if [[ $token =~ [0-9] ]] && [ ${#token} -gt ${#best} ]; then best=$token; fi
  done
  [ -n "$best" ] || best=$model
  drivers=$(cat)
  # try the exact model word first, then without trailing letters (drivers often say M125-M126 for an M126nw)
  for search in "$best" "$(printf '%s' "$best" | sed -E 's/[A-Za-z]+$//')"; do
    [ -n "$search" ] || continue
    # prefer the open-source drivers, then anything else that names this model
    result=$(printf '%s\n' "$drivers" | { grep -iF -- "$search" || true; } | awk '
      { line = tolower($0) }
      line ~ /brlaser/ || line ~ /hpcups/ { print $1; found = 1; exit }
      { if (!first) first = $1 }
      END { if (!found && first) print first }')
    if [ -n "$result" ]; then printf '%s\n' "$result"; return 0; fi
  done
  return 1
}

setup_printer() {
  say "USB printer"
  systemctl enable --now cups avahi-daemon >/dev/null 2>&1
  # share printers on the network, allow the admin web page from other devices
  cupsctl --share-printers --remote-admin --remote-any
  local admin=${SUDO_USER:-}
  if [ -n "$admin" ] && [ "$admin" != root ]; then usermod -aG lpadmin "$admin"; fi

  local uri
  uri=$(lpinfo -v 2>/dev/null | awk '$1 == "direct" && $2 ~ /^usb:\/\// {print $2; exit}')
  if [ -z "$uri" ]; then
    warn "No USB printer found. Plug it in, switch it on, then run: sudo bash $0 --printer"
  else
    local shown driver
    shown=$(printf '%s' "$uri" | sed -E 's/\?serial=.*//; s/%20/ /g')
    echo "Found: $shown"
    driver=$(lpinfo -m 2>/dev/null | pick_driver "$uri" || true)
    if [ -z "$driver" ]; then
      warn "No driver matched this printer. Add it in the web page below (Administration > Add Printer)."
    else
      echo "Driver: $driver"
      lpadmin -p "$PRINTER_NAME" -E -v "$uri" -m "$driver" -o printer-is-shared=true \
              -D "$(printf '%s' "$shown" | sed -E 's#usb://##; s#/# #')" -L "Raspberry Pi"
      lpadmin -d "$PRINTER_NAME"
      echo "Printer '$PRINTER_NAME' is installed, shared, and set as default."
    fi
  fi
  local ip; ip=$(hostname -I | awk '{print $1}')
  echo
  echo "Printer admin page: https://$ip:631  (accept the browser warning; log in with your Pi username/password)"
  echo "iPhones, iPads and Macs find the printer automatically (AirPrint). On Windows/Android add it as:"
  echo "    http://$ip:631/printers/$PRINTER_NAME"
}

# ---------------------------------------------------------------- run
if [ "${DVR_SETUP_SELFTEST:-}" = 1 ]; then return 0 2>/dev/null || exit 0; fi  # lets tests load the functions only

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash $0 $*"; exit 1; }
case "${1:-}" in
  ""|--cameras) ;;
  --printer) do_cameras=0; do_printer=1 ;;
  --all) do_printer=1 ;;
  *) echo "Unknown option: $1 (use --printer or --all)"; exit 1 ;;
esac
install_packages
[ $do_cameras -eq 1 ] && setup_cameras
[ $do_printer -eq 1 ] && setup_printer
say "Done."
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE          camera viewer status"
echo "  sudo journalctl -u $SERVICE -f          live camera viewer log"
echo "  sudo bash $0             change DVR address/brand/user/password/channels"
echo "  sudo bash $0 --printer   share a USB printer / detect it again"
