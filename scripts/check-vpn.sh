#!/usr/bin/env bash
# Verifies that qBittorrent's traffic actually leaves through the VPN,
# and that the kill switch works. Run this after every config change.
#
# Linux/Mac port of check-vpn.ps1 -- same five checks, same behavior.

set -uo pipefail
# This script lives in scripts/; gluetun-auth.toml and docker-compose.yml are
# one level up, in the repo root.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Safety net: docker-compose.novpn.yml runs the SAME container names with no
# VPN at all -- if that file is what's currently up (not docker-compose.yml),
# qbittorrent exists but gluetun does not. Say so loudly before the rest of
# this script runs, since steps 1-3 below will otherwise just look like a
# leak without explaining why.
qbit_running=$(docker inspect -f '{{.State.Running}}' qbittorrent 2>/dev/null)
gluetun_exists=$(docker inspect -f '{{.State.Running}}' gluetun 2>/dev/null)
if [ "$qbit_running" = "true" ] && [ -z "$gluetun_exists" ]; then
  echo ""
  echo "  *********************************************************"
  echo "  *  WARNING: running docker-compose.novpn.yml right now  *"
  echo "  *  qBittorrent has NO VPN in this mode. Any torrent     *"
  echo "  *  exposes your real IP to the swarm.                   *"
  echo "  *                                                       *"
  echo "  *  Switch back when you're done:                        *"
  echo "  *    docker compose -f docker-compose.novpn.yml down    *"
  echo "  *    docker compose up -d                               *"
  echo "  *********************************************************"
fi

echo ""
echo "== 1. Your real IP (no VPN) =="
real=$(curl -s --max-time 15 https://ifconfig.me/ip)
echo "$real"

echo ""
echo "== 2. IP that qBittorrent's container sees =="
vpn=$(docker exec qbittorrent curl -s --max-time 15 https://ifconfig.me/ip)
echo "$vpn"

echo ""
echo "== 3. Verdict =="
if [ -z "$vpn" ]; then
  echo "FAIL: no response. Is the stack running? (docker compose ps)"
elif [ "$vpn" = "$real" ]; then
  echo "LEAK: container is using your real IP. STOP and fix before torrenting."
else
  echo "OK: traffic is going out via $vpn, not $real"
fi

echo ""
echo "== 4. gluetun's reported public IP =="
# The control server requires an API key. Read it from the auth config so
# there is a single source of truth -- rotate the key there, not here.
auth_file="$REPO_ROOT/gluetun-auth.toml"
api_key=""
if [ -f "$auth_file" ]; then
  api_key=$(sed -n 's/^[[:space:]]*apikey[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$auth_file" | head -n1)
fi
if [ -z "$api_key" ]; then
  echo "no API key found in gluetun-auth.toml - skipping"
else
  resp=$(curl -s --max-time 15 -H "X-API-Key: $api_key" http://localhost:8010/v1/publicip/ip)
  if [ -z "$resp" ]; then
    echo "control server not reachable on :8010 (or key rejected)"
  else
    echo "$resp"
  fi
fi

echo ""
echo "== 5. Kill-switch test =="
printf "Stopping gluetun..."
docker stop gluetun >/dev/null
echo " done."

# An empty curl result is NOT proof on its own -- the exec itself may have
# failed because the container went down with gluetun's namespace.
state=$(docker inspect -f '{{.State.Running}}' qbittorrent 2>/dev/null)
if [ "$state" != "true" ]; then
  echo "INCONCLUSIVE: qbittorrent stopped along with gluetun. The kill switch"
  echo "holds by construction (no namespace = no network), but wasn't exercised."
else
  leaked=$(docker exec qbittorrent curl -s --max-time 8 https://ifconfig.me/ip 2>/dev/null)
  if [ -n "$leaked" ]; then
    echo "KILL SWITCH FAILED: still reachable as $leaked"
  else
    echo "OK: qBittorrent has no connectivity without the tunnel."
  fi
fi

echo "Restarting stack..."
docker compose -f "$REPO_ROOT/docker-compose.yml" up -d >/dev/null
# qBittorrent AND Prowlarr both share gluetun's namespace, which died with it;
# both must be restarted to rejoin the new one. Missing prowlarr here was a
# real bug -- caught live: it silently sat on the dead namespace with no
# network path at all until this restart was added.
docker compose -f "$REPO_ROOT/docker-compose.yml" restart qbittorrent prowlarr >/dev/null
echo "Done."
echo ""
