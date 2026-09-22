#!/usr/bin/env bash
# OPTIONAL: turn Jellyfin into a browsable, playable ROM library via the
# JellyEmu plugin (https://github.com/Jellyfin-PG/JellyEmu) -- box art from
# IGDB/RAWG, games launch in-browser via EmulatorJS (NES, SNES, PS1, N64,
# Game Boy, Sega Genesis, MAME, DOS, PICO-8, and more).
#
# HOST script -- run it on the machine Docker runs on (on Proxmox: inside the
# LXC), NOT inside a container. `docker compose run --rm setup` can't do
# this: it has no business installing a Jellyfin plugin on every fresh
# clone of this repo, and installing/updating a plugin requires restarting
# the Jellyfin container, which the container-based setup step never does.
# Not running it changes nothing about the rest of the stack.
#
#   sudo scripts/setup-jellyemu.sh
#
# What it does, each step skipped if already done (safe to re-run):
#   1. creates $MEDIA_ROOT/library/games, owned 1000:1000 (same as movies/tv)
#      -- it's already inside Jellyfin's existing /media bind mount, so no
#      compose change is needed for the folder itself
#   2. adds the Jellyfin-PG plugin repository
#   3. installs "File Transformation" (JellyEmu's UI-injection dependency)
#      and JellyEmu itself, refusing anything older than 0.9.2.0 -- see the
#      compatibility note below
#   4. restarts Jellyfin, but only if a plugin isn't Active yet
#   5. creates the "games" library (content type Books -- that's the scanner
#      JellyEmu hooks into) pointed at /media/games
#
# What it does NOT do, because these are your own accounts:
#   - IGDB / RAWG API keys for box art -- Dashboard -> Plugins -> JellyEmu
#     (https://api-docs.igdb.com/, https://rawg.io/apidocs)
#   - actually copying ROMs in (use the SMB share, see setup-smb.sh)
#
# Compatibility note: JellyEmu injects JavaScript into the WHOLE Jellyfin web
# client via File Transformation. Jellyfin 12 changed the frontend's internal
# player API enough that JellyEmu <=0.9.1.0 crashed every item detail page,
# not just games -- github.com/Jellyfin-PG/JellyEmu/issues/205. Fixed from
# 0.9.2.0 onward. This script checks the version it's about to install and
# refuses anything older, rather than silently installing a known-broken one.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JELLYFIN_URL="${JELLYFIN_URL:-http://127.0.0.1:8096}"
REPO_URL="https://raw.githubusercontent.com/Jellyfin-PG/Repository/refs/heads/main/manifest.json"
FILE_TRANSFORMATION_GUID="5e87cc92-571a-4d8d-8d98-d2d4147f9f90"
JELLYEMU_GUID="9bab105e-9af0-4e25-a87d-876713b60962"
MIN_JELLYEMU_VERSION="0.9.2.0"

env_get() {
  [ -f "$here/.env" ] || return 0
  grep -E "^$1=" "$here/.env" | tail -1 | cut -d= -f2-
}

step() { printf '\n=== %s ===\n' "$1"; }

MEDIA_ROOT="${MEDIA_ROOT:-$(env_get MEDIA_ROOT)}"
MEDIA_ROOT="${MEDIA_ROOT:-/mnt/media}"
JF_USER="${JELLYFIN_ADMIN_USER:-$(env_get JELLYFIN_ADMIN_USER)}"
JF_PASS="${JELLYFIN_ADMIN_PASSWORD:-$(env_get JELLYFIN_ADMIN_PASSWORD)}"

if [ -z "$JF_USER" ] || [ -z "$JF_PASS" ]; then
  echo "JELLYFIN_ADMIN_USER / JELLYFIN_ADMIN_PASSWORD not in .env -- can't talk to the Jellyfin API." >&2
  exit 1
fi

# --- 1. games folder -------------------------------------------------------
step "Games folder"
GAMES_DIR="$MEDIA_ROOT/library/games"
if [ -d "$GAMES_DIR" ] && [ "$(stat -c '%u:%g' "$GAMES_DIR")" = "1000:1000" ]; then
  echo "skip: $GAMES_DIR already exists, owned 1000:1000"
else
  mkdir -p "$GAMES_DIR"
  chown 1000:1000 "$GAMES_DIR"
  echo "ok: created $GAMES_DIR"
fi

# --- 2-5. everything else talks to the Jellyfin API -------------------------
# One python process for the whole sequence -- restart happens via `docker
# restart` (this is a host script, so that's just a normal shell command),
# then the same process polls the API back up before creating the library.
JELLYFIN_URL="$JELLYFIN_URL" JF_USER="$JF_USER" JF_PASS="$JF_PASS" \
REPO_URL="$REPO_URL" FT_GUID="$FILE_TRANSFORMATION_GUID" JE_GUID="$JELLYEMU_GUID" \
MIN_JE_VERSION="$MIN_JELLYEMU_VERSION" \
python3 - <<'PY'
import json, os, subprocess, sys, time, urllib.parse, urllib.request

base = os.environ["JELLYFIN_URL"].rstrip("/")
auth_client = 'MediaBrowser Client="setup-jellyemu", Device="setup", DeviceId="setup-jellyemu", Version="1.0"'


def call(path, data=None, method=None, token=None, params=None):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f'MediaBrowser Token="{token}"' if token else auth_client}
    if data is not None:
        headers["Content-Type"] = "application/json"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers,
                                  method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def authenticate():
    return call("/Users/AuthenticateByName", {"Username": os.environ["JF_USER"], "Pw": os.environ["JF_PASS"]})["AccessToken"]


try:
    token = authenticate()
except Exception as e:
    print(f"FAIL: could not authenticate to Jellyfin at {base} ({e})", file=sys.stderr)
    sys.exit(1)

# --- 2. repository -----------------------------------------------------------
print("\n=== Plugin repository ===")
repos = call("/Repositories", token=token) or []
if any(r.get("Url") == os.environ["REPO_URL"] for r in repos):
    print("skip: Jellyfin-PG repository already added")
else:
    repos.append({"Name": "Jellyfin-PG", "Url": os.environ["REPO_URL"], "Enabled": True})
    call("/Repositories", repos, token=token)
    print("ok: added Jellyfin-PG repository")
    time.sleep(3)  # let Jellyfin fetch the new manifest before listing packages

# --- 3. install plugins -------------------------------------------------------
print("\n=== Plugins ===")


def latest_version(packages, guid, min_version=None):
    norm = guid.replace("-", "")
    pkg = next((p for p in packages if p.get("guid", "").replace("-", "") == norm), None)
    if not pkg or not pkg.get("versions"):
        return None
    versions = sorted(pkg["versions"], key=lambda v: tuple(int(x) for x in v["version"].split(".")), reverse=True)
    best = versions[0]["version"]
    if min_version:
        min_t = tuple(int(x) for x in min_version.split("."))
        best_t = tuple(int(x) for x in best.split("."))
        if best_t < min_t:
            return "TOO_OLD"
    return best


packages = call("/Packages", token=token) or []
plugins = {p["Name"]: p for p in (call("/Plugins", token=token) or [])}

ft_version = latest_version(packages, os.environ["FT_GUID"])
je_version = latest_version(packages, os.environ["JE_GUID"], os.environ["MIN_JE_VERSION"])

if je_version == "TOO_OLD":
    print(f"FAIL: newest JellyEmu in the repo is older than {os.environ['MIN_JE_VERSION']} -- "
          "that range crashes Jellyfin 12's item pages "
          "(github.com/Jellyfin-PG/JellyEmu/issues/205). Not installing.", file=sys.stderr)
    sys.exit(1)
if not ft_version or not je_version:
    print("FAIL: could not find File Transformation / JellyEmu in the repository manifest.", file=sys.stderr)
    sys.exit(1)

need_restart = False
if "File Transformation" in plugins:
    print("skip: File Transformation already installed")
else:
    call("/Packages/Installed/File%20Transformation", method="POST", token=token,
         params={"AssemblyGuid": os.environ["FT_GUID"], "version": ft_version})
    print(f"ok: installed File Transformation {ft_version}")
    need_restart = True

if "JellyEmu" in plugins:
    print("skip: JellyEmu already installed")
else:
    call("/Packages/Installed/JellyEmu", method="POST", token=token,
         params={"AssemblyGuid": os.environ["JE_GUID"], "version": je_version})
    print(f"ok: installed JellyEmu {je_version}")
    need_restart = True

# --- 4. restart, only if something actually needs loading --------------------
print("\n=== Restart ===")
if not need_restart:
    print("skip: both plugins already loaded")
else:
    subprocess.run(["docker", "restart", "jellyfin"], check=True, capture_output=True)
    print("restarting Jellyfin...", end="", flush=True)
    up = False
    for _ in range(30):
        time.sleep(2)
        try:
            urllib.request.urlopen(base + "/System/Info/Public", timeout=5)
            up = True
            break
        except Exception:
            print(".", end="", flush=True)
    print()
    if not up:
        print("FAIL: Jellyfin didn't come back up within 60s -- check `docker logs jellyfin`", file=sys.stderr)
        sys.exit(1)
    token = authenticate()  # old token dies with the restart
    plugins = {p["Name"]: p for p in (call("/Plugins", token=token) or [])}
    for name in ("File Transformation", "JellyEmu"):
        status = plugins.get(name, {}).get("Status", "missing")
        print(f"{'ok' if status == 'Active' else 'WARN'}: {name} -> {status}")

# --- 5. games library ----------------------------------------------------------
print("\n=== Games library ===")
folders = call("/Library/VirtualFolders", token=token) or []
existing = next((f for f in folders if f.get("Name") == "games"), None)
if existing and "/media/games" in (existing.get("Locations") or []):
    print("skip: 'games' library already correct -> /media/games")
else:
    call("/Library/VirtualFolders", method="POST", token=token,
         params={"name": "games", "collectionType": "books", "refreshLibrary": "true"},
         data={"LibraryOptions": {"PathInfos": [{"Path": "/media/games"}]}})
    print("ok: created 'games' library -> /media/games")

print("""
Left for you:
  - Dashboard -> Plugins -> JellyEmu: add IGDB / RAWG API keys
      https://api-docs.igdb.com/   https://rawg.io/apidocs
  - Copy ROMs into games\\<system>\\ over the SMB share, then Scan Library
""")
PY
