#!/usr/bin/env bash
# Expose the Jellyfin library folder as a Windows-mappable SMB share, so an
# existing media collection can be copied onto the server from a PC.
#
# HOST script -- run it on the machine Docker runs on (or the Proxmox LXC),
# NOT inside a container. `docker compose run --rm setup` cannot do this: it
# has no way to install a package or add a user on the host.
#
#   sudo scripts/setup-smb.sh                 # share $MEDIA_ROOT/library
#   sudo MEDIA_ROOT=/srv/media scripts/setup-smb.sh
#   sudo LAN_PREFIX=192.168.1. scripts/setup-smb.sh
#
# Safe to re-run. Reads MEDIA_ROOT from ./.env if present.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$here/.env" ] && MEDIA_ROOT="${MEDIA_ROOT:-$(grep -E '^MEDIA_ROOT=' "$here/.env" | tail -1 | cut -d= -f2-)}"
MEDIA_ROOT="${MEDIA_ROOT:-/mnt/media}"
SHARE_PATH="$MEDIA_ROOT/library"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
LAN_PREFIX="${LAN_PREFIX:-$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | head -1 | cut -d. -f1-3).}"
SMB_USER="${SMB_USER:-media}"

[ -d "$SHARE_PATH" ] || { echo "no such folder: $SHARE_PATH  (set MEDIA_ROOT)" >&2; exit 1; }

echo "share    : $SHARE_PATH"
echo "owner    : ${PUID}:${PGID}  (matches the containers' PUID/PGID)"
echo "allow    : ${LAN_PREFIX}0/24 only"
echo "smb user : $SMB_USER"
echo

export DEBIAN_FRONTEND=noninteractive
command -v smbd >/dev/null || { apt-get update -qq && apt-get install -y -qq samba; }

# A system user that IS the containers' uid/gid, so files land owned correctly.
# nologin: it serves files but cannot be logged into.
getent group "$PGID" >/dev/null || groupadd -g "$PGID" "$SMB_USER"
getent passwd "$PUID" >/dev/null || useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin "$SMB_USER"
SMB_USER="$(getent passwd "$PUID" | cut -d: -f1)"   # real name if uid already existed

# --- smb.conf: keep any existing config, replace only our [library] block ---
CONF=/etc/samba/smb.conf
[ -f "$CONF.orig" ] || cp "$CONF" "$CONF.orig" 2>/dev/null || true
python3 - "$CONF" "$SHARE_PATH" "$SMB_USER" "$LAN_PREFIX" "$(hostname -s | cut -c1-15)" <<'PY'
import sys, re, pathlib
conf, path, user, lan, nb = sys.argv[1:6]
block = f"""[library]
   comment = Jellyfin library (tv + movies)
   path = {path}
   browseable = yes
   read only = no
   valid users = {user}
   force user = {user}
   force group = {user}
   create mask = 0664
   directory mask = 0775
   hosts allow = {lan} 127.
"""
text = pathlib.Path(conf).read_text() if pathlib.Path(conf).exists() else "[global]\n   workgroup = WORKGROUP\n"
text = re.sub(r"(?ms)^\[library\].*?(?=^\[|\Z)", "", text).rstrip() + "\n\n" + block
# make sure guests can't wander in; disable NetBIOS (the hostname is often
# longer than NetBIOS's 15-char limit, and \\<ip> works without it anyway)
for line in (f"netbios name = {nb}", "map to guest = never",
             "disable netbios = yes", "server min protocol = SMB2_10"):
    key = line.split("=")[0].strip()
    if key not in text:
        text = text.replace("[global]", f"[global]\n   {line}", 1)
pathlib.Path(conf).write_text(text)
PY
testparm -s >/dev/null

# --- password (Samba keeps its own; not the system password) ---
if pdbedit -L 2>/dev/null | grep -q "^$SMB_USER:"; then
    echo "smb user '$SMB_USER' already has a password (leaving it)"
else
    echo "Set an SMB password for '$SMB_USER' (used from Windows):"
    smbpasswd -a "$SMB_USER"
fi
smbpasswd -e "$SMB_USER" >/dev/null

systemctl enable --now smbd >/dev/null 2>&1 || service smbd restart
systemctl restart smbd 2>/dev/null || service smbd restart

ip="$(ip -4 -o addr show scope global | awk '{print $4}' | head -1 | cut -d/ -f1)"
echo
echo "done.  From Windows: Map network drive -> \\\\${ip}\\library"
echo "       user '$SMB_USER', the password you just set."
echo "       Copy shows into  library\\tv\\Series Name (Year)\\Season 01\\"
