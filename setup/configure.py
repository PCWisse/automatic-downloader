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
"""

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
# The host Sonarr/Radarr are told to use for the qBittorrent download client.
# Not always the same string as QBIT_URL's host -- kept separate on purpose.
QBIT_HOST = os.environ.get("QBIT_HOST", "gluetun")
SONARR_URL = "http://sonarr:8989"
RADARR_URL = "http://radarr:7878"
BAZARR_URL = "http://bazarr:6767"
SABNZBD_URL = "http://sabnzbd:8080"
JELLYFIN_URL = "http://jellyfin:8096"

QBIT_USER = os.environ.get("QBITTORRENT_USER", "")
QBIT_PASSWORD = os.environ.get("QBITTORRENT_PASSWORD", "")
JELLYFIN_USER = os.environ.get("JELLYFIN_ADMIN_USER", "")
JELLYFIN_PASSWORD = os.environ.get("JELLYFIN_ADMIN_PASSWORD", "")

# Tunables that mirror the README's documented values.
MAX_ACTIVE_DOWNLOADS = int(os.environ.get("QBIT_MAX_ACTIVE_DOWNLOADS", 4))
SEED_RATIO_LIMIT = float(os.environ.get("QBIT_SEED_RATIO_LIMIT", 1.0))
SEED_TIME_LIMIT_MIN = int(os.environ.get("QBIT_SEED_TIME_LIMIT_MIN", 180))
MIN_SEEDERS = int(os.environ.get("INDEXER_MIN_SEEDERS", 5))
MAX_SIZE_MB_PER_MIN_2160P = int(os.environ.get("MAX_SIZE_MB_PER_MIN_2160P", 252))

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


def configure_qbittorrent() -> bool:
    step("qBittorrent")
    if not QBIT_USER or not QBIT_PASSWORD:
        warn(
            "QBITTORRENT_USER / QBITTORRENT_PASSWORD not set in .env -- skipping. "
            "qBittorrent generates a random temporary password on first start and only "
            "prints it to its Docker log, so it cannot be read from here. "
            "See README step 6, then re-run this script."
        )
        _manual.append("Set a permanent qBittorrent password (README step 6), put it in .env, re-run setup")
        return False

    s = requests.Session()
    r = s.post(
        f"{QBIT_URL}/api/v2/auth/login",
        data={"username": QBIT_USER, "password": QBIT_PASSWORD},
        timeout=30,
    )
    # 5.x answers 204 with a session cookie; older versions answered 200 "Ok."
    if not any(c.startswith("QBT_SID") for c in s.cookies.keys()):
        warn(
            f"qBittorrent login failed (HTTP {r.status_code}). If this is a fresh install, set a "
            "permanent password first (README step 6) and put it in .env."
        )
        _manual.append("Fix qBittorrent credentials in .env, then re-run setup")
        return False
    ok("authenticated")

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
    resp = s.post(f"{QBIT_URL}/api/v2/app/setPreferences", data={"json": __import__("json").dumps(prefs)}, timeout=30)
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

    try:
        auth = requests.post(
            f"{JELLYFIN_URL}/Users/AuthenticateByName",
            json={"Username": JELLYFIN_USER, "Pw": JELLYFIN_PASSWORD},
            headers={
                "Authorization": 'MediaBrowser Client="setup", Device="setup", DeviceId="setup", Version="1.0.0"'
            },
            timeout=30,
        )
        auth.raise_for_status()
        token = auth.json()["AccessToken"]
    except requests.RequestException as e:
        warn(f"Jellyfin: could not authenticate as '{JELLYFIN_USER}' ({e})")
        _manual.append("Jellyfin: check JELLYFIN_ADMIN_USER / JELLYFIN_ADMIN_PASSWORD in .env")
        return

    headers = {"X-Emby-Token": token}
    try:
        folders = requests.get(f"{JELLYFIN_URL}/Library/VirtualFolders", headers=headers, timeout=30).json()
        have = {f.get("Name") for f in folders}
    except requests.RequestException as e:
        warn(f"Jellyfin: could not list libraries ({e})")
        return

    for name, ctype, path in (("Movies", "movies", "/media/movies"), ("Shows", "tvshows", "/media/tv")):
        if name in have:
            skip(f"Jellyfin: library '{name}' already exists")
            continue
        try:
            # /media is mounted read-only, so metadata/artwork must NOT be saved
            # into the media folders -- that would log write failures every scan.
            options = {
                "EnableRealtimeMonitor": True,
                "SaveLocalMetadata": False,
                "EnableInternetProviders": True,
                "PathInfos": [{"Path": path}],
            }
            r = requests.post(
                f"{JELLYFIN_URL}/Library/VirtualFolders",
                headers=headers,
                params={"name": name, "collectionType": ctype, "refreshLibrary": "true"},
                json=options,
                timeout=60,
            )
            r.raise_for_status()
            ok(f"Jellyfin: library '{name}' -> {path}")
        except requests.RequestException as e:
            warn(f"Jellyfin: could not create library '{name}' ({e})")

    # Hardware transcoding: passing the GPU through in compose is necessary but
    # NOT sufficient -- it must be switched on here too, or it silently uses CPU.
    try:
        enc = requests.get(f"{JELLYFIN_URL}/System/Configuration/encoding", headers=headers, timeout=30).json()
        if enc.get("HardwareAccelerationType") in ("nvenc", "NVENC"):
            skip("Jellyfin: NVENC already enabled")
        else:
            enc["HardwareAccelerationType"] = "nvenc"
            enc["EnableHardwareEncoding"] = True
            enc["EnableTonemapping"] = True  # HDR looks washed out on SDR screens without this
            r = requests.post(
                f"{JELLYFIN_URL}/System/Configuration/encoding",
                headers=headers,
                json=enc,
                timeout=30,
            )
            r.raise_for_status()
            ok("Jellyfin: NVENC hardware transcoding + tone mapping enabled")
            info("(harmless if this machine has no NVIDIA GPU -- Jellyfin falls back to CPU)")
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


# --- main --------------------------------------------------------------------


def main() -> int:
    print("\n" + "=" * 70)
    print("  Media stack setup -- configures everything that doesn't need")
    print("  your own accounts. Safe to re-run at any time.")
    print("=" * 70)

    step("Waiting for services")
    wait_for("qBittorrent", f"{QBIT_URL}/api/v2/app/version")
    wait_for("Prowlarr", f"{PROWLARR_URL}/api/v1/system/status")
    wait_for("Sonarr", f"{SONARR_URL}/api/v3/system/status")
    wait_for("Radarr", f"{RADARR_URL}/api/v3/system/status")
    wait_for("Bazarr", f"{BAZARR_URL}/")
    wait_for("SABnzbd", f"{SABNZBD_URL}/")
    wait_for("Jellyfin", f"{JELLYFIN_URL}/System/Info/Public")

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
