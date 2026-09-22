#!/usr/bin/env bash
# OPTIONAL: reach Jellyfin and Seerr from outside the house over Tailscale
# -- an encrypted private network between your own devices. No router ports are
# opened and nothing is published to the internet; only devices signed into
# your tailnet can connect.
#
# HOST script -- run it on the machine Docker runs on (on Proxmox: inside the
# LXC), NOT inside a container. `docker compose run --rm setup` can't do this:
# it has no way to install a system service. Not running it changes nothing
# about the rest of the stack.
#
#   sudo scripts/setup-tailscale.sh                         # interactive login (prints a URL)
#   sudo TS_AUTHKEY=tskey-auth-... scripts/setup-tailscale.sh
#   sudo TS_HOSTNAME=mediaserver scripts/setup-tailscale.sh
#
# What it does, each step skipped if already done (safe to re-run):
#   1. checks the TUN device exists (on Proxmox the LXC must be allowed it)
#   2. installs Tailscale from tailscale.com's own package repository
#   3. joins your tailnet (`tailscale up`)
#   4. tells Jellyfin which networks are local: your LAN, the Docker network,
#      and the tailnet. Without this Jellyfin treats tailnet clients as remote.
#   5. prints the addresses to use
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS_HOSTNAME="${TS_HOSTNAME:-$(hostname -s)}"
JELLYFIN_URL="${JELLYFIN_URL:-http://127.0.0.1:8096}"
COMPOSE_NETWORK="${COMPOSE_NETWORK:-downloader_default}"

# Tailscale's address ranges: IPv4 CGNAT space and its IPv6 ULA prefix.
TAILNET_V4="100.64.0.0/10"
TAILNET_V6="fd7a:115c:a1e0::/48"

# Read one KEY=value from .env WITHOUT sourcing it -- .env holds passwords with
# characters ($, #, %) that `source` would mangle or execute.
env_get() {
  [ -f "$here/.env" ] || return 0
  grep -E "^$1=" "$here/.env" | tail -1 | cut -d= -f2-
}

step() { printf '\n=== %s ===\n' "$1"; }

# --- 1. TUN device ------------------------------------------------------------
step "TUN device"
if [ ! -c /dev/net/tun ]; then
  cat >&2 <<'EOF'
/dev/net/tun is missing -- Tailscale needs it. On Proxmox, with the LXC stopped,
run on the HOST (replace <CTID> with the container id from `pct list`):

  echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> /etc/pve/lxc/<CTID>.conf
  echo "lxc.mount.entry: /dev/net dev/net none bind,create=dir" >> /etc/pve/lxc/<CTID>.conf

then start the LXC and re-run this script. (The VPN container needs the same
device, so if gluetun works, this is already in place.)
EOF
  exit 1
fi
echo "ok: /dev/net/tun present"

# --- 2. install ----------------------------------------------------------------
step "Install"
if command -v tailscale >/dev/null && command -v tailscaled >/dev/null; then
  echo "skip: already installed ($(tailscale version | head -1))"
else
  # The official installer adds tailscale.com's apt repository, so the package
  # updates with the rest of the system from then on.
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled >/dev/null 2>&1 || true

# --- 3. join the tailnet -------------------------------------------------------
step "Join tailnet"
ts_state() {
  tailscale status --json 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null \
    || true
}
if [ "$(ts_state)" = "Running" ]; then
  echo "skip: already connected as $(tailscale status --json | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  # An install from before --accept-dns=false was added: fix it in place.
  tailscale set --accept-dns=false
else
  # --accept-dns=false: without it Tailscale rewrites /etc/resolv.conf to its
  # own resolver (100.100.100.100), and every container resolves through it --
  # so if tailscaled ever stops, Sonarr/Radarr/Seerr lose DNS entirely.
  # The server is what others connect TO; it never needs tailnet names itself.
  up_args=(--hostname="$TS_HOSTNAME" --accept-dns=false)
  if [ -n "${TS_AUTHKEY:-}" ]; then
    up_args+=(--authkey="$TS_AUTHKEY")
  else
    echo "Open the login URL below in a browser and sign in -- this waits until you do."
  fi
  tailscale up "${up_args[@]}"
fi

# --- 4. Jellyfin local networks ------------------------------------------------
step "Jellyfin local networks"
JF_USER="${JELLYFIN_ADMIN_USER:-$(env_get JELLYFIN_ADMIN_USER)}"
JF_PASS="${JELLYFIN_ADMIN_PASSWORD:-$(env_get JELLYFIN_ADMIN_PASSWORD)}"
LAN_SUBNET="${LAN_SUBNET:-$(ip -4 route show scope link proto kernel 2>/dev/null \
  | awk -v dev="$(ip -4 route show default | awk '{print $5; exit}')" '$0 ~ "dev "dev {print $1; exit}')}"
DOCKER_SUBNET="$(docker network inspect "$COMPOSE_NETWORK" \
  --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | awk '{print $1}' || true)"

if [ -z "$JF_USER" ] || [ -z "$JF_PASS" ]; then
  cat <<EOF
skip: JELLYFIN_ADMIN_USER / JELLYFIN_ADMIN_PASSWORD not in .env. Set this by hand in
      Jellyfin -> Dashboard -> Networking -> LAN Networks:
        ${LAN_SUBNET:-<your LAN, e.g. 192.168.1.0/24>}, ${DOCKER_SUBNET:-172.18.0.0/16}, $TAILNET_V4, $TAILNET_V6
EOF
else
  JF_USER="$JF_USER" JF_PASS="$JF_PASS" JELLYFIN_URL="$JELLYFIN_URL" \
  WANT="$LAN_SUBNET $DOCKER_SUBNET $TAILNET_V4 $TAILNET_V6" \
  python3 - <<'PY'
import json, os, sys, urllib.request

base = os.environ["JELLYFIN_URL"].rstrip("/")
auth = 'MediaBrowser Client="setup-tailscale", Device="setup", DeviceId="setup-tailscale", Version="1.0"'

def call(path, data=None, token=None):
    headers = {"Authorization": f'MediaBrowser Token="{token}"' if token else auth, "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body, headers=headers,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None

try:
    token = call("/Users/AuthenticateByName",
                 {"Username": os.environ["JF_USER"], "Pw": os.environ["JF_PASS"]})["AccessToken"]
    net = call("/System/Configuration/network", token=token)
except Exception as e:
    print(f"WARN: could not reach Jellyfin at {base} ({e}) -- set LAN Networks by hand")
    sys.exit(0)

have = list(net.get("LocalNetworkSubnets") or [])
want = [s for s in os.environ["WANT"].split() if s]
# An EMPTY list means "auto-detect", and inside Docker Jellyfin only detects the
# container network -- so the moment we write an explicit list, the LAN and the
# Docker network must be in it too, or they stop counting as local.
merged = have + [s for s in want if s not in have]

if merged == have:
    print("skip: already set ->", ", ".join(have))
else:
    net["LocalNetworkSubnets"] = merged
    call("/System/Configuration/network", net, token=token)
    print("ok: LAN Networks ->", ", ".join(merged))
PY
fi

# --- 5. summary ----------------------------------------------------------------
step "Done"
if [ "$(ts_state)" = "Running" ]; then
  ip4="$(tailscale ip -4 | head -1)"
  dns="$(tailscale status --json | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  cat <<EOF
From any device signed into your tailnet:

  Jellyfin    http://$dns:8096    (or http://$ip4:8096)
  Seerr       http://$dns:5055    (or http://$ip4:5055)

The name only resolves with MagicDNS on (Tailscale admin console -> DNS).
Letting someone else in, restricting what they can reach, and the Chromecast
caveat: see README -> "Remote access to Jellyfin".
EOF
  tailnet="$(tailscale status --json | python3 -c 'import sys,json; print(json.load(sys.stdin).get("CurrentTailnet",{}).get("Name",""))')"
  peers="$(tailscale status --json | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("Peer") or {}))')"
  if [ "$peers" = "0" ]; then
    cat <<EOF

NOTE: this server is the only device in tailnet "$tailnet" so far. Those
addresses only open from a device running the Tailscale app, signed in to that
SAME account -- a different login is a different tailnet, and the page just
hangs with no error.
EOF
  fi
else
  echo "Tailscale is not connected yet -- re-run this script after signing in."
fi
