"""
Watches active qBittorrent downloads. If one sustains below SPEED_THRESHOLD_BPS
for SUSTAINED_SECONDS, it is STOPPED (not deleted -- progress is preserved) and
a different release for the same episode/movie is grabbed to try instead.
After MAX_RETRIES attempts, whichever stopped candidate made the most progress
is resumed as the final answer; the rest are left stopped, untouched, for you
to clean up manually if you want the disk space back.

This is deliberately conservative compared to an earlier version of this
script, which deleted the slow download outright on every retry. That version
thrashed for hours without ever finishing anything, because it had no memory
of prior attempts and threw away real progress every time it acted. Nothing
here is ever deleted automatically anymore.

Usenet (SABnzbd) is deliberately not covered: there is no "seeders" concept
and no alternate release to fall back to the way there is with torrents.

Also watches for indexers stuck in Sonarr/Radarr's failure backoff. Those keep
their own backoff, separate from Prowlarr's, and it does not always clear once
a tracker recovers -- leaving searches to report "0 active indexers" and return
nothing, which is indistinguishable from "no releases exist" unless you go
looking in the logs. Re-testing a parked indexer clears it, so that runs here
on a slow timer.
"""

import json
import os
import time
import requests

QBIT_URL = os.environ["QBIT_URL"]
QBIT_USER = os.environ["QBIT_USER"]
QBIT_PASSWORD = os.environ["QBIT_PASSWORD"]

SONARR_URL = os.environ["SONARR_URL"]
SONARR_API_KEY = os.environ["SONARR_API_KEY"]
RADARR_URL = os.environ["RADARR_URL"]
RADARR_API_KEY = os.environ["RADARR_API_KEY"]

SPEED_THRESHOLD_BPS = int(os.environ.get("SPEED_THRESHOLD_BPS", 512 * 1024))  # 0.5 MiB/s
SUSTAINED_SECONDS = int(os.environ.get("SUSTAINED_SECONDS", 15 * 60))
GRACE_PERIOD_SECONDS = int(os.environ.get("GRACE_PERIOD_SECONDS", 10 * 60))
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", 60))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 2))
PROGRESS_GUARD = float(os.environ.get("PROGRESS_GUARD", 0.20))  # 20%
STATE_FILE = os.environ.get("RETRY_STATE_FILE", "/state/retry_state.json")

ACTIVE_STATES = {"downloading", "stalledDL", "metaDL", "forcedDL"}

# How often to look for indexers stuck in Sonarr/Radarr's failure backoff.
# Deliberately far slower than POLL_INTERVAL_SECONDS: each retest is a real
# request to a real tracker, so this must not run on the 60s download loop.
INDEXER_CHECK_INTERVAL_SECONDS = int(os.environ.get("INDEXER_CHECK_INTERVAL_SECONDS", 15 * 60))


# Sonarr/Radarr health-check identifiers for "indexers are in backoff". Matched
# on `source` rather than the message text, which is prose and changes.
INDEXER_HEALTH_SOURCES = {"IndexerStatusCheck", "IndexerLongTermStatusCheck"}

# Unix timestamp of the last indexer sweep. 0 = never, so the first cycle runs.
last_indexer_check: float = 0.0

# hash -> unix timestamp when it first dropped below the threshold.
# In-memory only -- resetting this on restart just gives a slow torrent a
# fresh grace window, which is harmless.
low_since: dict[str, float] = {}

# media_key -> {"attempts": [{hash, title, progress}], "decided": bool}
# Persisted to disk: this is what remembers prior attempts across restarts
# and prevents re-litigating an episode after a winner's been picked.
state: dict[str, dict] = {}

qbit_session = requests.Session()


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_state() -> None:
    global state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        log(f"loaded state: {len(state)} tracked episode/movie item(s)")
    except FileNotFoundError:
        state = {}
        log("no prior state file -- starting fresh")
    except Exception as e:
        log(f"WARN: could not load state ({e}), starting fresh")
        state = {}


def save_state() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        log(f"WARN: could not save state: {e}")


def media_key(app_name: str, rec: dict) -> str | None:
    if app_name == "sonarr" and rec.get("episodeId"):
        return f"sonarr:episode:{rec['episodeId']}"
    if app_name == "radarr" and rec.get("movieId"):
        return f"radarr:movie:{rec['movieId']}"
    return None


def qbit_login() -> None:
    r = qbit_session.post(
        f"{QBIT_URL}/api/v2/auth/login",
        data={"username": QBIT_USER, "password": QBIT_PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    # qBittorrent 5.x returns an empty 204 on success, not the old "Ok." body.
    # The session cookie is the real proof of success -- checked directly.
    if not any(c.startswith("QBT_SID") for c in qbit_session.cookies.keys()):
        raise RuntimeError(f"qBittorrent login rejected: HTTP {r.status_code}, no session cookie set")


def qbit_get(path: str, **params) -> requests.Response:
    r = qbit_session.get(f"{QBIT_URL}{path}", params=params, timeout=15)
    if r.status_code == 403:
        qbit_login()
        r = qbit_session.get(f"{QBIT_URL}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r


def qbit_post(path: str, **data) -> requests.Response:
    r = qbit_session.post(f"{QBIT_URL}{path}", data=data, timeout=15)
    if r.status_code == 403:
        qbit_login()
        r = qbit_session.post(f"{QBIT_URL}{path}", data=data, timeout=15)
    r.raise_for_status()
    return r


def qbit_torrents() -> list[dict]:
    return qbit_get("/api/v2/torrents/info").json()


def qbit_stop(torrent_hash: str) -> None:
    # qBittorrent 5.x renamed "pause" -> "stop" at the API level, not just in
    # the UI. Verified directly: /api/v2/torrents/pause is a 404 on this
    # version; /api/v2/torrents/stop is the real endpoint.
    qbit_post("/api/v2/torrents/stop", hashes=torrent_hash)


def qbit_start(torrent_hash: str) -> None:
    qbit_post("/api/v2/torrents/start", hashes=torrent_hash)


def find_owning_app(torrent_hash: str) -> tuple[str, str, str, dict] | None:
    """Returns (app_name, base_url, api_key, queue_record) or None."""
    for app_name, base_url, api_key in (
        ("sonarr", SONARR_URL, SONARR_API_KEY),
        ("radarr", RADARR_URL, RADARR_API_KEY),
    ):
        try:
            r = requests.get(
                f"{base_url}/api/v3/queue",
                headers={"X-Api-Key": api_key},
                params={"pageSize": 200, "includeUnknownItems": "true"},
                timeout=15,
            )
            r.raise_for_status()
            for rec in r.json().get("records", []):
                if (rec.get("downloadId") or "").lower() == torrent_hash.lower():
                    return app_name, base_url, api_key, rec
        except requests.RequestException as e:
            log(f"WARN: could not query {app_name} queue: {e}")
    return None


def grab_alternative_release(app_name: str, base_url: str, api_key: str, rec: dict, already_tried_titles: set[str]) -> bool:
    """Search fresh candidates and grab the best one not already tried. Returns True if a grab was made."""
    id_field = "episodeId" if app_name == "sonarr" else "movieId"
    media_id = rec.get(id_field)
    if not media_id:
        return False

    try:
        r = requests.get(
            f"{base_url}/api/v3/release",
            headers={"X-Api-Key": api_key},
            params={id_field: media_id},
            timeout=60,
        )
        r.raise_for_status()
        candidates = r.json()
    except requests.RequestException as e:
        log(f"WARN: release search failed for {app_name} {id_field}={media_id}: {e}")
        return False

    # Skip anything Sonarr/Radarr already rejected (quality/size/seeder
    # filters already applied) and anything with a title we've already tried.
    viable = [c for c in candidates if not c.get("rejected") and c.get("title") not in already_tried_titles]
    if not viable:
        log(f"no untried, non-rejected alternative releases available for {app_name} {id_field}={media_id}")
        return False

    pick = viable[0]  # release search results are already ranked by score
    try:
        r = requests.post(
            f"{base_url}/api/v3/release",
            headers={"X-Api-Key": api_key},
            json={"guid": pick["guid"], "indexerId": pick["indexerId"]},
            timeout=30,
        )
        r.raise_for_status()
        log(f"grabbed alternative release via {app_name}: {pick['title'][:70]}")
        return True
    except requests.RequestException as e:
        log(f"ERROR: failed to grab alternative release: {e}")
        return False


def stuck_indexer_names(base_url: str, api_key: str) -> set[str]:
    """Names of indexers Sonarr/Radarr has parked in its failure backoff.

    Sonarr/Radarr keep their OWN backoff, separate from Prowlarr's. An indexer
    that recovered on Prowlarr's side can stay parked here, and while it is,
    searches log "0 active indexers" and silently return nothing -- which looks
    exactly like "no releases exist" from the outside.
    """
    r = requests.get(f"{base_url}/api/v3/health", headers={"X-Api-Key": api_key}, timeout=15)
    r.raise_for_status()
    names: set[str] = set()
    for item in r.json():
        if item.get("source") not in INDEXER_HEALTH_SOURCES:
            continue
        # "Indexers unavailable due to failures: 1337x (Prowlarr), YTS (Prowlarr)"
        _, _, listed = item.get("message", "").partition(":")
        names.update(n.strip() for n in listed.split(",") if n.strip())
    return names


def retest_stuck_indexers(app_name: str, base_url: str, api_key: str) -> None:
    """Re-test parked indexers. A passing test clears the backoff, which is all
    the UI's Test button does -- this is that, on a timer."""
    try:
        stuck = stuck_indexer_names(base_url, api_key)
    except requests.RequestException as e:
        log(f"WARN: could not read {app_name} health: {e}")
        return
    if not stuck:
        return

    try:
        indexers = requests.get(f"{base_url}/api/v3/indexer", headers={"X-Api-Key": api_key}, timeout=15).json()
    except requests.RequestException as e:
        log(f"WARN: could not list {app_name} indexers: {e}")
        return

    # Names come out of prose, so a parse miss is possible. Falling back to all
    # indexers is safe here: we only reach this branch when the health check
    # already told us something IS parked.
    targets = [i for i in indexers if i.get("name") in stuck] or indexers
    log(f"{app_name}: {len(targets)} indexer(s) in failure backoff, re-testing")

    for idx in targets:
        name = idx.get("name", "?")
        try:
            r = requests.post(
                f"{base_url}/api/v3/indexer/test",
                headers={"X-Api-Key": api_key},
                json=idx,
                timeout=90,
            )
        except requests.RequestException as e:
            log(f"  {app_name}: {name} still unreachable ({e})")
            continue
        if r.ok:
            log(f"  {app_name}: {name} RECOVERED -- backoff cleared")
        else:
            log(f"  {app_name}: {name} still failing (HTTP {r.status_code}) -- leaving it parked")


def check_indexers() -> None:
    for app_name, base_url, api_key in (
        ("sonarr", SONARR_URL, SONARR_API_KEY),
        ("radarr", RADARR_URL, RADARR_API_KEY),
    ):
        retest_stuck_indexers(app_name, base_url, api_key)


def resolve_episode(key: str) -> None:
    """MAX_RETRIES reached: pick the best-progressed stopped candidate, resume
    it, leave the rest stopped (never deleted), and stop managing this item."""
    attempts = state[key]["attempts"]
    best = max(attempts, key=lambda a: a["progress"])
    log(
        f"DECIDED ({key}): resuming best candidate '{best['title'][:60]}' "
        f"({best['progress']*100:.0f}% progress) out of {len(attempts)} tried; "
        f"the rest stay stopped, untouched, for manual cleanup if you want them gone"
    )
    qbit_start(best["hash"])
    state[key]["decided"] = True
    save_state()


def check_once() -> None:
    now = time.time()
    torrents = qbit_torrents()
    seen_hashes = set()

    for t in torrents:
        if t["state"] not in ACTIVE_STATES:
            continue
        h = t["hash"]
        seen_hashes.add(h)
        age = now - t["added_on"]
        if age < GRACE_PERIOD_SECONDS:
            continue

        if t["dlspeed"] >= SPEED_THRESHOLD_BPS:
            low_since.pop(h, None)
            continue

        since = low_since.setdefault(h, now)
        slow_for = now - since
        if slow_for < SUSTAINED_SECONDS:
            log(
                f"watching: {t['name'][:60]} | {t['dlspeed']/1024/1024:.2f} MiB/s | "
                f"progress={t['progress']*100:.0f}% | slow for {slow_for/60:.1f}/{SUSTAINED_SECONDS/60:.0f} min"
            )
            continue

        if t["progress"] >= PROGRESS_GUARD:
            log(
                f"PROTECTED (progress {t['progress']*100:.0f}% >= {PROGRESS_GUARD*100:.0f}%): "
                f"not touching '{t['name'][:60]}' despite sustained-slow -- letting it finish"
            )
            low_since.pop(h, None)
            continue

        owner = find_owning_app(h)
        if owner is None:
            log(f"WARN: '{t['name'][:60]}' is sustained-slow but not found in Sonarr or Radarr's queue -- skipping")
            low_since.pop(h, None)
            continue
        app_name, base_url, api_key, rec = owner

        key = media_key(app_name, rec)
        if key is None:
            log(f"WARN: could not determine episode/movie identity for '{t['name'][:60]}' -- skipping")
            low_since.pop(h, None)
            continue

        entry = state.setdefault(key, {"attempts": [], "decided": False})
        if entry["decided"]:
            # Already resolved this one previously; do not re-litigate it.
            low_since.pop(h, None)
            continue

        log(f"STOPPING (sustained slow, {t['progress']*100:.0f}% progress -- kept, not deleted): {t['name'][:60]}")
        qbit_stop(h)
        entry["attempts"].append({"hash": h, "title": t["name"], "progress": t["progress"]})
        save_state()
        low_since.pop(h, None)

        attempt_count = len(entry["attempts"])
        if attempt_count >= MAX_RETRIES:
            resolve_episode(key)
        else:
            already_tried = {a["title"] for a in entry["attempts"]}
            log(f"trying alternative release ({attempt_count}/{MAX_RETRIES} attempts so far) for {key}")
            grab_alternative_release(app_name, base_url, api_key, rec, already_tried)

    for h in list(low_since.keys()):
        if h not in seen_hashes:
            low_since.pop(h, None)


def main() -> None:
    log("speed-monitor starting")
    log(
        f"config: threshold={SPEED_THRESHOLD_BPS/1024/1024:.2f} MiB/s, "
        f"sustained={SUSTAINED_SECONDS/60:.0f} min, grace={GRACE_PERIOD_SECONDS/60:.0f} min, "
        f"poll={POLL_INTERVAL_SECONDS}s, max_retries={MAX_RETRIES}, progress_guard={PROGRESS_GUARD*100:.0f}%, "
        f"indexer_check={INDEXER_CHECK_INTERVAL_SECONDS/60:.0f} min"
    )
    load_state()
    qbit_login()
    log("qBittorrent login OK")

    global last_indexer_check
    while True:
        try:
            check_once()
        except Exception as e:
            log(f"ERROR in check cycle: {e}")

        # Separate, much slower cadence -- see INDEXER_CHECK_INTERVAL_SECONDS.
        if time.time() - last_indexer_check >= INDEXER_CHECK_INTERVAL_SECONDS:
            last_indexer_check = time.time()
            try:
                check_indexers()
            except Exception as e:
                log(f"ERROR in indexer check: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
