#!/usr/bin/env bash
# Compares raw connection throughput against throughput through the VPN
# tunnel (whichever provider is configured in .env). Puts NOTHING into a
# torrent swarm -- your IP is never exposed to peers. This is the safe way
# to answer "what is the VPN costing me?".
#
# Usage:
#   ./speedtest.sh              # 25 MB per path, 2 runs (default)
#   ./speedtest.sh 100          # 100 MB per path, more accurate, more data used
#   ./speedtest.sh 100 3        # 100 MB per path, 3 runs
#
# NOTE: the tunnelled run consumes your VPN provider's data allowance, if it
# has one (Privado's free tier is 10 GB/month). The direct run does not.
#
# Linux/Mac port of speedtest.ps1 -- same measurement approach (curl's own
# speed_download, not a wrapper that could throttle the number).

set -uo pipefail

size_mb="${1:-25}"
runs="${2:-2}"
bytes=$((size_mb * 1000000))
url="https://speed.cloudflare.com/__down?bytes=${bytes}"

format_speed() {
  # $1 = bytes/sec
  awk -v b="$1" 'BEGIN { printf "%6.1f MB/s  (%4.0f Mbps)", b/1048576, (b*8)/1048576 }'
}

echo ""
echo "Speed test: ${size_mb} MB x ${runs} run(s) per path"
echo "Tunnelled runs use ~$((size_mb * runs)) MB of your VPN provider's allowance, if it has one."
echo ""

# --- direct ---------------------------------------------------------------
direct_max=0
for i in $(seq 1 "$runs"); do
  printf "  direct  run %s ..." "$i"
  raw=$(curl -s -o /dev/null -w '%{speed_download}' --max-time 180 "$url" 2>/dev/null)
  if [ -n "$raw" ] && awk -v r="$raw" 'BEGIN{exit !(r>0)}'; then
    echo "  $(format_speed "$raw")"
    if awk -v r="$raw" -v m="$direct_max" 'BEGIN{exit !(r>m)}'; then direct_max="$raw"; fi
  else
    echo "  FAILED"
  fi
done

# --- through the tunnel ----------------------------------------------------
vpn_max=0
vpn_have=""
running=$(docker inspect -f '{{.State.Running}}' qbittorrent 2>/dev/null)
if [ "$running" != "true" ]; then
  echo "  qbittorrent is not running - skipping tunnelled test"
else
  for i in $(seq 1 "$runs"); do
    printf "  tunnel  run %s ..." "$i"
    raw=$(docker exec qbittorrent sh -c "curl -s -o /dev/null -w '%{speed_download}' --max-time 180 '$url'" 2>/dev/null)
    if [ -n "$raw" ] && awk -v r="$raw" 'BEGIN{exit !(r>0)}'; then
      echo "  $(format_speed "$raw")"
      vpn_have="yes"
      if awk -v r="$raw" -v m="$vpn_max" 'BEGIN{exit !(r>m)}'; then vpn_max="$raw"; fi
    else
      echo "  FAILED (is the tunnel up? ./vpn-toggle.sh status)"
    fi
  done
fi

# --- summary ----------------------------------------------------------------
echo ""
echo "== Result =="
if awk -v d="$direct_max" 'BEGIN{exit !(d>0)}'; then
  echo "  direct (no VPN)   $(format_speed "$direct_max")"
fi
if [ -n "$vpn_have" ]; then
  echo "  through the VPN   $(format_speed "$vpn_max")"
fi
if awk -v d="$direct_max" 'BEGIN{exit !(d>0)}' && [ -n "$vpn_have" ]; then
  pct=$(awk -v d="$direct_max" -v v="$vpn_max" 'BEGIN { printf "%.0f", (1 - v/d) * 100 }')
  echo "  tunnel overhead   ${pct}%"
  echo ""
  echo "Best of ${runs} run(s) shown. Short transfers under-report because of TCP"
  echo "slow-start - use 100 as the first argument if you want a firmer number."
  echo ""
  echo "Reality check: torrent speed is governed by seeder count and the lack"
  echo "of port forwarding far more than by this ceiling."
fi
echo ""
