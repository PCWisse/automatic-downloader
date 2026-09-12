"""
One-shot configuration for the whole stack.

    docker compose run --rm setup

Takes a freshly-started stack from "containers running" to "fully wired",
doing everything the README's manual steps 7-13 describe. Uses each app's
REST API -- the same calls their own web UIs make -- rather than driving a
browser, so there is no UI-scraping fragility.

SAFE TO RE-RUN. Every step checks current state first and skips or updates
rather than duplicating. Nothing is ever deleted.

Two things it deliberately cannot do, because they need YOUR accounts:
  * Indexers in Prowlarr (which sites, plus your credentials)
  * Usenet provider in SABnzbd (your paid subscription details)
Both are reported at the end as remaining manual steps.

One thing needs a hand exactly once: on a brand-new install qBittorrent's
password lives only in its Docker log, which this container cannot read (no
Docker socket, by design). Pass QBITTORRENT_BOOTSTRAP_PASSWORD on that first
run and the script sets QBITTORRENT_PASSWORD permanently -- see README step 6.
"""

import json
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests
import yaml

# --- config from environment -------------------------------------------------

MEDIA_ROOT_IN_CONTAINER = "/data"  # identical in every service, see README

# Defaults match the VPN-protected stack (docker-compose.yml), where
# qBittorrent and Prowlarr share gluetun's network namespace and are reached
# through it. docker-compose.novpn.yml overrides both to point at the
# containers directly, since there is no gluetun in that stack -- see its
# `setup` service for the override.
QBIT_URL = os.environ.get("QBIT_URL", "http://gluetun:8090")
PROWLARR_URL = os.environ.get("PROWLARR_URL", "http://gluetun:9696")
# Byparr (FlareSolverr-compatible Cloudflare solver) as Prowlarr must reach it.
# On the VPN stack Prowlarr shares gluetun's namespace with Byparr, so it is
# just localhost; docker-compose.novpn.yml overrides this to the container name.
BYPARR_URL = os.environ.get("BYPARR_URL", "http://localhost:8191/")
# The host Sonarr/Radarr are told to use for the qBittorrent download client.
# Not always the same string as QBIT_URL's host -- kept separate on purpose.
QBIT_HOST = os.environ.get("QBIT_HOST", "gluetun")
SONARR_URL = "http://sonarr:8989"
RADARR_URL = "http://radarr:7878"
BAZARR_URL = "http://bazarr:6767"
SABNZBD_URL = "http://sabnzbd:8080"
JELLYFIN_URL = "http://jellyfin:8096"
JELLYSEERR_URL = "http://jellyseerr:5055"

QBIT_USER = os.environ.get("QBITTORRENT_USER", "")
QBIT_PASSWORD = os.environ.get("QBITTORRENT_PASSWORD", "")
# One-shot credential for the very first run. On a fresh install qBittorrent
# invents a random password and prints it ONLY to its Docker log, which this
# container deliberately cannot read (no Docker socket -- see the setup service
# in docker-compose.yml). Pass that password in once and the script sets
# QBITTORRENT_PASSWORD permanently; after that this is no longer needed.
QBIT_BOOTSTRAP_PASSWORD = os.environ.get("QBITTORRENT_BOOTSTRAP_PASSWORD", "")
JELLYFIN_USER = os.environ.get("JELLYFIN_ADMIN_USER", "")
JELLYFIN_PASSWORD = os.environ.get("JELLYFIN_ADMIN_PASSWORD", "")

# nvidia (default) -> NVENC, amd -> VAAPI, anything else -> hardware
# transcoding left off. Must match the compose file(s) actually in use --
# see docker-compose.gpu-amd.yml.
GPU_VENDOR = os.environ.get("GPU_VENDOR", "nvidia").lower()

# Tunables that mirror the README's documented values.
MAX_ACTIVE_DOWNLOADS = int(os.environ.get("QBIT_MAX_ACTIVE_DOWNLOADS", 4))
SEED_RATIO_LIMIT = float(os.environ.get("QBIT_SEED_RATIO_LIMIT", 1.0))
SEED_TIME_LIMIT_MIN = int(os.environ.get("QBIT_SEED_TIME_LIMIT_MIN", 180))
MIN_SEEDERS = int(os.environ.get("INDEXER_MIN_SEEDERS", 5))
MAX_SIZE_MB_PER_MIN_2160P = int(os.environ.get("MAX_SIZE_MB_PER_MIN_2160P", 252))

# Quality profile Jellyseerr hands to Sonarr/Radarr for new requests. Must be
# a profile name that exists in them; falls back to the first non-"Any"
# profile if it doesn't. "Any" is deliberately avoided -- despite the name it
# EXCLUDES 4K (see README).
JELLYSEERR_PROFILE = os.environ.get("JELLYSEERR_QUALITY_PROFILE", "HD-1080p")
# Sonarr and Radarr profile names diverge as soon as Recyclarr is involved --
# the TRaSH 4K profiles are "WEB-2160p" in Sonarr but "[SQP] SQP-1 WEB (2160p)"
# in Radarr -- so one shared name cannot match both. These override per app and
# fall back to the shared value above.
JELLYSEERR_SONARR_PROFILE = os.environ.get("JELLYSEERR_SONARR_PROFILE", "") or JELLYSEERR_PROFILE
JELLYSEERR_RADARR_PROFILE = os.environ.get("JELLYSEERR_RADARR_PROFILE", "") or JELLYSEERR_PROFILE
JELLYSEERR_EMAIL = os.environ.get("JELLYSEERR_ADMIN_EMAIL", "")

# --- output helpers ----------------------------------------------------------

_problems: list[str] = []
_manual: list[str] = []


def info(msg: str) -> None:
    print(f"    {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"  [ok]   {msg}", flush=True)


def skip(msg: str) -> None:
    print(f"  [skip] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}", flush=True)
    _problems.append(msg)


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


# --- generic API helpers -----------------------------------------------------


def arr_get(base: str, path: str, api_key: str, **params):
    r = requests.get(f"{base}{path}", headers={"X-Api-Key": api_key}, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def arr_post(base: str, path: str, api_key: str, payload: dict):
    r = requests.post(
        f"{base}{path}",
        headers={"X-Api-Key": api_key},
        json=payload,
        params={"forceSave": "true"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def arr_put(base: str, path: str, api_key: str, payload: dict):
    r = requests.put(
        f"{base}{path}",
        headers={"X-Api-Key": api_key},
        json=payload,
        params={"forceSave": "true"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def set_field(obj: dict, name: str, value) -> None:
    """Set a value in an *arr 'fields' list (their generic settings shape)."""
    for f in obj.get("fields", []):
        if f.get("name") == name:
            f["value"] = value
            return
    obj.setdefault("fields", []).append({"name": name, "value": value})


def get_field(obj: dict, name: str):
    for f in obj.get("fields", []):
        if f.get("name") == name:
            return f.get("value")
    return None


# --- reading API keys from the mounted config volumes ------------------------


def read_arr_api_key(config_path: str, app: str) -> str | None:
    """Sonarr/Radarr/Prowlarr store theirs in config.xml."""
    try:
        tree = ET.parse(config_path)
        key = tree.getroot().findtext("ApiKey")
        if key:
            return key.strip()
        warn(f"{app}: config.xml has no <ApiKey> yet")
    except FileNotFoundError:
        warn(f"{app}: {config_path} not found -- has the container started at least once?")
    except ET.ParseError as e:
        warn(f"{app}: could not parse config.xml ({e})")
    return None


def read_bazarr_api_key(config_path: str) -> str | None:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        key = (cfg.get("auth") or {}).get("apikey")
        if key:
            return key
        warn("Bazarr: no apikey in config.yaml yet")
    except FileNotFoundError:
        warn(f"Bazarr: {config_path} not found -- has the container started at least once?")
    except Exception as e:
        warn(f"Bazarr: could not read config.yaml ({e})")
    return None


def read_sabnzbd_api_key(config_path: str) -> str | None:
    try:
        with open(config_path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("api_key"):
                    return line.split("=", 1)[1].strip()
        warn("SABnzbd: no api_key in sabnzbd.ini yet")
    except FileNotFoundError:
        warn(f"SABnzbd: {config_path} not found -- has the container started at least once?")
    return None


# --- host bind-mount permissions ---------------------------------------------

# Recyclarr is the only service that runs as a NON-root user (user: 1000:1000 in
# compose) against a BIND MOUNT rather than a named volume. On a Linux host a
# fresh clone leaves ./recyclarr-config owned by whoever cloned it -- usually
# root -- and Recyclarr then crash-loops on:
#
#     Access to the path '/config/logs' is denied. [ Permission denied ]
#
# Named volumes never hit this because Docker creates them with the right owner,
# and Docker Desktop does not either because its bind mounts ignore ownership.
# So it is specifically a Linux-host failure, invisible on Windows/macOS -- which
# is exactly the kind of thing worth fixing here instead of in the README.
HOST_PUID = int(os.environ.get("PUID", 1000))
HOST_PGID = int(os.environ.get("PGID", 1000))

# Where the setup service mounts those host directories, read-write.
BIND_MOUNTS = (("recyclarr-config", "/hostcfg/recyclarr"),)

# Recyclarr's own config lives here (same mount, read side). When it contains a
# quality_definition block for an app, Recyclarr owns that app's quality
# definitions and setup must not fight it -- see configure_quality_sizes().
RECYCLARR_CONFIG_DIRS = (
    "/hostcfg/recyclarr/configs",  # `recyclarr config create` writes here
    "/hostcfg/recyclarr",          # a hand-written recyclarr.yml sits at the root
)


def recyclarr_manages_quality(app: str) -> bool:
    """True when a Recyclarr config defines quality_definition for this app.

    Recyclarr syncs quality definitions (the 2160p size caps among them) from
    the TRaSH Guides on its own schedule. If setup also writes them, the two
    ping-pong: setup on every manual run, Recyclarr nightly. Whoever the user
    pointed at those profiles should own the sizes too -- so setup backs off.
    """
    for d in RECYCLARR_CONFIG_DIRS:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith((".yml", ".yaml")):
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    doc = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError):
                continue
            for instance in (doc.get(app) or {}).values():
                if isinstance(instance, dict) and "quality_definition" in instance:
                    return True
    return False


def configure_host_permissions() -> None:
    step("Host bind-mount permissions")

    for label, mount in BIND_MOUNTS:
        if not os.path.isdir(mount):
            skip(f"{label}: not mounted into setup, cannot check ownership")
            continue

        # Checking the top level plus its immediate children is enough to spot a
        # fresh clone (uniformly root-owned) without stat-ing the ~73 MB
        # TRaSH-Guides clone under resources/ on every single run.
        probes = [mount] + [os.path.join(mount, n) for n in os.listdir(mount)]
        if all(_owned_correctly(p) for p in probes):
            skip(f"{label}: already owned by {HOST_PUID}:{HOST_PGID}")
            continue

        try:
            fixed = _chown_tree(mount)
        except PermissionError:
            warn(
                f"{label}: owned by the wrong user and setup could not change it. "
                f"Run on the host:  sudo chown -R {HOST_PUID}:{HOST_PGID} ./{label}"
            )
            _manual.append(f"chown -R {HOST_PUID}:{HOST_PGID} ./{label} on the host")
            continue
        ok(f"{label}: ownership corrected to {HOST_PUID}:{HOST_PGID} ({fixed} path(s))")


def _owned_correctly(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return True  # unreadable/vanished -- not our problem to diagnose here
    return st.st_uid == HOST_PUID and st.st_gid == HOST_PGID


def _chown_tree(root: str) -> int:
    """chown -R, touching only what is actually wrong. Returns paths changed."""
    changed = 0
    for path in _walk_paths(root):
        if not _owned_correctly(path):
            os.chown(path, HOST_PUID, HOST_PGID)
            changed += 1
    return changed


def _walk_paths(root: str):
    yield root
    for parent, dirs, files in os.walk(root):
        for name in dirs + files:
            yield os.path.join(parent, name)


# --- readiness ---------------------------------------------------------------


def wait_for(name: str, url: str, timeout: int = 180) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=10)
            # Any HTTP answer (even 401/403) proves it is listening.
            if r.status_code < 500:
                ok(f"{name} is up")
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    warn(f"{name} did not respond within {timeout}s at {url}")
    return False


# --- qBittorrent -------------------------------------------------------------


def qbit_login(session: requests.Session, password: str) -> bool:
    """True when these credentials produce a session cookie."""
    session.cookies.clear()
    try:
        session.post(
            f"{QBIT_URL}/api/v2/auth/login",
            data={"username": QBIT_USER, "password": password},
            headers={"Referer": QBIT_URL},
            timeout=30,
        )
    except requests.RequestException:
        return False
    # 5.x answers 204 with a session cookie; older versions answered 200 "Ok."
    return any(c.startswith("QBT_SID") for c in session.cookies.keys())


def qbit_authenticate(session: requests.Session) -> bool:
    """Log in, setting the .env password permanently on a first run.

    Three outcomes, in order of preference:
      1. The .env password already works -- nothing to do.
      2. It doesn't, but QBITTORRENT_BOOTSTRAP_PASSWORD does. That is the random
         password qBittorrent printed to its Docker log on first start. Use it
         once to set the .env password for good, so Sonarr, Radarr and
         speed-monitor (which all read QBITTORRENT_PASSWORD) can authenticate.
      3. Neither works -- explain exactly how to get the bootstrap password.
    """
    if qbit_login(session, QBIT_PASSWORD):
        ok("authenticated")
        return True

    if QBIT_BOOTSTRAP_PASSWORD and qbit_login(session, QBIT_BOOTSTRAP_PASSWORD):
        info("bootstrap password accepted -- setting the .env password permanently")
        r = session.post(
            f"{QBIT_URL}/api/v2/app/setPreferences",
            data={"json": json.dumps({"web_ui_password": QBIT_PASSWORD})},
            headers={"Referer": QBIT_URL},
            timeout=30,
        )
        r.raise_for_status()
        # Prove it took rather than trusting the 200 -- a fresh login, not the
        # session cookie we already hold, which stays valid either way.
        if not qbit_login(session, QBIT_PASSWORD):
            warn("qBittorrent: password change reported success but the new password does not log in")
            _manual.append("Set the qBittorrent WebUI password by hand to match .env, then re-run setup")
            return False
        ok("WebUI password set to QBITTORRENT_PASSWORD from .env")
        return True

    warn(
        "qBittorrent login failed with the .env password. On a fresh install qBittorrent "
        "generates a random temporary password and prints it only to its Docker log, which "
        "this container cannot read (no Docker socket, by design). Get it on the host with:  "
        "docker logs qbittorrent 2>&1 | grep -i 'temporary password'  -- then re-run:  "
        "QBITTORRENT_BOOTSTRAP_PASSWORD='<that password>' docker compose run --rm setup"
    )
    _manual.append(
        "qBittorrent: re-run setup with QBITTORRENT_BOOTSTRAP_PASSWORD set to the temporary "
        "password from `docker logs qbittorrent`, and it will set the .env one permanently"
    )
    return False


def configure_qbittorrent() -> bool:
    step("qBittorrent")
    if not QBIT_USER or not QBIT_PASSWORD:
        warn(
            "QBITTORRENT_USER / QBITTORRENT_PASSWORD not set in .env -- skipping. "
            "Set them (any password you like -- this script will apply it to qBittorrent), "
            "then re-run."
        )
        _manual.append("Set QBITTORRENT_USER / QBITTORRENT_PASSWORD in .env, then re-run setup")
        return False

    s = requests.Session()
    if not qbit_authenticate(s):
        return False

    prefs = {
        "save_path": f"{MEDIA_ROOT_IN_CONTAINER}/torrents/complete",
        "temp_path": f"{MEDIA_ROOT_IN_CONTAINER}/torrents/incomplete",
        "temp_path_enabled": True,
        "max_active_downloads": MAX_ACTIVE_DOWNLOADS,
        "max_active_torrents": max(MAX_ACTIVE_DOWNLOADS * 3, 15),
        "queueing_enabled": True,
        "max_ratio_enabled": True,
        "max_ratio": SEED_RATIO_LIMIT,
        "max_seeding_time_enabled": True,
        "max_seeding_time": SEED_TIME_LIMIT_MIN,
        "max_ratio_act": 0,  # 0 = stop seeding. 1 would DELETE -- never that.
        "upnp": False,       # the VPN has no port forwarding; retrying forever is pointless
        "dht": True,
        "pex": True,
    }
    resp = s.post(f"{QBIT_URL}/api/v2/app/setPreferences", data={"json": json.dumps(prefs)}, timeout=30)
    resp.raise_for_status()

    current = s.get(f"{QBIT_URL}/api/v2/app/preferences", timeout=30).json()
    ok(f"save path        -> {current.get('save_path')}")
    ok(f"incomplete path  -> {current.get('temp_path')}")
    ok(f"max downloads    -> {current.get('max_active_downloads')}")
    ok(f"seed limits      -> ratio {current.get('max_ratio')}, {current.get('max_seeding_time')} min, action=stop")
    ok(f"UPnP             -> {current.get('upnp')}")
    return True


# --- Prowlarr ----------------------------------------------------------------


def configure_prowlarr_apps(prowlarr_key: str, sonarr_key: str, radarr_key: str) -> None:
    step("Prowlarr -> Sonarr / Radarr")
    existing = arr_get(PROWLARR_URL, "/api/v1/applications", prowlarr_key)
    by_name = {a["name"]: a for a in existing}

    schemas = arr_get(PROWLARR_URL, "/api/v1/applications/schema", prowlarr_key)

    targets = [
        ("Sonarr", SONARR_URL, sonarr_key),
        ("Radarr", RADARR_URL, radarr_key),
    ]

    for name, base_url, api_key in targets:
        if not api_key:
            warn(f"{name}: no API key available, skipping Prowlarr link")
            continue

        if name in by_name:
            app = by_name[name]
            # The important bit: Full Sync silently overwrites app-side settings
            # (verified: it reset minimumSeeders back to 1 with no warning).
            if app.get("syncLevel") != "addOnly":
                app["syncLevel"] = "addOnly"
                arr_put(PROWLARR_URL, f"/api/v1/applications/{app['id']}", prowlarr_key, app)
                ok(f"{name}: already linked, sync level corrected to addOnly")
            else:
                skip(f"{name}: already linked (addOnly)")
            continue

        schema = next((s for s in schemas if s.get("implementation") == name), None)
        if schema is None:
            warn(f"{name}: no Prowlarr application schema found, skipping")
            continue

        payload = dict(schema)
        payload["name"] = name
        payload["syncLevel"] = "addOnly"
        set_field(payload, "prowlarrUrl", PROWLARR_URL)
        set_field(payload, "baseUrl", base_url)
        set_field(payload, "apiKey", api_key)
        try:
            arr_post(PROWLARR_URL, "/api/v1/applications", prowlarr_key, payload)
            ok(f"{name}: linked (addOnly)")
        except requests.HTTPError as e:
            warn(f"{name}: could not link to Prowlarr -- {e.response.text[:200]}")


def configure_byparr_proxy(prowlarr_key: str) -> None:
    """Register Byparr as a FlareSolverr proxy in Prowlarr.

    Byparr is a drop-in FlareSolverr replacement -- a headless browser that
    clears Cloudflare's JS challenge for indexers that sit behind it. The
    container is already running (see docker-compose.yml); this just tells
    Prowlarr about it.

    It is opt-in PER INDEXER: Prowlarr only routes an indexer through the
    proxy when that indexer carries the matching tag. We create the proxy
    and a 'byparr' tag; the user still has to put that tag on whichever
    indexers actually need it (reported at the end).
    """
    step("Prowlarr -> Byparr (Cloudflare solver)")

    existing = arr_get(PROWLARR_URL, "/api/v1/indexerProxy", prowlarr_key)
    if any(p.get("implementation") == "FlareSolverr" for p in existing):
        skip("Byparr: FlareSolverr proxy already present")
        _manual.append(
            "Prowlarr: tag any Cloudflare-protected indexer with 'byparr' so it "
            "routes through the solver (Indexer -> Tags)"
        )
        return

    # Get or create the 'byparr' tag.
    tags = arr_get(PROWLARR_URL, "/api/v1/tag", prowlarr_key)
    tag = next((t for t in tags if t.get("label") == "byparr"), None)
    if tag is None:
        tag = arr_post(PROWLARR_URL, "/api/v1/tag", prowlarr_key, {"label": "byparr"})
    tag_id = tag["id"]

    schemas = arr_get(PROWLARR_URL, "/api/v1/indexerProxy/schema", prowlarr_key)
    schema = next((s for s in schemas if s.get("implementation") == "FlareSolverr"), None)
    if schema is None:
        warn("Byparr: Prowlarr has no FlareSolverr proxy schema, skipping")
        return

    payload = dict(schema)
    payload["name"] = "byparr"
    payload["tags"] = [tag_id]
    set_field(payload, "host", BYPARR_URL)
    set_field(payload, "requestTimeout", 60)

    try:
        arr_post(PROWLARR_URL, "/api/v1/indexerProxy", prowlarr_key, payload)
        ok(f"Byparr: registered as FlareSolverr proxy ({BYPARR_URL}), tag 'byparr'")
        _manual.append(
            "Prowlarr: tag any Cloudflare-protected indexer with 'byparr' so it "
            "routes through the solver (Indexer -> Tags)"
        )
    except requests.HTTPError as e:
        warn(f"Byparr: could not register proxy -- {e.response.text[:200]}")


# --- Sonarr / Radarr ---------------------------------------------------------


def configure_download_clients(app: str, base: str, key: str, sab_key: str | None) -> None:
    existing = arr_get(base, "/api/v3/downloadclient", key)
    by_impl = {c.get("implementation"): c for c in existing}
    schemas = arr_get(base, "/api/v3/downloadclient/schema", key)

    cat_torrent = "tv-sonarr" if app == "sonarr" else "radarr"
    cat_usenet = "tv" if app == "sonarr" else "movies"
    cat_field = "tvCategory" if app == "sonarr" else "movieCategory"

    # --- qBittorrent -----------------------------------------------------
    # QBIT_HOST is 'gluetun' on the VPN-protected stack (qBittorrent shares
    # its namespace) and 'qbittorrent' on the no-VPN stack (its own container,
    # reached directly). See QBIT_HOST's definition above.
    if "QBittorrent" in by_impl:
        skip(f"{app}: qBittorrent download client already present")
    elif not QBIT_USER or not QBIT_PASSWORD:
        warn(f"{app}: qBittorrent credentials missing from .env, skipping download client")
    else:
        schema = next((s for s in schemas if s.get("implementation") == "QBittorrent"), None)
        if schema is None:
            warn(f"{app}: no qBittorrent schema available")
        else:
            payload = dict(schema)
            payload["name"] = "qBittorrent"
            payload["enable"] = True
            payload["priority"] = 2  # Usenet first when both exist
            payload["removeCompletedDownloads"] = True
            payload["removeFailedDownloads"] = True
            set_field(payload, "host", QBIT_HOST)
            set_field(payload, "port", 8090)
            set_field(payload, "username", QBIT_USER)
            set_field(payload, "password", QBIT_PASSWORD)
            set_field(payload, cat_field, cat_torrent)
            try:
                arr_post(base, "/api/v3/downloadclient", key, payload)
                ok(f"{app}: qBittorrent added (host={QBIT_HOST}, category={cat_torrent})")
            except requests.HTTPError as e:
                warn(f"{app}: qBittorrent client failed -- {e.response.text[:200]}")

    # --- SABnzbd -------------------------------------------------------------
    if "Sabnzbd" in by_impl:
        skip(f"{app}: SABnzbd download client already present")
    elif not sab_key:
        skip(f"{app}: no SABnzbd API key, skipping (fine if you don't use Usenet)")
    else:
        schema = next((s for s in schemas if s.get("implementation") == "Sabnzbd"), None)
        if schema is None:
            warn(f"{app}: no SABnzbd schema available")
        else:
            payload = dict(schema)
            payload["name"] = "SABnzbd"
            payload["enable"] = True
            payload["priority"] = 1  # tried before torrents
            payload["removeCompletedDownloads"] = True
            payload["removeFailedDownloads"] = True
            set_field(payload, "host", "sabnzbd")
            set_field(payload, "port", 8080)
            set_field(payload, "apiKey", sab_key)
            set_field(payload, cat_field, cat_usenet)
            try:
                arr_post(base, "/api/v3/downloadclient", key, payload)
                ok(f"{app}: SABnzbd added (category={cat_usenet}, priority 1)")
            except requests.HTTPError as e:
                warn(f"{app}: SABnzbd client failed -- {e.response.text[:200]}")


def configure_root_folder(app: str, base: str, key: str) -> None:
    wanted = f"{MEDIA_ROOT_IN_CONTAINER}/library/{'tv' if app == 'sonarr' else 'movies'}"
    existing = arr_get(base, "/api/v3/rootfolder", key)
    if any(rf.get("path", "").rstrip("/") == wanted for rf in existing):
        skip(f"{app}: root folder {wanted} already set")
        return
    try:
        arr_post(base, "/api/v3/rootfolder", key, {"path": wanted})
        ok(f"{app}: root folder -> {wanted}")
    except requests.HTTPError as e:
        warn(f"{app}: root folder failed -- {e.response.text[:200]}")


def configure_quality_sizes(app: str, base: str, key: str) -> None:
    """Cap 2160p sizes. Value is MB-per-minute-of-runtime, not a flat size."""
    if recyclarr_manages_quality(app):
        skip(f"{app}: 2160p size caps -> left to Recyclarr (it has a quality_definition for {app})")
        return
    defs = arr_get(base, "/api/v3/qualitydefinition", key)
    changed = 0
    for d in defs:
        if "2160p" not in d["quality"]["name"]:
            continue
        if d.get("maxSize") == MAX_SIZE_MB_PER_MIN_2160P:
            continue
        d["maxSize"] = MAX_SIZE_MB_PER_MIN_2160P
        try:
            arr_put(base, f"/api/v3/qualitydefinition/{d['id']}", key, d)
            changed += 1
        except requests.HTTPError as e:
            warn(f"{app}: quality definition {d['quality']['name']} failed -- {e.response.text[:200]}")
    if changed:
        ok(f"{app}: {changed} x 2160p max size -> {MAX_SIZE_MB_PER_MIN_2160P} MB/min")
    else:
        skip(f"{app}: 2160p size caps already correct")


def configure_min_seeders(app: str, base: str, key: str) -> None:
    indexers = arr_get(base, "/api/v3/indexer", key)
    if not indexers:
        skip(f"{app}: no indexers yet (they arrive from Prowlarr once you add some)")
        return
    changed = 0
    for ix in indexers:
        if ix.get("protocol") != "torrent":
            continue
        if get_field(ix, "minimumSeeders") == MIN_SEEDERS:
            continue
        set_field(ix, "minimumSeeders", MIN_SEEDERS)
        try:
            arr_put(base, f"/api/v3/indexer/{ix['id']}", key, ix)
            changed += 1
        except requests.HTTPError as e:
            warn(f"{app}: indexer {ix.get('name')} failed -- {e.response.text[:200]}")
    if changed:
        ok(f"{app}: min seeders -> {MIN_SEEDERS} on {changed} indexer(s)")
    else:
        skip(f"{app}: min seeders already {MIN_SEEDERS}")


def configure_completed_handling(app: str, base: str, key: str) -> None:
    cfg = arr_get(base, "/api/v3/config/downloadclient", key)
    if cfg.get("enableCompletedDownloadHandling"):
        skip(f"{app}: completed download handling already on")
        return
    cfg["enableCompletedDownloadHandling"] = True
    try:
        arr_put(base, f"/api/v3/config/downloadclient/{cfg['id']}", key, cfg)
        ok(f"{app}: completed download handling enabled")
    except requests.HTTPError as e:
        warn(f"{app}: could not enable completed handling -- {e.response.text[:200]}")


# --- SABnzbd -----------------------------------------------------------------


def configure_sabnzbd(sab_key: str | None) -> None:
    step("SABnzbd")
    if not sab_key:
        warn("SABnzbd: no API key found, skipping")
        return

    def sab(mode: str, **params):
        params.update({"mode": mode, "apikey": sab_key, "output": "json"})
        r = requests.get(f"{SABNZBD_URL}/api", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    # Categories must exist or Sonarr/Radarr refuse with "Category does not exist".
    try:
        current = sab("get_config", section="categories")
        have = {c.get("name") for c in current.get("config", {}).get("categories", [])}
    except Exception as e:
        warn(f"SABnzbd: could not read categories ({e})")
        have = set()

    for cat, folder in (("tv", "/data/usenet/complete"), ("movies", "/data/usenet/complete")):
        if cat in have:
            skip(f"SABnzbd: category '{cat}' already exists")
            continue
        try:
            sab("set_config", section="categories", keyword=cat, name=cat, priority=0, pp=3, script="None", dir="")
            ok(f"SABnzbd: category '{cat}' created")
        except Exception as e:
            warn(f"SABnzbd: could not create category '{cat}' ({e})")

    # Sonarr/Radarr connect as host 'sabnzbd'; without it they get 401.
    try:
        misc = sab("get_config", section="misc")["config"]["misc"]
        # Returned as a list by current SABnzbd, but older versions answered
        # with a comma-separated string -- handle both rather than assume.
        raw_whitelist = misc.get("host_whitelist", "")
        if isinstance(raw_whitelist, str):
            hosts = [h.strip() for h in raw_whitelist.split(",") if h.strip()]
        else:
            hosts = [str(h).strip() for h in raw_whitelist if str(h).strip()]
        if "sabnzbd" not in hosts:
            hosts.append("sabnzbd")
            sab("set_config", section="misc", keyword="host_whitelist", value=",".join(hosts))
            ok("SABnzbd: 'sabnzbd' added to host whitelist")
        else:
            skip("SABnzbd: host whitelist already contains 'sabnzbd'")

        folders = {"download_dir": "/data/usenet/incomplete", "complete_dir": "/data/usenet/complete"}
        for keyword, value in folders.items():
            if misc.get(keyword) != value:
                sab("set_config", section="misc", keyword=keyword, value=value)
                ok(f"SABnzbd: {keyword} -> {value}")
            else:
                skip(f"SABnzbd: {keyword} already {value}")
    except Exception as e:
        warn(f"SABnzbd: could not update misc config ({e})")

    _manual.append("SABnzbd: add your Usenet provider (Config -> Servers) -- needs your subscription details")


# --- Bazarr ------------------------------------------------------------------


def configure_bazarr(bazarr_key: str | None, sonarr_key: str, radarr_key: str) -> None:
    step("Bazarr")
    if not bazarr_key:
        warn("Bazarr: no API key found, skipping")
        return

    headers = {"X-API-KEY": bazarr_key}
    try:
        r = requests.get(f"{BAZARR_URL}/api/system/settings", headers=headers, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        warn(f"Bazarr: could not read settings ({e})")
        return

    # Booleans MUST be lowercase strings. Python's bool serializes to "True"
    # via requests' form encoding, and Bazarr rejects that outright with a 406:
    #   "general.use_sonarr must is_type_of <class 'bool'> but it is True"
    payload = {}
    if sonarr_key:
        payload.update({
            "settings-general-use_sonarr": "true",
            "settings-sonarr-ip": "sonarr",
            "settings-sonarr-port": 8989,
            "settings-sonarr-base_url": "/",
            "settings-sonarr-apikey": sonarr_key,
            "settings-sonarr-ssl": "false",
        })
    if radarr_key:
        payload.update({
            "settings-general-use_radarr": "true",
            "settings-radarr-ip": "radarr",
            "settings-radarr-port": 7878,
            "settings-radarr-base_url": "/",
            "settings-radarr-apikey": radarr_key,
            "settings-radarr-ssl": "false",
        })
    if not payload:
        warn("Bazarr: no Sonarr/Radarr keys to configure with")
        return

    try:
        r = requests.post(f"{BAZARR_URL}/api/system/settings", headers=headers, data=payload, timeout=60)
        r.raise_for_status()
        ok("Bazarr: connected to Sonarr and Radarr")
        _manual.append("Bazarr: pick subtitle languages and providers (Settings -> Languages / Providers)")
    except requests.RequestException as e:
        warn(f"Bazarr: could not save settings ({e})")


# --- Jellyfin ----------------------------------------------------------------


def jellyfin_token() -> str | None:
    """Authenticate as the .env admin and return an access token, or None."""
    if not JELLYFIN_USER or not JELLYFIN_PASSWORD:
        return None
    try:
        r = requests.post(
            f"{JELLYFIN_URL}/Users/AuthenticateByName",
            json={"Username": JELLYFIN_USER, "Pw": JELLYFIN_PASSWORD},
            headers={
                "Authorization": 'MediaBrowser Client="setup", Device="setup", DeviceId="setup", Version="1.0.0"'
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["AccessToken"]
    except (requests.RequestException, KeyError):
        return None


def configure_jellyfin() -> None:
    step("Jellyfin")
    try:
        r = requests.get(f"{JELLYFIN_URL}/System/Info/Public", timeout=30)
        r.raise_for_status()
        public = r.json()
    except requests.RequestException as e:
        warn(f"Jellyfin: not reachable ({e})")
        return

    wizard_done = public.get("StartupWizardCompleted", False)

    if not wizard_done:
        if not JELLYFIN_USER or not JELLYFIN_PASSWORD:
            warn(
                "Jellyfin: wizard not complete and JELLYFIN_ADMIN_USER / JELLYFIN_ADMIN_PASSWORD "
                "are not set in .env -- skipping. Set them and re-run, or finish the wizard by hand."
            )
            _manual.append("Jellyfin: run the setup wizard at http://localhost:8096")
            return
        try:
            requests.post(
                f"{JELLYFIN_URL}/Startup/Configuration",
                json={
                    "UICulture": "en-US",
                    "MetadataCountryCode": "US",
                    "PreferredMetadataLanguage": "en",
                },
                timeout=30,
            ).raise_for_status()
            # Must be fetched before the user can be set -- primes the wizard state.
            requests.get(f"{JELLYFIN_URL}/Startup/User", timeout=30).raise_for_status()
            requests.post(
                f"{JELLYFIN_URL}/Startup/User",
                json={"Name": JELLYFIN_USER, "Password": JELLYFIN_PASSWORD},
                timeout=30,
            ).raise_for_status()
            # Needed for Tailscale clients (100.64.0.0/10 is non-local to Jellyfin);
            # this alone opens no router ports -- UPnP stays off.
            requests.post(
                f"{JELLYFIN_URL}/Startup/RemoteAccess",
                json={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False},
                timeout=30,
            ).raise_for_status()
            requests.post(f"{JELLYFIN_URL}/Startup/Complete", timeout=30).raise_for_status()
            ok(f"Jellyfin: wizard completed, admin user '{JELLYFIN_USER}' created")
        except requests.RequestException as e:
            warn(f"Jellyfin: wizard failed ({e})")
            _manual.append("Jellyfin: finish the setup wizard by hand at http://localhost:8096")
            return
    else:
        skip("Jellyfin: wizard already completed")

    if not JELLYFIN_USER or not JELLYFIN_PASSWORD:
        skip("Jellyfin: no admin credentials in .env, skipping library setup")
        _manual.append("Jellyfin: add Movies (/media/movies) and Shows (/media/tv) libraries")
        return

    token = jellyfin_token()
    if token is None:
        warn(f"Jellyfin: could not authenticate as '{JELLYFIN_USER}'")
        _manual.append("Jellyfin: check JELLYFIN_ADMIN_USER / JELLYFIN_ADMIN_PASSWORD in .env")
        return

    headers = {"X-Emby-Token": token}
    try:
        folders = requests.get(f"{JELLYFIN_URL}/Library/VirtualFolders", headers=headers, timeout=30).json()
        have = {f.get("Name"): f for f in folders}
    except requests.RequestException as e:
        warn(f"Jellyfin: could not list libraries ({e})")
        return

    # The options every library must carry. EnableRealtimeMonitor is the one
    # that actually broke playback in the wild: a library created by an older
    # setup came up with it OFF, so new episodes were only ever seen by the
    # 12-hourly scan -- which raced Sonarr's multi-file imports and produced
    # unplayable "Season Unknown" entries. SaveLocalMetadata must stay off
    # because /media is read-only; writing there logs an error every scan.
    # (EnableInternetProviders is deliberately not listed -- on Jellyfin 10.11
    # it is a legacy derived flag that always reads back False with the modern
    # empty TypeOptions, so asserting it would "correct" on every run. Internet
    # metadata works regardless via the server-default fetchers.)
    want_opts = {
        "EnableRealtimeMonitor": True,
        "SaveLocalMetadata": False,
    }

    for name, ctype, path in (("Movies", "movies", "/media/movies"), ("Shows", "tvshows", "/media/tv")):
        try:
            folder = have.get(name)
            if folder is not None:
                locations = folder.get("Locations") or []
                opts = folder.get("LibraryOptions") or {}
                item_id = folder.get("ItemId")

                # A library can exist with no folder attached -- it then scans
                # nothing and stays permanently empty.
                if path not in locations:
                    requests.post(
                        f"{JELLYFIN_URL}/Library/VirtualFolders/Paths",
                        headers=headers,
                        params={"refreshLibrary": "false"},
                        json={"Name": name, "PathInfo": {"Path": path}},
                        timeout=60,
                    ).raise_for_status()
                    ok(f"Jellyfin: library '{name}' had no media folder, attached {path}")

                drift = {k: v for k, v in want_opts.items() if opts.get(k) != v}
                if drift and item_id:
                    merged = {**opts, **want_opts}
                    requests.post(
                        f"{JELLYFIN_URL}/Library/VirtualFolders/LibraryOptions",
                        headers=headers,
                        json={"Id": item_id, "LibraryOptions": merged},
                        timeout=60,
                    ).raise_for_status()
                    ok(f"Jellyfin: library '{name}' options corrected ({', '.join(sorted(drift))})")
                elif not drift and path in locations:
                    skip(f"Jellyfin: library '{name}' already correct -> {path}")
                continue

            # PathInfos MUST sit under LibraryOptions: sent at the top level
            # Jellyfin still returns 204 but silently creates an EMPTY library.
            options = {"LibraryOptions": {**want_opts, "PathInfos": [{"Path": path}]}}
            requests.post(
                f"{JELLYFIN_URL}/Library/VirtualFolders",
                headers=headers,
                params={"name": name, "collectionType": ctype, "refreshLibrary": "true"},
                json=options,
                timeout=60,
            ).raise_for_status()
            ok(f"Jellyfin: library '{name}' -> {path}")
        except requests.RequestException as e:
            warn(f"Jellyfin: could not configure library '{name}' ({e})")

    # Hardware transcoding: passing the GPU through in compose is necessary but
    # NOT sufficient -- it must be switched on here too, or it silently uses CPU.
    if GPU_VENDOR == "none":
        skip("Jellyfin: hardware transcoding left off (GPU_VENDOR=none)")
    else:
        accel_type = "vaapi" if GPU_VENDOR == "amd" else "nvenc"
        try:
            enc = requests.get(f"{JELLYFIN_URL}/System/Configuration/encoding", headers=headers, timeout=30).json()

            # Jellyfin's decode codec list is a SEPARATE setting from the
            # encoder backend above, and defaults to just ['h264', 'vc1'] no
            # matter what HardwareAccelerationType is set to. Missing this
            # means every HEVC file -- i.e. essentially all 4K, and a lot of
            # 1080p -- decodes on the CPU while only the encode is hardware.
            # On a small box that falls behind real time and the stream stalls
            # repeatedly, while an H.264 source (fully hardware, decode+encode)
            # plays perfectly -- a confusing split verified live: with 'hevc'
            # missing, a 4K episode transcoded with no -hwaccel decode flags at
            # all; adding it made the same file decode+encode entirely on the
            # GPU at 1.4x realtime. vp8/vp9 have been broadly supported since
            # ~2015 on both vendors, so they're safe to assume; av1 is not --
            # only recent GPUs decode it, so it's left for you to enable by
            # hand (Dashboard -> Playback) once you've confirmed your hardware
            # supports it.
            want_codecs = {"h264", "vc1", "hevc", "vp9"}
            have_codecs = {c.lower() for c in enc.get("HardwareDecodingCodecs", [])}

            drift = []
            if enc.get("HardwareAccelerationType", "").lower() != accel_type:
                drift.append("encoder backend")
                enc["HardwareAccelerationType"] = accel_type
                enc["EnableHardwareEncoding"] = True
                if accel_type == "vaapi":
                    enc["VaapiDevice"] = "/dev/dri/renderD128"
                    # Tone mapping deliberately NOT enabled for VAAPI. It needs
                    # an OpenCL runtime in the container, and on AMD a missing
                    # one makes HDR transcodes FAIL rather than fall back to
                    # CPU. Turn it on by hand once you've confirmed 4K HDR
                    # playback works on your hardware:
                    #   Dashboard -> Playback -> Enable Tone mapping
                else:
                    # HDR looks washed out on SDR screens without this. Safe on
                    # NVENC, where the tone-mapping path needs no extra runtime.
                    enc["EnableTonemapping"] = True
            if not want_codecs <= have_codecs:
                drift.append("decode codecs")
                enc["HardwareDecodingCodecs"] = sorted(have_codecs | want_codecs)

            if not drift:
                skip(f"Jellyfin: {accel_type} + hardware decode already correct")
            else:
                r = requests.post(
                    f"{JELLYFIN_URL}/System/Configuration/encoding",
                    headers=headers,
                    json=enc,
                    timeout=30,
                )
                r.raise_for_status()
                ok(f"Jellyfin: {accel_type} hardware transcoding corrected ({', '.join(drift)})")
                info(f"(harmless if this machine has no {GPU_VENDOR.upper()} GPU -- Jellyfin falls back to CPU)")
        except requests.RequestException as e:
            warn(f"Jellyfin: could not set transcoding options ({e})")

    # Real-time monitoring (inotify) only sees writes made from inside a
    # container -- host-side copies (e.g. from Windows Explorer) never trigger
    # it. A StartupTrigger closes that gap on every restart; the existing
    # interval trigger(s) stay as a periodic backstop in between restarts.
    try:
        tasks = requests.get(f"{JELLYFIN_URL}/ScheduledTasks", headers=headers, timeout=30).json()
        scan_task = next((t for t in tasks if t.get("Key") == "RefreshLibrary"), None)
        if scan_task is None:
            warn("Jellyfin: could not find the 'Scan Media Library' scheduled task")
        else:
            triggers = scan_task.get("Triggers", [])
            if any(t.get("Type") == "StartupTrigger" for t in triggers):
                skip("Jellyfin: library scan already runs on startup")
            else:
                triggers.append({"Type": "StartupTrigger"})
                r = requests.post(
                    f"{JELLYFIN_URL}/ScheduledTasks/{scan_task['Id']}/Triggers",
                    headers=headers,
                    json=triggers,
                    timeout=30,
                )
                r.raise_for_status()
                ok("Jellyfin: library scan now also runs on startup")
    except requests.RequestException as e:
        warn(f"Jellyfin: could not set startup library scan trigger ({e})")


# --- Sonarr / Radarr -> Jellyfin --------------------------------------------


def configure_jellyfin_notifications(sonarr_key: str, radarr_key: str) -> None:
    """Tell Jellyfin to rescan the moment Sonarr/Radarr finish an import.

    Without this, Jellyfin only learns about new files on its own 12-hourly
    scan (plus the startup trigger). That scan races multi-file imports --
    a season pack landing while the scan is mid-folder produces episodes
    Jellyfin cannot place, and they end up unplayable. A post-import library
    update from the *arr is a targeted rescan of just that one folder, fired
    once the files are already in place.
    """
    step("Sonarr / Radarr -> Jellyfin")

    token = jellyfin_token()
    if token is None:
        skip("Jellyfin: no admin credentials in .env -- cannot create an API key for the *arrs")
        _manual.append(
            "Sonarr/Radarr: add a 'Emby / Jellyfin' connection (Settings -> Connect), "
            "host jellyfin, port 8096, 'Update Library' on"
        )
        return
    jf_headers = {"X-Emby-Token": token}

    # Import/rename events change files on disk; grab/health do not. onGrab is
    # deliberately left off -- it fires before the file exists.
    wanted_events = {
        "onDownload", "onUpgrade", "onRename", "onImportComplete",
        "onEpisodeFileDelete", "onEpisodeFileDeleteForUpgrade", "onSeriesDelete",
        "onMovieFileDelete", "onMovieFileDeleteForUpgrade", "onMovieDelete",
    }

    targets = [("Sonarr", SONARR_URL, sonarr_key), ("Radarr", RADARR_URL, radarr_key)]
    for label, base, key in targets:
        if not key:
            continue
        try:
            existing = arr_get(base, "/api/v3/notification", key)
            current = next((n for n in existing if n.get("implementation") == "MediaBrowser"), None)

            # The *arr masks the apiKey field in GET responses ("********"), so
            # it cannot be verified -- judge by the observable fields instead.
            if (
                current
                and get_field(current, "host") == "jellyfin"
                and get_field(current, "updateLibrary") is True
                and current.get("onDownload")
            ):
                skip(f"{label}: Jellyfin library-update connection already set")
                continue

            jf_key = _ensure_jellyfin_api_key(jf_headers, label)
            if jf_key is None:
                warn(f"{label}: could not create a Jellyfin API key")
                continue

            schema = arr_get(base, "/api/v3/notification/schema", key)
            template = next((s for s in schema if s.get("implementation") == "MediaBrowser"), None)
            if template is None:
                warn(f"{label}: no 'MediaBrowser' notification schema, skipping")
                continue

            payload = dict(current) if current else dict(template)
            payload["name"] = "Jellyfin"
            payload["implementation"] = "MediaBrowser"
            payload["implementationName"] = template.get("implementationName", "Emby / Jellyfin")
            payload["configContract"] = "MediaBrowserSettings"
            for k in list(payload):
                if k.startswith("on") and f"supportsOn{k[2:]}" in template:
                    payload[k] = k in wanted_events
            set_field(payload, "host", "jellyfin")
            set_field(payload, "port", 8096)
            set_field(payload, "useSsl", False)
            set_field(payload, "apiKey", jf_key)
            set_field(payload, "updateLibrary", True)
            set_field(payload, "notify", False)

            if current:
                arr_put(base, f"/api/v3/notification/{current['id']}", key, payload)
                ok(f"{label}: Jellyfin library-update connection corrected")
            else:
                arr_post(base, "/api/v3/notification", key, payload)
                ok(f"{label}: Jellyfin library-update connection added")
        except requests.HTTPError as e:
            warn(f"{label}: could not set Jellyfin connection -- {e.response.text[:200]}")
        except requests.RequestException as e:
            warn(f"{label}: could not set Jellyfin connection ({e})")


def _ensure_jellyfin_api_key(jf_headers: dict, app: str) -> str | None:
    """Return an existing Jellyfin API key for `app`, creating one if needed."""
    keys = requests.get(f"{JELLYFIN_URL}/Auth/Keys", headers=jf_headers, timeout=30).json()
    for k in keys.get("Items", []):
        if k.get("AppName") == app:
            return k.get("AccessToken")
    # POST /Auth/Keys returns 204 with no body -- re-read to get the token.
    requests.post(
        f"{JELLYFIN_URL}/Auth/Keys", headers=jf_headers, params={"app": app}, timeout=30
    ).raise_for_status()
    keys = requests.get(f"{JELLYFIN_URL}/Auth/Keys", headers=jf_headers, timeout=30).json()
    return next((k.get("AccessToken") for k in keys.get("Items", []) if k.get("AppName") == app), None)


# --- Jellyseerr ---------------------------------------------------------------


def configure_jellyseerr(sonarr_key: str, radarr_key: str) -> None:
    """Complete Jellyseerr's setup wizard: sign in with Jellyfin, enable the
    libraries, and register Sonarr/Radarr as request targets.

    Unlike every other app here, Jellyseerr authenticates with a SESSION COOKIE
    rather than an API key -- hence requests.Session() throughout.
    """
    step("Jellyseerr")

    if not JELLYFIN_USER or not JELLYFIN_PASSWORD:
        warn("Jellyseerr: needs JELLYFIN_ADMIN_USER/PASSWORD in .env, skipping")
        _manual.append("Jellyseerr: run its wizard at http://localhost:5055 (signs in with Jellyfin)")
        return

    s = requests.Session()

    try:
        public = s.get(f"{JELLYSEERR_URL}/api/v1/settings/public", timeout=30).json()
    except requests.RequestException as e:
        warn(f"Jellyseerr: not reachable ({e})")
        return
    initialized = bool(public.get("initialized"))

    # Sign in. On a FRESH install this also creates the admin account and
    # stores the Jellyfin connection. On a re-run the hostname fields must be
    # omitted -- Jellyseerr rejects them outright once configured with
    # "Jellyfin hostname already configured", which would fail the whole step.
    payload: dict = {"username": JELLYFIN_USER, "password": JELLYFIN_PASSWORD}
    if not initialized:
        payload.update(
            {
                "hostname": "jellyfin",  # bare host + port, NOT a URL
                "port": 8096,
                "useSsl": False,
                "urlBase": "",
                "serverType": 2,  # MediaServerType.JELLYFIN
            }
        )
        if JELLYSEERR_EMAIL:
            payload["email"] = JELLYSEERR_EMAIL
    try:
        r = s.post(f"{JELLYSEERR_URL}/api/v1/auth/jellyfin", json=payload, timeout=60)
        r.raise_for_status()
        ok("Jellyseerr: signed in with Jellyfin" if initialized else "Jellyseerr: admin account created")
    except requests.RequestException as e:
        detail = getattr(e.response, "text", "")[:200] if getattr(e, "response", None) is not None else e
        warn(f"Jellyseerr: could not sign in -- {detail}")
        _manual.append("Jellyseerr: finish its wizard by hand at http://localhost:5055")
        return

    # Libraries. Enabling them is what makes titles show as already-available
    # instead of requestable.
    #
    # ⚠ /settings/jellyfin/library REWRITES every library's enabled flag from
    # its `enable` query param on EVERY call -- so hitting it without that
    # param (to sync, or just to look) silently DISABLES everything. Never
    # call it to read. Read the state from /settings/jellyfin instead, and
    # always pass sync and enable together in one shot.
    try:
        current = s.get(f"{JELLYSEERR_URL}/api/v1/settings/jellyfin", timeout=30).json().get("libraries", [])
        if current and all(lib.get("enabled") for lib in current):
            skip(f"Jellyseerr: {len(current)} librar{'y' if len(current) == 1 else 'ies'} already enabled")
        else:
            found = s.get(
                f"{JELLYSEERR_URL}/api/v1/settings/jellyfin/library",
                params={"sync": "true", "enable": ",".join(lib["id"] for lib in current)} if current else {"sync": "true"},
                timeout=60,
            ).json()
            if not found:
                warn("Jellyseerr: Jellyfin reported no libraries -- create them there first")
            else:
                libs = s.get(
                    f"{JELLYSEERR_URL}/api/v1/settings/jellyfin/library",
                    params={"enable": ",".join(lib["id"] for lib in found)},
                    timeout=60,
                ).json()
                ok(f"Jellyseerr: enabled {len(libs)} librar{'y' if len(libs) == 1 else 'ies'}")
    except requests.RequestException as e:
        warn(f"Jellyseerr: could not sync libraries ({e})")

    # Sonarr / Radarr. The /test endpoint doubles as the only way to read an
    # app's quality profiles and root folders through Jellyseerr.
    targets = (
        ("radarr", "Radarr", radarr_key, 7878, f"{MEDIA_ROOT_IN_CONTAINER}/library/movies",
         JELLYSEERR_RADARR_PROFILE),
        ("sonarr", "Sonarr", sonarr_key, 8989, f"{MEDIA_ROOT_IN_CONTAINER}/library/tv",
         JELLYSEERR_SONARR_PROFILE),
    )
    for slug, label, key, port, want_dir, want_profile in targets:
        if not key:
            warn(f"Jellyseerr: no {label} API key, skipping")
            continue
        try:
            existing = s.get(f"{JELLYSEERR_URL}/api/v1/settings/{slug}", timeout=30).json()

            conn = {"hostname": slug, "port": port, "apiKey": key, "useSsl": False, "baseUrl": ""}
            probe = s.post(f"{JELLYSEERR_URL}/api/v1/settings/{slug}/test", json=conn, timeout=60)
            probe.raise_for_status()
            probe = probe.json()

            profiles = probe.get("profiles", [])
            roots = [r["path"] for r in probe.get("rootFolders", [])]
            if not profiles or not roots:
                warn(f"Jellyseerr: {label} returned no profiles/root folders")
                continue

            # Prefer the configured name. Falling back silently is how people end
            # up requesting everything at SD, so say so loudly when it happens --
            # and never fall back to "Any", which despite the name EXCLUDES 4K.
            chosen = next((p for p in profiles if p["name"] == want_profile), None)
            if chosen is None:
                chosen = next((p for p in profiles if p["name"] == "HD-1080p"), None)
                chosen = chosen or next((p for p in profiles if p["name"].lower() != "any"), profiles[0])
                warn(
                    f"Jellyseerr: {label} has no quality profile named '{want_profile}' -- "
                    f"falling back to '{chosen['name']}'. Set JELLYSEERR_{label.upper()}_PROFILE "
                    "in .env to one of: " + ", ".join(p["name"] for p in profiles)
                )
            root = want_dir if want_dir in roots else roots[0]

            if existing:
                # Already connected. Only correct the quality profile if it has
                # drifted from what .env asks for -- anything else the user may
                # have tuned in Jellyseerr's UI is left exactly as it is.
                svc = next((e for e in existing if e.get("isDefault")), existing[0])
                if svc.get("activeProfileId") == chosen["id"]:
                    skip(f"Jellyseerr: {label} already connected -> {svc.get('activeProfileName')}")
                    continue
                # 'id' is read-only on this endpoint and 400s if sent back.
                body = {k: v for k, v in svc.items() if k != "id"}
                body["activeProfileId"] = chosen["id"]
                body["activeProfileName"] = chosen["name"]
                s.put(
                    f"{JELLYSEERR_URL}/api/v1/settings/{slug}/{svc['id']}", json=body, timeout=60
                ).raise_for_status()
                ok(f"Jellyseerr: {label} quality profile {svc.get('activeProfileName')!r} -> {chosen['name']!r}")
                continue

            body = {
                **conn,
                "name": label,
                "activeProfileId": chosen["id"],
                "activeProfileName": chosen["name"],
                "activeDirectory": root,
                "is4k": False,
                "isDefault": True,
                "externalUrl": "",
                "syncEnabled": True,
                "preventSearch": False,
                "tagRequests": False,
            }
            if slug == "radarr":
                body["minimumAvailability"] = "released"
            else:
                body["enableSeasonFolders"] = True

            s.post(f"{JELLYSEERR_URL}/api/v1/settings/{slug}", json=body, timeout=60).raise_for_status()
            ok(f"Jellyseerr: {label} connected -> {chosen['name']}, {root}")
        except requests.RequestException as e:
            detail = getattr(e.response, "text", "")[:200] if getattr(e, "response", None) is not None else e
            warn(f"Jellyseerr: could not connect {label} -- {detail}")

    if initialized:
        skip("Jellyseerr: setup already finalized")
    else:
        try:
            s.post(f"{JELLYSEERR_URL}/api/v1/settings/initialize", json={}, timeout=30).raise_for_status()
            ok("Jellyseerr: setup complete -- http://localhost:5055")
        except requests.RequestException as e:
            warn(f"Jellyseerr: could not finalize setup ({e})")


# --- main --------------------------------------------------------------------


def main() -> int:
    print("\n" + "=" * 70)
    print("  Media stack setup -- configures everything that doesn't need")
    print("  your own accounts. Safe to re-run at any time.")
    print("=" * 70)

    # Before waiting on services: a wrongly-owned recyclarr-config makes the
    # recyclarr container crash-loop, and the fix is on the host filesystem, not
    # in any app's API. Do it first so a fresh clone comes up clean.
    configure_host_permissions()

    step("Waiting for services")
    wait_for("qBittorrent", f"{QBIT_URL}/api/v2/app/version")
    wait_for("Prowlarr", f"{PROWLARR_URL}/api/v1/system/status")
    wait_for("Sonarr", f"{SONARR_URL}/api/v3/system/status")
    wait_for("Radarr", f"{RADARR_URL}/api/v3/system/status")
    wait_for("Bazarr", f"{BAZARR_URL}/")
    wait_for("SABnzbd", f"{SABNZBD_URL}/")
    wait_for("Jellyfin", f"{JELLYFIN_URL}/System/Info/Public")
    wait_for("Jellyseerr", f"{JELLYSEERR_URL}/api/v1/status")

    step("Reading API keys from config volumes")
    sonarr_key = read_arr_api_key("/keys/sonarr/config.xml", "Sonarr")
    radarr_key = read_arr_api_key("/keys/radarr/config.xml", "Radarr")
    prowlarr_key = read_arr_api_key("/keys/prowlarr/config.xml", "Prowlarr")
    bazarr_key = read_bazarr_api_key("/keys/bazarr/config/config.yaml")
    sab_key = read_sabnzbd_api_key("/keys/sabnzbd/sabnzbd.ini")
    for label, k in (
        ("Sonarr", sonarr_key), ("Radarr", radarr_key), ("Prowlarr", prowlarr_key),
        ("Bazarr", bazarr_key), ("SABnzbd", sab_key),
    ):
        if k:
            ok(f"{label} API key found")

    configure_qbittorrent()

    if prowlarr_key:
        configure_prowlarr_apps(prowlarr_key, sonarr_key or "", radarr_key or "")
        configure_byparr_proxy(prowlarr_key)
        _manual.append("Prowlarr: add your indexers (http://localhost:9696) -- needs your own accounts")

    for app, base, key in (("sonarr", SONARR_URL, sonarr_key), ("radarr", RADARR_URL, radarr_key)):
        if not key:
            continue
        step(f"{app.capitalize()}")
        configure_download_clients(app, base, key, sab_key)
        configure_root_folder(app, base, key)
        configure_quality_sizes(app, base, key)
        configure_min_seeders(app, base, key)
        configure_completed_handling(app, base, key)

    configure_sabnzbd(sab_key)
    configure_bazarr(bazarr_key, sonarr_key or "", radarr_key or "")
    configure_jellyfin()
    configure_jellyfin_notifications(sonarr_key or "", radarr_key or "")
    configure_jellyseerr(sonarr_key or "", radarr_key or "")

    print("\n" + "=" * 70)
    if _problems:
        print(f"  Completed with {len(_problems)} warning(s):")
        for p in _problems:
            print(f"    - {p}")
    else:
        print("  Completed with no warnings.")

    if _manual:
        print("\n  Still needs you (these require your own accounts/choices):")
        for m in _manual:
            print(f"    - {m}")

    print("\n  Re-run this any time: docker compose run --rm setup")
    print("=" * 70 + "\n")
    return 1 if _problems else 0


if __name__ == "__main__":
    sys.exit(main())
