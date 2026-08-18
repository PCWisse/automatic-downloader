#!/usr/bin/env bash
# Pause / resume the VPN tunnel without recreating containers. Works with
# whichever provider is configured in .env -- gluetun handles the difference.
#
# IMPORTANT -- what "stopped" actually means:
#   Stopping the tunnel does NOT let qBittorrent connect directly. gluetun's
#   firewall stays up, so qBittorrent simply loses ALL connectivity. Verified:
#   with the tunnel stopped, `curl` from inside qbittorrent returns nothing.
#   This is a PAUSE button (stop all torrent traffic now), not a VPN bypass.
#
# Usage:
#   ./vpn-toggle.sh            # toggle: running <-> stopped
#   ./vpn-toggle.sh status
#   ./vpn-toggle.sh stop
#   ./vpn-toggle.sh start
#
# Linux/Mac port of vpn-toggle.ps1 -- same behavior, including the
# reconnect-and-verify polling loop.

set -uo pipefail
# This script lives in scripts/; gluetun-auth.toml is one level up, in the
# repo root.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

action="${1:-toggle}"
case "$action" in
  toggle|status|stop|start) ;;
  *) echo "Usage: $0 [toggle|status|stop|start]"; exit 1 ;;
esac

# Single source of truth for the key: the auth config gluetun itself reads.
auth_file="$REPO_ROOT/gluetun-auth.toml"
if [ ! -f "$auth_file" ]; then
  echo "gluetun-auth.toml not found"
  exit 1
fi
api_key=$(sed -n 's/^[[:space:]]*apikey[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$auth_file" | head -n1)
if [ -z "$api_key" ]; then
  echo "no apikey found in gluetun-auth.toml"
  exit 1
fi

base="http://localhost:8010/v1"

get_status() {
  local resp
  resp=$(curl -s --max-time 10 -H "X-API-Key: $api_key" "$base/vpn/status")
  if [ -z "$resp" ]; then
    echo "Cannot reach gluetun on :8010 - is the stack up?" >&2
    exit 1
  fi
  # Tiny inline JSON pluck -- avoids requiring jq for a one-field read.
  echo "$resp" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p'
}

set_status() {
  curl -s --max-time 30 -X PUT -H "X-API-Key: $api_key" -H "Content-Type: application/json" \
    -d "{\"status\":\"$1\"}" "$base/vpn/status" | sed -n 's/.*"outcome":"\([^"]*\)".*/\1/p'
}

current=$(get_status)

case "$action" in
  status)
    echo "VPN tunnel: $current"
    if [ "$current" = "running" ]; then
      ip_json=$(curl -s --max-time 15 -H "X-API-Key: $api_key" "$base/publicip/ip")
      if [ -n "$ip_json" ]; then
        public_ip=$(echo "$ip_json" | sed -n 's/.*"public_ip":"\([^"]*\)".*/\1/p')
        city=$(echo "$ip_json" | sed -n 's/.*"city":"\([^"]*\)".*/\1/p')
        country=$(echo "$ip_json" | sed -n 's/.*"country":"\([^"]*\)".*/\1/p')
        echo "Exit node : $public_ip  ($city, $country)"
        echo "Note: GeoIP city lookups are often inaccurate on VPN exit ranges - trust the IP, not the city."
      else
        echo "(public IP not available yet)"
      fi
    else
      echo "qBittorrent currently has NO internet access."
    fi
    exit 0
    ;;
  stop)   target="stopped" ;;
  start)  target="running" ;;
  toggle) if [ "$current" = "running" ]; then target="stopped"; else target="running"; fi ;;
esac

if [ "$current" = "$target" ]; then
  echo "VPN is already '$current' - nothing to do."
  exit 0
fi

printf "VPN: %s -> %s ..." "$current" "$target"
outcome=$(set_status "$target")
echo " $outcome"

if [ "$target" = "stopped" ]; then
  echo ""
  echo "qBittorrent now has NO internet access (firewall still up)."
  echo "This is a pause, NOT a bypass - torrents will stall until you start it again."
  echo "Resume with: ./vpn-toggle.sh start"
else
  printf "Waiting for the tunnel to come up..."
  connected=""
  for _ in $(seq 1 12); do
    sleep 5
    ip_json=$(curl -s --max-time 10 -H "X-API-Key: $api_key" "$base/publicip/ip")
    public_ip=$(echo "$ip_json" | sed -n 's/.*"public_ip":"\([^"]*\)".*/\1/p')
    if [ -n "$public_ip" ]; then
      echo ""
      echo "Connected. Exit node: $public_ip"
      real=$(curl -s --max-time 10 https://icanhazip.com | tr -d '[:space:]')
      if [ "$public_ip" = "$real" ]; then
        echo "WARNING: exit IP equals your real IP - do not download."
      else
        echo "Your real IP ($real) is not exposed."
      fi
      connected="yes"
      break
    fi
    printf "."
  done
  if [ -z "$connected" ]; then
    echo ""
    echo "Tunnel did not report an IP in 60s - check: docker logs gluetun"
  fi
fi
