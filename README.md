# Automated Media Server — Jellyfin + *arr + VPN-protected downloads

A complete self-hosted media stack in Docker. You add a movie or show to a list;
it finds it, downloads it over a VPN or Usenet, renames it, files it, fetches
subtitles, and it appears in Jellyfin ready to watch.

| Service               | Role                                                                               | URL                                                 |
| --------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------- |
| **Jellyfin**    | Watch your library                                                                 | [http://localhost:8096](http://localhost:8096)       |
| **Radarr**      | Movies: wanted list, quality rules, imports                                        | [http://localhost:7878](http://localhost:7878)       |
| **Sonarr**      | TV: same, plus auto-grabs new episodes                                             | [http://localhost:8989](http://localhost:8989)       |
| **Prowlarr**    | Indexer manager — configure search sites once                                     | [http://localhost:9696](http://localhost:9696)       |
| **Bazarr**      | Subtitles, automatically                                                           | [http://localhost:6767](http://localhost:6767)       |
| **SABnzbd**     | Usenet downloader                                                                  | [http://localhost:8080](http://localhost:8080)       |
| **qBittorrent** | Torrent downloader                                                                 | [http://localhost:8090](http://localhost:8090)       |
| **gluetun**     | VPN tunnel + kill switch                                                           | [http://localhost:8010](http://localhost:8010) (API) |
| Byparr                | Anti-bot browser proxy — outside this guide's scope, not wired into Prowlarr here | —                                                  |
| Recyclarr             | Syncs TRaSH Guides quality profiles into Sonarr/Radarr (scheduled, not a live UI)  | —                                                  |

Everything except Jellyfin binds to `127.0.0.1` — admin UIs are not exposed to
your network. Jellyfin listens on all interfaces so phones and TVs can reach it.

**Designed for:** Windows + Docker Desktop (WSL2). Works on Linux with two small
changes noted in [Running on Linux](#running-on-linux).

> ### 👉 Setting this up for the first time?
>
> **Read [ONE-TIME-SETUP.md](ONE-TIME-SETUP.md) instead.** It's a short,
> copy-paste checklist that gets you running in ~20 minutes.
>
> This README is the **reference manual** — it explains every decision, documents
> what each setting does and why, and is where you come back when something
> breaks or you want to change how the stack behaves.

---

## How it works

```
      You add "The Matrix" to Radarr (or to a Trakt/IMDb list it syncs)
                              │
                              ▼
                    ┌──────────────────┐
                    │      Radarr      │  "who has this?"
                    │  (or Sonarr/TV)  │────────────────┐
                    └──────────────────┘                ▼
                              ▲                  ┌─────────────┐
                              │  release list    │  Prowlarr   │──► your indexers
                              └──────────────────│ (behind VPN)│    (torrent + usenet)
                                                 └─────────────┘
                              │
                    picks best match by quality profile
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
        ┌───────────────┐          ┌─────────────────┐
        │    SABnzbd    │          │   qBittorrent   │
        │  (usenet,     │          │  (torrents,     │
        │   direct)     │          │   behind VPN)   │
        └───────────────┘          └─────────────────┘
                └─────────────┬─────────────┘
                              ▼
              /data/usenet/complete  or  /data/torrents/complete
                              │
                     Radarr/Sonarr import:
                     rename + HARDLINK into
                              ▼
                    /data/library/movies|tv
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
        ┌───────────────┐          ┌─────────────────┐
        │   Jellyfin    │          │     Bazarr      │
        │  (watch it)   │          │  (subtitles)    │
        └───────────────┘          └─────────────────┘
```

**Radarr and Sonarr are the brains; everything else is a tool they drive.** In
normal use you never open qBittorrent or SABnzbd.

### What goes through the VPN, and what doesn't

| Container         | VPN? | Why                                                                                                            |
| ----------------- | ---- | -------------------------------------------------------------------------------------------------------------- |
| qBittorrent       | ✅   | Torrent swarms expose your IP to every peer                                                                    |
| Prowlarr          | ✅   | Hides which indexer sites you query from your ISP                                                              |
| Sonarr / Radarr   | ❌   | HTTPS to metadata APIs. Nothing to hide, and the VPN only adds latency                                         |
| SABnzbd           | ❌   | Usenet is one direct SSL connection to your provider — no swarm. Tunnelling it throws away most of your speed |
| Jellyfin / Bazarr | ❌   | Must be reachable on your LAN                                                                                  |

qBittorrent and Prowlarr use `network_mode: service:gluetun`, meaning they have
**no network path except the tunnel**. If the VPN drops they go dark rather than
falling back to your real connection. That is the kill switch, and it is
structural — not a setting that can be forgotten.

---

# QUICK START

> For a shorter, action-only version of this section, use
> **[ONE-TIME-SETUP.md](ONE-TIME-SETUP.md)**. What follows is the same process
> with the reasoning included.

Steps 1–6 get the stack running (~15 min), then **step 7 runs one command
that configures everything else automatically**. Steps 8–15 document what that
script does — you only need to read them for the three things it can't do for
you (indexers, Usenet provider, subtitle languages).

## Prerequisites

- **Docker Desktop** with the WSL2 backend (or Docker + Compose v2 on Linux)
- **A VPN account** — this ships configured for PrivadoVPN, but gluetun supports
  [most providers](https://github.com/qdm12/gluetun-wiki); see
  [Using a different VPN provider](#using-a-different-vpn-provider)
- **Disk space** on a single drive — 4K TV seasons are 50–100 GB each
- **Optional: an NVIDIA GPU** for hardware transcoding. Without one, delete the
  `deploy:` block from the `jellyfin` service or that container will not start
- **Optional: a Usenet subscription** — a provider (~€3–10/mo) *and* an indexer
  (~€10–15/yr). Without both, skip SABnzbd and use torrents only

## What is in this folder

| File                                                   | Purpose                                                                                                                                                                     |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ONE-TIME-SETUP.md`                                  | **Start here on a fresh install** — short copy-paste setup checklist                                                                                                 |
| `docker-compose.yml`                                 | The whole stack                                                                                                                                                             |
| `docs/docker-compose.annotated.yml`                  | Same stack, fully commented — read this, don't run it. Reference only, may drift from the real file                                                                        |
| `.env`                                               | Your credentials and paths.**Secret**, git-ignored                                                                                                                    |
| `.env.example`                                       | Template — copy to`.env`                                                                                                                                                 |
| `gluetun-auth.toml`                                  | API key for gluetun's control server.**Secret**, git-ignored                                                                                                          |
| `gluetun-auth.toml.example`                          | Template — copy and generate your own key                                                                                                                                  |
| `scripts\check-vpn.ps1` / `scripts/check-vpn.sh`   | Leak test + kill-switch test. Run before downloading. Windows/Linux, identical behavior                                                                                     |
| `scripts\vpn-toggle.ps1` / `scripts/vpn-toggle.sh` | Pause/resume the tunnel. Windows/Linux, identical behavior                                                                                                                  |
| `scripts\speedtest.ps1` / `scripts/speedtest.sh`   | Direct vs tunnelled throughput. Windows/Linux, identical behavior                                                                                                           |
| `docker-compose.novpn.yml`                           | ⚠ Full stack, no VPN — an alternative to`docker-compose.yml`, not an addition. See [ONE-TIME-SETUP.md](ONE-TIME-SETUP.md#optional-running-the-whole-stack-without-a-vpn) |
| `.gitignore`                                         | Keeps both secret files out of commits                                                                                                                                      |
| `setup/configure.py`                                 | One-shot API-based configurator —`docker compose run --rm setup`. Idempotent                                                                                             |
| `recyclarr-config/recyclarr.yml`                     | Quality profile sync config for Sonarr/Radarr.**Contains API keys** — same secret status as `.env`                                                                 |
| `speed-monitor/monitor.py`                           | ⚠ Custom-built, not an off-the-shelf project — see[Download quality and speed controls](#download-quality-and-speed-controls)                                              |

## 1. Create your `.env`

```
Copy-Item .env.example .env
```

Then edit it. Three things matter:

- **VPN credentials** — `.env` ships pre-filled for Privado over OpenVPN
  (`OPENVPN_USER` / `OPENVPN_PASSWORD`), but any provider gluetun supports
  works the same way — see [Using a different VPN provider](#using-a-different-vpn-provider).
  ⚠ Whichever provider you use, these are almost never your website login —
  most issue a *separate* OpenVPN/WireGuard username on a "manual setup" page
  in your account dashboard. Privado's is at
  [app.privadovpn.com/admin-panel](https://app.privadovpn.com/admin-panel). Using
  your email here is the single most common cause of `AUTH_FAILED`. If the
  password contains `$`, double it (`$$`) or Compose expands it as a variable.
- **`MEDIA_ROOT`** — one folder that will hold downloads *and* the library.
  Forward slashes, no trailing slash (`D:/media`, or `/srv/media` on Linux).
- **`TZ`** — your timezone.

## 2. Create the media folders

Everything must live under one root. On Windows:

```
$root = "D:\media"   # must match MEDIA_ROOT in .env
New-Item -ItemType Directory -Force `
  "$root\torrents\complete", "$root\torrents\incomplete", `
  "$root\usenet\complete",   "$root\usenet\incomplete", `
  "$root\library\movies",    "$root\library\tv"
```

Resulting layout, identical inside every container as `/data`:

```
MEDIA_ROOT/               ->  /data
├── torrents/complete         finished torrents (still seeding)
├── torrents/incomplete       partial downloads
├── usenet/complete           finished Usenet downloads
├── usenet/incomplete         partial Usenet downloads
└── library/movies|tv         the tidy library Jellyfin reads
```

**This single-root layout is mandatory**, not cosmetic — see
[Why one mount](#why-everything-shares-one-mount-hardlinks).

## 3. Generate your own gluetun API key

Never reuse a key from someone else's copy of this repo.

```
Copy-Item gluetun-auth.toml.example gluetun-auth.toml
docker run --rm qmcgaw/gluetun:v3 genkey
```

Paste the generated key into **both** `apikey = "..."` lines in
`gluetun-auth.toml`.

## 4. Start the stack

```
docker compose up -d
docker logs -f gluetun
```

Wait for `Initialization Sequence Completed` and a public IP line. qBittorrent
and Prowlarr will not start until gluetun reports healthy — that is deliberate.

`AUTH_FAILED` means step 1 credentials are wrong.

## 5. Verify the VPN before downloading anything

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-vpn.ps1
```

```bash
./scripts/check-vpn.sh
```

The container's IP must differ from yours, and stopping gluetun must kill
qBittorrent's connectivity entirely. **Do not skip this.**

## 6. Set a permanent qBittorrent password

This is the one step that **cannot** be automated. qBittorrent generates a
random temporary password on every restart and prints it **only to its Docker
log** — not to any file the setup script could read. Reading it would mean
handing the script access to the Docker socket, which is a much larger
privilege than this warrants.

```
docker logs qbittorrent | Select-String "temporary password" | Select-Object -Last 1
```

Log in at [http://localhost:8090](http://localhost:8090) as `admin`, then **immediately** set your own
password under *Options → Web UI → Authentication*. Until you do, every restart
locks you out and you must re-read the log.

Then put those credentials in `.env`:

```
QBITTORRENT_USER=admin
QBITTORRENT_PASSWORD=your-chosen-password
```

## 7. Run the automated setup

```
docker compose run --rm setup
```

This does **everything** in steps 8–13 below — download clients, root folders,
quality caps, seeder minimums, Prowlarr↔Sonarr/Radarr links, SABnzbd
categories and whitelist, Bazarr connections, the Jellyfin wizard, libraries,
NVENC, and a startup trigger on Jellyfin's library scan (see
[§13](#13-jellyfin--the-library)). It uses each app's own REST API (the same
calls their web UIs make), so there is no browser automation to break when a
UI changes.

**Safe to re-run at any time.** Every step checks current state first and skips
or corrects rather than duplicating. Nothing is ever deleted.

Two optional `.env` values it will use if present:

```
JELLYFIN_ADMIN_USER=yourname
JELLYFIN_ADMIN_PASSWORD=your-password
```

With those set it completes Jellyfin's first-run wizard and creates both
libraries automatically. Without them it skips Jellyfin and tells you so.

It finishes by listing what still needs you — the things requiring your own
accounts, which no script can supply:

- **Prowlarr indexers** (step 8) — which sites, plus your credentials
- **SABnzbd Usenet provider** (step 12) — your paid subscription details
- **Bazarr languages/providers** (step 14) — your preference

Everything else is done. Steps 9–11 and 13 below are kept as reference for
what the script configured, and for anyone who prefers doing it by hand.

## 8. Prowlarr — add your indexers

[http://localhost:9696](http://localhost:9696) → **Indexers → Add Indexer**.

This is the one step nobody can do for you: Prowlarr can only search sites you
have access to. Add your torrent trackers and/or Usenet indexers here. This is
the **only** place indexers get configured — Sonarr and Radarr inherit them.

Some public indexers sit behind Cloudflare bot protection and fail with
`blocked by CloudFlare Protection`. That is the site deliberately refusing
automated access. Use an indexer you have proper access to instead — a Usenet
indexer you subscribe to, or a tracker with a working API.

## 9. Prowlarr — push indexers to Sonarr and Radarr

Still in Prowlarr: **Settings → Apps → Add**, once for Sonarr and once for Radarr.

| Field                | Sonarr                  | Radarr                  |
| -------------------- | ----------------------- | ----------------------- |
| Prowlarr Server      | `http://gluetun:9696` | `http://gluetun:9696` |
| Sonarr/Radarr Server | `http://sonarr:8989`  | `http://radarr:7878`  |
| API Key              | *see below*           | *see below*           |

⚠ **Prowlarr Server must be `http://gluetun:9696`, not `localhost`.** This field
is the address *Sonarr/Radarr* use to call back to Prowlarr — and Sonarr/Radarr
live in a different network namespace than Prowlarr. From their side,
`localhost` means themselves, not Prowlarr, and the field fails validation with
`Invalid Url: 'http://localhost:9696'`. Since Prowlarr shares gluetun's
namespace, `gluetun` is the correct address — verified: `curl http://gluetun:9696`
from inside the Radarr container returns `200`, while `localhost:9696` from the
same container gets connection refused.

Find each API key in that app's **Settings → General**, or from the command line:

```
docker exec sonarr grep -o '<ApiKey>[^<]*</ApiKey>' /config/config.xml
docker exec radarr grep -o '<ApiKey>[^<]*</ApiKey>' /config/config.xml
```

⚠ Use the **container names** (`sonarr`, `radarr`), never `localhost`. Prowlarr
runs inside gluetun's network namespace, so `localhost` there is the VPN
container, not your PC.

Hit **Test**, then **Sync App Indexers**.

## 10. Sonarr and Radarr — add download clients

In **both** apps: **Settings → Download Clients → Add**.

**qBittorrent** — it lives behind the VPN, so address it as `gluetun`. Its
**Category** field (e.g. `tv-sonarr` in Sonarr, `radarr` in Radarr) does not
need to match anything — qBittorrent creates categories on the fly, and each
app's category only needs to be distinct so downloads from Sonarr and Radarr
stay distinguishable in one shared qBittorrent instance:

| Field               | Value                      |
| ------------------- | -------------------------- |
| Host                | `gluetun`                |
| Port                | `8090`                   |
| Username / Password | the ones you set in step 6 |

**SABnzbd** (skip if not using Usenet):

| Field    | Value                                 |
| -------- | ------------------------------------- |
| Host     | `sabnzbd`                           |
| Port     | `8080`                              |
| Category | `tv` (Sonarr) / `movies` (Radarr) |
| API Key  | from SABnzbd →*Config → General*  |

The API key exists from first boot, even before step 11's provider setup — so
this step can be done now, in any order relative to step 11.

⚠ **Unlike qBittorrent, SABnzbd will not create the category for you** — it
must already exist in SABnzbd, or the connection fails with `Category does not exist`. Create it first: SABnzbd → **Config → Categories → Add Category**,
name it exactly `tv` (or `movies` for Radarr), save. Do this before adding
SABnzbd as a download client, not after.

⚠ If the connection test instead fails with `401`/`Access denied`, SABnzbd's
`host_whitelist` doesn't yet include `sabnzbd` (the hostname Sonarr/Radarr
connect with) — only whatever host it was first opened from. Fix in SABnzbd:
**Config → General → Host whitelist**, add `sabnzbd`, save, restart the
container.

With both clients added, open **each one's own edit screen** (click its name
in the Download Clients list — this is not a separate global setting) and set
its **Client Priority** field: SABnzbd `1`, qBittorrent `2`, in both Sonarr and
Radarr. Lower number is tried first, so Usenet is attempted before torrents —
faster, and it doesn't depend on seeders. Don't confuse this field with
*Recent Priority* / *Older Priority* on the same screen, which control
something else (how eagerly to grab very new vs. older releases).

## 11. Sonarr and Radarr — set root folders

**Settings → Media Management → Root Folders**:

- Sonarr → `/data/library/tv`
- Radarr → `/data/library/movies`

Leave *Use Hardlinks instead of Copy* **on**. It is the entire reason for the
single-root layout.

## 12. SABnzbd — your Usenet provider

[http://localhost:8080](http://localhost:8080), run the wizard, enter your provider's server details.
Then **Config → Folders**:

- Temporary Download Folder: `/data/usenet/incomplete`
- Completed Download Folder: `/data/usenet/complete`

## 13. Jellyfin — the library

[http://localhost:8096](http://localhost:8096). Run the wizard and create your admin user — pick a real
password, there is no temporary-password fallback and resetting it means editing
the database.

Add **two** libraries:

| Content type | Folder            |
| ------------ | ----------------- |
| Movies       | `/media/movies` |
| Shows        | `/media/tv`     |

In each library's settings:

| Setting                         | Value         | Why                                                                     |
| ------------------------------- | ------------- | ----------------------------------------------------------------------- |
| Enable real time monitoring     | **on**  | New imports appear automatically                                        |
| Save artwork into media folders | **off** | `/media` is read-only — leaving it on logs write failures every scan |
| Save metadata as NFO            | **off** | Same reason                                                             |

At the **Remote access** step: leave *Allow remote connections* ticked, but
**untick "Enable automatic port mapping"** (that is UPnP —
see [Remote access](#remote-access-to-jellyfin)).

Then **Dashboard → Scheduled Tasks → Scan Media Library** — set it to run every
few hours as a backstop, and add a trigger to run it **on application startup**.
Real-time monitoring only sees writes made from inside a container, so a
host-side copy (e.g. from Windows Explorer) won't show up until the next scan
or restart.

If you ran the [automated setup](#7-run-the-automated-setup), both of these
are already done — it adds a startup trigger to the existing scan schedule and
leaves the interval trigger in place as the backstop.

If you have an NVIDIA GPU: **Dashboard → Playback → Transcoding** →
[enable NVENC](#hardware-transcoding).

## 14. Bazarr — subtitles

[http://localhost:6767](http://localhost:6767) → **Settings → Sonarr** and **Settings → Radarr**:

| Field   | Sonarr         | Radarr         |
| ------- | -------------- | -------------- |
| Address | `sonarr`     | `radarr`     |
| Port    | `8989`       | `7878`       |
| API Key | same as step 8 | same as step 8 |

Then **Settings → Languages** to choose your subtitle languages and create a
profile, and **Settings → Providers** to add subtitle sources (OpenSubtitles etc).

## 15. Let your phone reach Jellyfin

Docker publishes port 8096, but **Windows Firewall still blocks inbound
connections by default** — Docker Desktop does not create an exception for
WSL2-published ports. Without this rule, no device can connect.

In an **elevated** PowerShell (Run as Administrator):

```
New-NetFirewallRule -DisplayName "Jellyfin (8096)" -Direction Inbound -Protocol TCP -LocalPort 8096 -Action Allow -Profile Private
```

Find your LAN IP:

```
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -eq 'Dhcp' } | Select-Object IPAddress, InterfaceAlias
```

Then browse to `http://<that-ip>:8096` from your phone.

See [Jellyfin shows several servers](#jellyfin-shows-several-servers-and-none-work)
if the mobile app discovers multiple entries.

---

# USING IT

## Adding media — you do not add things one at a time

Radarr and Sonarr both support **Import Lists**: point them at a list, and they
automatically add *and download* everything on it, then keep syncing as the list
changes.

**Settings → Import Lists → Add.** Available sources include:

| Source                                         | Use case                                                      |
| ---------------------------------------------- | ------------------------------------------------------------- |
| **Trakt List / Trakt User**              | The best option. Keep a list on trakt.tv, add from your phone |
| **IMDb Lists**                           | Any public IMDb list URL                                      |
| **TMDb List / TMDb User**                | Same via TMDb                                                 |
| **TMDb Popular / Trakt Popular**         | "Always have the current top 10", fully automatic             |
| **Plex Watchlist**, Simkl, StevenLu, RSS | Other sources                                                 |

Set **Search on Add = yes** and it downloads immediately.

Your workflow becomes: *add a film to a Trakt list on your phone → it appears in
Jellyfin later*. For TV, Sonarr additionally monitors ongoing series and grabs
new episodes the night they air.

For one-offs, **Movies → Add New** (Radarr) or **Series → Add New** (Sonarr)
still works fine.

### Adding a request UI (optional)

**Jellyseerr** gives housemates/family a friendly "request a movie" page that
feeds straight into Radarr/Sonarr. Worth it only if other people will use the
server; skip for solo use.

## Everyday commands

```
docker compose up -d                       # start everything
docker compose down                        # stop everything
docker compose pull; docker compose up -d  # update all images
docker compose logs -f sonarr              # follow one service
```

⚠ **After touching gluetun alone, always restart its dependants:**

```
docker compose restart qbittorrent prowlarr
```

[Why this is mandatory](#why-gluetun-changes-need-a-qbittorrentprowlarr-restart).

## Optional extras

**Recyclarr** — syncs [TRaSH Guides](https://trash-guides.info/) quality
profiles into Sonarr/Radarr. Your quality profile (`HD-1080p` etc.) only
controls *resolution*; Recyclarr adds **custom formats** that score releases on
encode quality, audio, and release-group reputation — filtering out fakes and
bad encodes automatically. It's a scheduled sync tool, not a live-running
service — a container that wakes up on a cron schedule, syncs, and goes back to
sleep. Already included in `docker-compose.yml`, config at
`recyclarr-config/recyclarr.yml`.

Setup:

1. Put your Sonarr/Radarr API keys and container URLs (`http://sonarr:8989`,
   `http://radarr:7878`) into `recyclarr-config/recyclarr.yml`
2. Pick a quality profile from the guide and put its `trash_id` in the config.
   **Get the current, correct ID from Recyclarr itself** — the guide changes,
   and a stale ID from a blog post or an old guide page will fail:

   ```
   docker exec -t recyclarr recyclarr list quality-profiles sonarr
   docker exec -t recyclarr recyclarr list quality-profiles radarr
   ```

   (`-t` is required — without a TTY these commands print nothing.)
3. Check it resolves before touching anything live:

   ```
   docker exec recyclarr recyclarr sync sonarr --preview
   ```
4. Then sync for real:

   ```
   docker exec recyclarr recyclarr sync
   ```

Verify it actually landed — a clean exit code alone doesn't confirm the sync
did anything:

```
curl -s http://localhost:8989/api/v3/qualityprofile -H "X-Api-Key: <sonarr key>"
```

Your new profile should appear in that list, and `/api/v3/customformat` should
return dozens of entries instead of zero. After the first sync, it repeats
automatically on `CRON_SCHEDULE` (default: daily at 03:00, set in
`docker-compose.yml`) — no need to run it by hand again.

**Jellyseerr** — a request UI so family/housemates can ask for a film and have
it feed straight into Radarr/Sonarr, without giving them admin access. Worth it
only if other people use the server. A ready-to-use (commented-out) service
block already exists at the bottom of `docker-compose.yml` — uncomment it and
`docker compose up -d` to add it.

## Download quality and speed controls

Three separate mechanisms, each catching a different failure mode. They run
independently but stack together — a release has to clear all three to end up
watchable in reasonable time. All three are optional; the stack works without
them, just with less protection against bad picks.

```
Sonarr/Radarr searches indexers
        │
        ▼
①  Seeder minimum (Prowlarr/indexer setting)
    rejects weakly-seeded torrents BEFORE grab
        │  survives
        ▼
②  Quality Definition size cap (Sonarr/Radarr setting)
    rejects oversized releases (e.g. remuxes) BEFORE grab
        │  survives — release is grabbed, download starts
        ▼
③  speed-monitor (custom container, this repo only)
    watches the LIVE download after grab; if it stalls,
    un-grabs it and triggers a new search
```

### ① Seeder minimum — filters before grab

Each indexer has a **Minimum Seeders** field (Sonarr/Radarr → *Settings →
Indexers → click an indexer*). Default is `1`, which is barely a filter — a
1-seeder torrent almost always crawls. Currently set to **`5`** across all
torrent indexers in both apps. Low seeders is the single strongest predictor of
a slow torrent available *before* anything downloads, which is why it's the
first line of defense.

Doesn't apply to Usenet (SABnzbd) — there's no seeder concept there.

### ② Quality Definition size cap — filters before grab

**Settings → Quality → [tier]** (e.g. `Bluray-2160p Remux`) has a **Max Size**
field, expressed as **MB per minute of runtime**, not a flat file size —
Sonarr/Radarr compute the actual GB limit per episode/movie using each item's
real runtime.

Currently set on Sonarr to **252 MB/min** (≈15 GB for a 61-minute episode)
across every 2160p tier — `HDTV-2160p`, `WEBRip-2160p`, `WEBDL-2160p`,
`Bluray-2160p`, `Bluray-2160p Remux`. This is a **global** setting per app, not
per-profile — it affects every quality profile in Sonarr, not just one show.
Radarr has its own separate Quality Definitions if you want the same treatment
for movies (2-hour films need a different MB/min value than 61-minute
episodes — recompute rather than reuse Sonarr's number).

Worth knowing: `Bluray-2160p Remux` has a **floor** of 187.4 MB/min (its
minimum possible size) — with a 252 MB/min ceiling, only a narrow window
survives, and for episodes over ~68 minutes even the floor exceeds the cap. In
practice this means remux releases are excluded almost entirely, which is the
intended effect: remuxes are minimally compressed and were the direct cause of
a 262 GB/season release before this cap existed.

To change the number: recompute for your episode length —
`(desired_GB × 1024) / minutes = MB/min`.

### ③ speed-monitor — watches live, after grab

⚠ **Unlike everything else in this repo, this is custom-built for this
specific setup — not an off-the-shelf project.** It's the one piece most
likely to need a tweak or a fix later. Read `speed-monitor/monitor.py` before
trusting it blindly.

**What it solves:** ① and ② only evaluate a release *before* it downloads.
Neither catches a release that looked fine on paper (decent seeders, sane
size) but turns out to crawl in practice — a throttled source, a swarm that
evaporates, etc. speed-monitor is a small container that polls qBittorrent's
live API and reacts to what's actually happening, not what was predicted.

#### A real incident, and why the design looks the way it does

The first version of this script **deleted** a slow download outright and
blocklisted it on every retry, with no memory of having tried before. Combined
with raising `max_active_downloads` (more concurrent downloads = less
bandwidth each, more of them individually look "slow" at once), it thrashed
for about 5 hours: every Season 4 episode of a real show got killed and
re-grabbed **twice**, 22 perfectly viable releases ended up wrongly
blocklisted, and nothing ever finished. Recovery meant clearing the blocklist
and starting over.

The current design exists specifically to make that failure mode structurally
impossible, not just less likely:

1. Poll qBittorrent every `POLL_INTERVAL_SECONDS` (default 60s) for active downloads
2. Ignore anything younger than `GRACE_PERIOD_SECONDS` (default 10 min) — early
   peer discovery is normally slow and shouldn't be punished
3. Ignore anything already past `PROGRESS_GUARD` (default 20%) — a download
   that's made real progress is never touched for being slow, no matter how
   long. Restarting a mostly-finished download to chase an unknown
   replacement is a bad trade
4. If a download is **continuously** below `SPEED_THRESHOLD_BPS` (default
   0.5 MiB/s) for `SUSTAINED_SECONDS` (default 15 min), it is **stopped** —
   `POST /api/v2/torrents/stop` — never deleted, never blocklisted. Recovering
   above the threshold even briefly resets the clock; only a genuinely
   sustained stall counts
5. A **different** release for the same episode/movie is fetched fresh via
   `GET /api/v3/release` and grabbed via `POST /api/v3/release` — the same
   two-step flow Sonarr/Radarr's own "Interactive Search" UI uses internally.
   Already-tried release titles and anything Sonarr/Radarr already rejected
   are skipped
6. After `MAX_RETRIES` stopped attempts (default 2) on the *same* episode or
   movie, whichever stopped candidate got the **furthest progress** is
   resumed — `POST /api/v2/torrents/start` — and treated as the final answer.
   The others are left stopped, exactly as they were, for you to clean up
   manually if you want the disk space back. This episode is then never
   auto-managed again, even if the resumed one is later slow too

qBittorrent 5.x renamed pause/resume to stop/start **at the API level, not
just in the UI** — `/api/v2/torrents/pause` is a 404 on this version.
Confirmed directly with curl before writing this, since guessing the older
name once already cost real debugging time.

**Nothing is ever deleted automatically.** The worst case is a few stopped,
harmless torrents sitting idle in qBittorrent until you clear them by hand —
not lost progress, not a wrongful blocklist.

**Deliberately not covered:** Usenet (SABnzbd). There's no seeder-style signal
and no alternate release to fall back to the way torrents have — a slow
Usenet download is just your provider's connection, not a bad pick.

**Manually stopping a download yourself is always safe.** speed-monitor only
evaluates torrents in an active state (`downloading`, `stalledDL`, `metaDL`,
`forcedDL`). The moment you stop one yourself, its state becomes `stoppedDL` —
outside that set — so it's skipped entirely on the next poll, with or without
this redesign.

**Config** (`docker-compose.yml`, `speed-monitor` service environment):

| Variable                  | Default                | Meaning                                                                    |
| ------------------------- | ---------------------- | -------------------------------------------------------------------------- |
| `SPEED_THRESHOLD_BPS`   | `524288` (0.5 MiB/s) | Below this = "slow"                                                        |
| `SUSTAINED_SECONDS`     | `900` (15 min)       | How long "slow" must persist before acting                                 |
| `GRACE_PERIOD_SECONDS`  | `600` (10 min)       | Downloads younger than this are never evaluated                            |
| `PROGRESS_GUARD`        | `0.20` (20%)         | Downloads past this progress are never touched                             |
| `MAX_RETRIES`           | `2`                  | Stopped attempts allowed before picking the best and giving up on the rest |
| `POLL_INTERVAL_SECONDS` | `60`                 | How often it checks                                                        |

**Watch it work:**

```
docker logs -f speed-monitor
```

Every active download prints its current speed, progress, and how long it's
been slow, so you can see a threshold breach — and the resulting alternative
grab, or the final decision — coming before it happens.

**State persists** to `speed-monitor/state/retry_state.json`, so a container
restart doesn't forget which episodes have already been retried and silently
allow re-litigating them from scratch.

**Credentials:** needs its own qBittorrent WebUI login (`QBITTORRENT_USER` /
`QBITTORRENT_PASSWORD` in `.env`) plus Sonarr/Radarr API keys
(`SONARR_API_KEY` / `RADARR_API_KEY` in `.env`) — the app-level API keys
already used elsewhere don't grant qBittorrent access, and vice versa.

## Live app settings not stored in this repo

Everything above this point lives in `docker-compose.yml`, `.env`, or a file
under version control — clone this repo elsewhere, `docker compose up -d`,
and it comes back automatically. **The settings below do not.** They live
inside each app's own database, in a named Docker volume
(`sonarr-config`, `qbittorrent-config`, etc.). That means they:

- ✅ survive a container restart, `docker compose down` / `up`, even Docker
  Desktop itself going down and back up
- ❌ do **not** survive `docker compose down -v`, a deleted volume, or a fresh
  clone on a different machine — nothing in git recreates them

If you ever rebuild from scratch, or the numbers below look wrong and you're
not sure why, this is the checklist to redo.

**qBittorrent** (Options → BitTorrent, unless noted):

| Setting                  | Value                                       | Why                                                                                                                                                                                                    |
| ------------------------ | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Maximum active downloads | `4`                                       | Past this, qBittorrent's global 500-connection cap gets divided so thin per-torrent that already-weak swarms get starved further — see[What the VPN costs you](#what-the-vpn-costs-you) era discussion |
| Ratio limit              | `1.0`, action **Stop** (not remove) | Stops seeding automatically; "Stop" preserves the file, only "Remove" would delete it                                                                                                                  |
| Seeding time limit       | `180` minutes                             | Backstop in case ratio is never reached                                                                                                                                                                |

**Sonarr → Settings → Quality → Quality Definitions** — max size, in **MB per
minute of runtime** (not a flat GB figure):

| Quality                                                                 | Max size                                                                                                  |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| HDTV-2160p, WEBRip-2160p, WEBDL-2160p, Bluray-2160p, Bluray-2160p Remux | `252` (≈15 GB for a 61-minute episode — recompute for different runtimes: `(GB × 1024) / minutes`) |

⚠ **Radarr's equivalent Quality Definitions were never actually set** — movies
currently have no size cap. If you want the same protection there, set it
yourself using Radarr's own runtime math (movies run ~2 hours, so the same
252 MB/min would allow ~30 GB — decide what's actually reasonable for film
remuxes before copying the TV number).

**Prowlarr → Settings → Apps** — for both the Sonarr and Radarr entries:

| Field      | Value                                                 | Why                                                                                                                                                                                                                                                                                                                                                                        |
| ---------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sync Level | **`Add Only`**, not the default `Full Sync` | `Full Sync` periodically pushes Prowlarr's indexer list down to Sonarr/Radarr and **overwrites** app-specific fields Prowlarr doesn't track — confirmed directly: this silently reset the seeder minimum below back to `1` without any error or warning. `Add Only` still lets Prowlarr push new indexers, it just stops clobbering settings on existing ones |

**Sonarr AND Radarr → Settings → Indexers**, each torrent indexer:

| Field           | Value |
| --------------- | ----- |
| Minimum Seeders | `5` |

**SABnzbd → Config**:

| Setting                            | Value                                                                                            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------ |
| Categories (Config → Categories)  | `tv`, `movies` — required, Sonarr/Radarr fail with `Category does not exist` without them |
| Host whitelist (Config → General) | must include`sabnzbd` — Sonarr/Radarr connect using that hostname, not `localhost`          |

---

# REFERENCE

## Why everything shares one mount (hardlinks)

Sonarr and Radarr "import" by creating a **hardlink** — a second directory entry
pointing at the same bytes on disk. It is instant, uses no extra space, and
leaves the original where it is so qBittorrent can keep seeding.

Hardlinks only work **within a single filesystem mount**. Verified on this setup:

```
ln /data/torrents/complete/file.mkv /data/library/movies/file.mkv
-> links=2  inode=2251799814069397   (same inode = one copy of the data)
```

Mount downloads and library separately and it breaks:

```
ln: failed to create hard link ... : Cross-device link
```

That is `EXDEV` — Linux refuses `rename(2)` and hardlinks across mount points,
**even when both are on the same physical drive**. The apps then fall back to
copying: every import takes minutes, needs double the disk, and you must choose
between seeding and having a library.

The identical `/data` path in every container matters too. Sonarr tells
qBittorrent "the download is at `/data/torrents/complete/x`" — if qBittorrent
knew that folder by a different path, Sonarr would never find the finished file.

## Security: what is exposed to whom

| Service                         | Bound to                    | Reachable from  | Auth               |
| ------------------------------- | --------------------------- | --------------- | ------------------ |
| Jellyfin                        | `0.0.0.0:8096`            | LAN (+ tailnet) | Password           |
| Sonarr, Radarr, Bazarr, SABnzbd | `127.0.0.1`               | This PC only    | Password / API key |
| qBittorrent, Prowlarr           | `127.0.0.1` (via gluetun) | This PC only    | Password / API key |
| gluetun control API             | `127.0.0.1:8010`          | This PC only    | API key            |

A Docker `ports:` entry without an address prefix binds to `0.0.0.0` — every
interface, so every device on your network. Prefixing with `127.0.0.1:` limits
it to the host. That is the cheapest hardening available and costs nothing for
services you only use locally.

### How `gluetun-auth.toml` works

gluetun reads it **once at startup** from `/gluetun/auth/config.toml`. Each
`[[roles]]` block is an allow-rule:

```toml
[[roles]]
name   = "healthcheck"                                  # label, for logs only
routes = ["GET /v1/publicip/ip", "GET /v1/vpn/status"]  # "METHOD /path" pairs
auth   = "apikey"                                       # none | basic | apikey
apikey = "..."                                          # sent as X-API-Key header
```

Three things worth knowing:

1. **Supplying this file replaces gluetun's built-in defaults entirely.** Any
   route not named in a role is denied. Without it, `GET /v1/vpn/status` and
   `GET /v1/publicip/ip` answer *anyone on your LAN* with no credential — and
   the API is not read-only: `PUT /v1/vpn/status` can **stop your VPN**.
2. **Roles are additive.** Splitting "healthcheck" and "toggle" into two blocks
   is not required — it documents intent and lets you revoke the VPN-stopping
   capability by deleting one block.
3. **Changes need a recreate, not a restart:**

   ```
   docker compose up -d --force-recreate gluetun
   docker compose up -d --force-recreate qbittorrent prowlarr
   ```

### Why the key cannot live in `.env`

gluetun does **not** support environment-variable interpolation inside
`config.toml`. There is one alternative,
`HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE`, which takes a JSON role as an env var —
but it applies to **every route not covered by a config file**, handing the same
key access to the routes that stop your VPN and read your settings. That trades
away least-privilege for tidiness.

The pattern that actually solves "I want to commit this" is the one `.env`
already uses: commit a template, ignore the real file.

| Committed                     | Ignored               |
| ----------------------------- | --------------------- |
| `.env.example`              | `.env`              |
| `gluetun-auth.toml.example` | `gluetun-auth.toml` |

> Before your first `git commit`, run `git status` and confirm neither `.env`
> nor `gluetun-auth.toml` appears. Once a secret is in history, deleting it in a
> later commit does not remove it.

## Turning the VPN on and off

Three different things people mean by this.

### 1. Pause / resume the tunnel — `scripts\vpn-toggle.ps1` / `scripts/vpn-toggle.sh`

```powershell
.\scripts\vpn-toggle.ps1            # toggle
.\scripts\vpn-toggle.ps1 status     # show state + exit IP
.\scripts\vpn-toggle.ps1 stop
.\scripts\vpn-toggle.ps1 start      # reconnects, then verifies your real IP is not exposed
```

```bash
./scripts/vpn-toggle.sh             # toggle
./scripts/vpn-toggle.sh status
./scripts/vpn-toggle.sh stop
./scripts/vpn-toggle.sh start
```

**This is a pause button, not a bypass.** Stopping the tunnel does *not* give
qBittorrent a direct connection — gluetun's firewall stays up, so it loses all
connectivity. Verified:

```
PUT /v1/vpn/status {"status":"stopped"}  ->  {"outcome":"stopped"}
curl from inside qbittorrent             ->  no response at all
```

Downloads go to 0 B/s, not to full speed. Use it to stop torrent traffic
immediately, or to force a reconnect to a different server.

It does **not** need a container restart afterwards — it pauses the tunnel
inside the running container, so the network namespace is never destroyed.

### 2. Change exit country — a setting

In `.env`:

```
VPN_COUNTRIES=Netherlands
```

Then `docker compose up -d && docker compose restart qbittorrent prowlarr`.
See available countries with:

```
docker run --rm qmcgaw/gluetun:v3 format-servers -privado
```

gluetun picks a random server within the country on each connect, which is why
the exit IP changes between reconnects.

### 3. Running the whole stack with no VPN

Full setup: [ONE-TIME-SETUP.md](ONE-TIME-SETUP.md#optional-running-the-whole-stack-without-a-vpn).
`docker-compose.novpn.yml` is an **alternative** to the protected stack, not a
second instance alongside it — same project, same container names, same
ports, same volumes, minus gluetun:

```
docker compose down                                   # stop the protected stack
docker compose -f docker-compose.novpn.yml up -d      # start the no-VPN one
```

⚠ **qBittorrent and Prowlarr both connect directly to the internet in this
mode** — every peer in a torrent swarm sees your real IP, and every indexer
search reveals which sites you query to your ISP. There is no kill switch;
that is the point of the file. Switch back the moment you're done:

```
docker compose -f docker-compose.novpn.yml down
docker compose up -d
```

Because container names and ports are identical to the protected stack, both
files can never run at once — starting one while the other's containers exist
fails outright on a naming conflict, rather than silently running both.

For raw throughput you do not need it — `scripts\speedtest.ps1` / `scripts/speedtest.sh`
measures that without touching a swarm.

## What the VPN costs you

```powershell
.\scripts\speedtest.ps1                 # 25 MB per path
.\scripts\speedtest.ps1 -SizeMB 100     # firmer numbers, more data used
```

```bash
./scripts/speedtest.sh                  # 25 MB per path
./scripts/speedtest.sh 100               # firmer numbers, more data used
```

Example measurement (50 MB × 2, best of each) on a 700 Mbps line, through
Privado over OpenVPN — your numbers depend on your provider and connection:

| Path                          | Speed                |
| ----------------------------- | -------------------- |
| Direct (no VPN)               | ~81 MB/s — 649 Mbps |
| Through the VPN (OpenVPN/UDP) | ~21 MB/s — 167 Mbps |
| Tunnel overhead               | ~74%                 |

That overhead is normal for **OpenVPN**: single-threaded, encryption in
userspace. **WireGuard loses far less** (often single-digit percent) — if your
provider supports it, switching `VPN_TYPE` to `wireguard` in `.env` (see
[Using a different VPN provider](#using-a-different-vpn-provider)) is the real
fix, not a setting to tune within OpenVPN.

**It rarely matters either way.** Torrent speed is governed by seeder count and
the absence of port forwarding far more than by this ceiling. A torrent with 1
seeder is slow everywhere — check the Seeds column before blaming the tunnel.

> Only the tunnelled runs consume your VPN data allowance, if your provider caps
> one (Privado's free tier is 10 GB/month). A `-SizeMB 100 -Runs 2` test spends
> 200 MB of it.

## Remote access to Jellyfin

### The "allow remote connections" checkbox is not enough

`EnableRemoteAccess` only tells Jellyfin *"accept clients from outside my local
subnet."* It does **not** open a router port, give you a public address, or add
encryption.

The setting that *would* punch a hole is **"Enable automatic port mapping"**
(UPnP), which is why setup step 12 unticks it. Checkbox + UPnP is how people
accidentally publish a plaintext-HTTP Jellyfin login to the open internet.
Jellyfin has had authentication CVEs; this is not a thing to leave exposed.

The checkbox **is** required for Tailscale, though — tailnet clients get
`100.64.0.0/10` addresses, which Jellyfin treats as non-local and would reject.

### Tailscale — the recommended way

An encrypted private mesh between your own devices. **No router ports opened,
nothing exposed to the public internet.**

1. Create a free account at [https://tailscale.com](https://tailscale.com)
2. Install the client on this PC and sign in
3. Install it on every device you want to watch from — same account
4. Find this machine's address: `tailscale ip -4` (a `100.x.y.z`)
5. Browse to `http://100.x.y.z:8096`

For Jellyfin to accept those clients, add `100.64.0.0/10` to
**Dashboard → Networking → LAN Networks**.

With **MagicDNS** on you can use the machine name instead of the IP.

### Free HTTPS with a real certificate

`tailscale serve` fronts Jellyfin with a browser-trusted Let's Encrypt
certificate on a `*.ts.net` hostname — no domain purchase, no port forwarding,
no certificate warnings:

```
tailscale serve --bg 8096
```

> Do **not** use `tailscale funnel` unless you mean it — that publishes the
> service to the entire internet, which is exactly what this avoids.

## Hardware transcoding

Passing the GPU into the container and *using* it are two different things. The
compose file does the first; you must do the second.

**Dashboard → Playback → Transcoding:**

- Hardware acceleration: **NVIDIA NVENC**
- Tick **H264**, **HEVC**, **HEVC 10bit**, **VP9**
- Tick **Enable hardware decoding** and **Enable hardware encoding**
- Tick **Enable Tone mapping** — HDR/DV content looks washed out on SDR screens
  without it

Verify the GPU is actually visible to the container:

```
docker exec jellyfin nvidia-smi --query-gpu=name --format=csv,noheader
```

No NVIDIA GPU? Delete the `deploy:` block from the `jellyfin` service or the
container will not start.

## Running on Linux

Two changes:

1. In `.env`, set `MEDIA_ROOT=/srv/media` (or wherever), using a normal path.
2. Set `PUID`/`PGID` in `docker-compose.yml` to your user — `id -u` and `id -g`.
   On Windows these are ignored because drvfs has no real ownership; on Linux
   they decide whether the apps can write to your media folders at all.

Everything else works the same way, including every helper script — each
`.ps1` has a `.sh` twin with identical behavior (`scripts/check-vpn.sh`,
`scripts/vpn-toggle.sh`, `scripts/speedtest.sh`). The Windows Firewall step (step 15,
[Let your phone reach Jellyfin](#15-let-your-phone-reach-jellyfin)) does not
apply; use `ufw allow 8096/tcp` or your distro's equivalent instead.

## Using a different VPN provider

This repo ships pre-configured for Privado, but **gluetun itself is
provider-agnostic** — around 40 providers are supported, and switching is
purely an `.env` edit. Nothing in `docker-compose.yml`, nor any other
container, needs to change or even knows which VPN you're using.

Full provider list, with the exact variables each one needs:
[gluetun-wiki/setup/providers](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers).

### The two connection types

Every provider uses one of these. `.env.example` has a ready-to-fill template
for both.

**OpenVPN** (username + password) — what Privado uses, and the simpler of the
two to set up. Most providers support it.

```ini
VPN_SERVICE_PROVIDER=protonvpn      # your provider's gluetun name, lowercase
VPN_TYPE=openvpn
OPENVPN_USER=your-openvpn-username  # NOT your website login -- see below
OPENVPN_PASSWORD=your-openvpn-password
```

**WireGuard** (key-based, no username/password) — meaningfully faster, since
OpenVPN's encryption is single-threaded and WireGuard's isn't (see
[What the VPN costs you](#what-the-vpn-costs-you)). Get the values from your
provider's WireGuard config generator, usually a downloadable file.

```ini
VPN_SERVICE_PROVIDER=protonvpn
VPN_TYPE=wireguard
WIREGUARD_PRIVATE_KEY=your-private-key
WIREGUARD_ADDRESSES=10.x.x.x/32
```

⚠ Whichever type you use, the credentials are almost never your provider's
**website login**. Nearly every provider issues a *separate* OpenVPN/WireGuard
credential on a "manual setup" page in your account dashboard. Using your
website email as `OPENVPN_USER` is the single most common cause of
`AUTH_FAILED` — this isn't a Privado quirk, it trips people up on every
provider.

### Worth knowing when choosing a provider

**Port forwarding matters more than raw speed.** Privado has none, on any
plan — downloads work fine, but no peer can connect *to* you, so seeding is
weak and poorly-seeded torrents stay slow regardless of your connection speed
(see [Adding media](#adding-media-you-do-not-add-things-one-at-a-time) — this
is why per-episode releases often beat season packs). Providers with port
forwarding (Proton, AirVPN, PIA) fix that directly.

**WireGuard support** — most providers with port forwarding also support
WireGuard, and the two tend to go together. If both matter to you, that
narrows the field fast.

---

# TROUBLESHOOTING

## The stack

| Symptom                                             | Fix                                                                                                                                 |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| gluetun`AUTH_FAILED`                              | Use the OpenVPN username from your provider's admin panel,**not** your email. Then try `OPENVPN_PROTOCOL=tcp`               |
| qBittorrent/Prowlarr never start                    | gluetun is not healthy —`docker logs gluetun`                                                                                    |
| WebUI dead but containers show`Up`                | You restarted gluetun alone. Run`docker compose restart qbittorrent prowlarr`                                                     |
| `joining network namespace ... No such container` | After`--force-recreate` on gluetun, `restart` is not enough. Use `docker compose up -d --force-recreate qbittorrent prowlarr` |
| Compose says a variable is not set                  | `.env` is missing or lacks `MEDIA_ROOT`. Copy `.env.example`                                                                  |
| A service can't write to`/data`                   | On Linux,`PUID`/`PGID` don't match the folder owner                                                                             |

## Downloads

| Symptom                             | Fix                                                                                                                                                                           |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Torrent stuck at "stalled"          | Check the**Seeds** column. 1 seeder is slow everywhere — not a VPN problem                                                                                             |
| Torrent errors after moving folders | Right-click →**Set Location** → the new path, then **Force Recheck**. Set Location alone does not re-verify                                                     |
| "Permission denied" on every file   | Save path points somewhere not mounted. It must be under`/data`                                                                                                             |
| Can't log in to qBittorrent         | The temp password regenerates on**every restart**: `docker logs qbittorrent \| Select-String "temporary password" \| Select-Object -Last 1` — then set a permanent one |
| Slow torrents generally             | Expected without port forwarding; prefer well-seeded releases                                                                                                                 |
| Ran out of VPN data                 | Only if your provider caps one — Privado's free tier is 10 GB/month                                                                                                          |

## The *arr apps

| Symptom                                                          | Fix                                                                                               |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Prowlarr can't reach Sonarr/Radarr                               | Use container names`sonarr`/`radarr`, not `localhost` — Prowlarr is in gluetun's namespace |
| Sonarr/Radarr can't reach qBittorrent                            | Host is`gluetun`, port `8090` — not `localhost`                                            |
| `ProwlarrUrl: Invalid Url` when adding Sonarr/Radarr as an app | Prowlarr Server field must be`http://gluetun:9696`, not `localhost` — see step 8             |
| `blocked by CloudFlare Protection` on an indexer               | The site is refusing automated access. Use an indexer you have proper access to instead           |
| `Category does not exist` adding SABnzbd as a download client  | The category must exist in SABnzbd first — see step 9                                            |
| SABnzbd download client fails with`401`/`Access denied`      | Add`sabnzbd` to SABnzbd's Host whitelist — see step 9                                          |
| Imports are slow / disk fills up                                 | Hardlinks are failing. Confirm downloads and library are under the same`MEDIA_ROOT`             |
| Nothing is found for anything                                    | No indexers configured, or they didn't sync. Prowlarr →**Sync App Indexers**               |

## Jellyfin

| Symptom                                       | Fix                                                                                                                                                           |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Shows several servers and none work** | See[below](#jellyfin-shows-several-servers-and-none-work)                                                                                                      |
| Library is empty                              | Files only appear after Sonarr/Radarr*import* them into `/data/library`. Raw downloads are not in the library                                             |
| Nothing appears after a download              | **Dashboard → Scheduled Tasks → Scan Media Library**. If that finds it, real-time monitoring missed the event                                         |
| Write/permission errors on scan               | Untick*Save artwork into media folders* and *Save metadata as NFO* — `/media` is read-only by design                                                   |
| Container won't start                         | Almost always the GPU`deploy:` block on a machine without NVIDIA                                                                                            |
| Can't identify a movie                        | Rename the folder to`Movie Name (Year)`, or use *Identify*                                                                                                |
| 4K HDR looks washed out                       | Tick*Enable Tone mapping* under Dashboard → Playback                                                                                                       |
| Playback stutters on 4K                       | Enable NVENC, or use a client that direct-plays HEVC                                                                                                          |
| Forgot the admin password                     | No fallback — recovery means editing the database, or`docker volume rm downloader_jellyfin-config` and redoing the wizard (loses watch history, not media) |

### Jellyfin shows several servers and none work

Two separate causes, usually together.

**1. No firewall rule.** Docker publishes 8096, but Windows blocks inbound
connections unless something allows them, and Docker Desktop does not create the
exception. This blocks *every* address. Fix — elevated PowerShell:

```
New-NetFirewallRule -DisplayName "Jellyfin (8096)" -Direction Inbound -Protocol TCP -LocalPort 8096 -Action Allow -Profile Private
```

**2. Virtual adapters.** Jellyfin's auto-discovery answers on every network
interface, and a Docker host has many. Only addresses on your real LAN work:

| Example address    | What it is                       | Usable? |
| ------------------ | -------------------------------- | ------- |
| `192.168.x.x`    | Real LAN (WiFi or Ethernet)      | ✅      |
| `172.x.x.x`      | WSL / Docker internal            | ❌      |
| `169.254.x.x`    | Link-local (no DHCP)             | ❌      |
| `100.x.x.x`      | Tailscale — works, if installed | ✅      |
| Other VPN adapters | Different virtual network        | ❌      |

A PC on both WiFi *and* Ethernet has two valid LAN IPs; either works. List the
real ones:

```
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -eq 'Dhcp' } | Select-Object IPAddress, InterfaceAlias
```

**Also check:** if your phone is on a **Guest** WiFi network, it cannot reach
the main LAN at all regardless of firewall rules — guest networks are
deliberately isolated.

## Deep dives

### Why gluetun changes need a qBittorrent/Prowlarr restart

```
docker restart gluetun                        # <- never on its own
docker compose restart qbittorrent prowlarr   # <- always follow with this
```

qBittorrent and Prowlarr have no network stack of their own.
`network_mode: service:gluetun` joins them to gluetun's namespace — same
interfaces, same IP, same routing table. That is what makes the kill switch
airtight, and it is also the catch.

A namespace belongs to the process that created it. When gluetun stops, Docker
tears its namespace down; when it starts again it creates a **brand new** one.
The dependent containers still point at the old, dead namespace and nothing
re-attaches them. They look `Up` in `docker ps`, but nothing works.

`depends_on` only controls *startup* ordering — it does nothing on a restart of
an already-running stack.

Rules of thumb:

- Whole-stack `up -d` / `down` / `restart` — fine, handled in order
- Touching **gluetun alone** — always restart its dependants
- After `--force-recreate` on gluetun, `restart` **fails outright** with
  `No such container`; use `up -d --force-recreate qbittorrent prowlarr`
- `scripts\vpn-toggle.ps1` needs none of this

### Changing ports

For services in gluetun's namespace, edit **two** places and keep them in sync:

| Port                | Publish on`gluetun`   | Matching setting                                           |
| ------------------- | ----------------------- | ---------------------------------------------------------- |
| qBittorrent WebUI   | `127.0.0.1:8090:8090` | `WEBUI_PORT=8090` on **qbittorrent**               |
| gluetun control API | `127.0.0.1:8010:8010` | `HTTP_CONTROL_SERVER_ADDRESS=:8010` on **gluetun** |
| Prowlarr            | `127.0.0.1:9696:9696` | Prowlarr's own setting                                     |

The `ports:` entry always goes on **gluetun**, never on the dependent service —
Docker hard-errors otherwise. And `WEBUI_PORT` is passed as a `--webui-port`
flag, overriding `qBittorrent.conf`, so do not also change it in the WebUI.

Sonarr, Radarr, Bazarr, SABnzbd and Jellyfin have their own namespaces — change
their `ports:` directly, nothing to sync.

The defaults 8080 and 8000 were deliberately vacated: both are heavily contested
on a dev machine (8080: Tomcat, webpack, Spring Boot; 8000: Django, uvicorn,
`python -m http.server`). SABnzbd took 8080 since qBittorrent had moved off it.

### Renaming or moving this folder

Safe — because `docker-compose.yml` starts with:

```yaml
name: downloader
```

Without that, Compose derives the project name from the **folder name**, and the
project name prefixes every named volume. Rename the folder and `up -d` looks
for a volume that does not exist, **creates an empty one, and starts a blank
app** — settings and history gone, no error. The fixed `container_name:` values
turn it into a loud name-conflict error instead, which is the only reason it
fails visibly.

To migrate volumes between project names, copy — never move — so the originals
remain as a rollback:

```
docker volume create newproject_sonarr-config
docker run --rm -v oldproject_sonarr-config:/from -v newproject_sonarr-config:/to alpine sh -c "cd /from && cp -a . /to/"
```

### Does Docker protect me from viruses?

Partly, and the protection is narrower than it looks.

**What it does protect:** if a malicious torrent or peer exploited a bug *in
qBittorrent itself*, the attacker would land inside the container as an
unprivileged user with the VPN as their only network path. They would see
`/config` and `/data`, not the rest of the PC. A real gain over running the
client natively — but an uncommon attack.

**What it does not protect, which is the actual risk:** the container never
opens your files. You do, in Windows, after they land in your library. A
downloaded `.exe` or macro-laden document is exactly as dangerous as it would
have been without Docker. The container is a box around the *downloader*, not
around the *downloads*. It is process isolation, not a security sandbox.

**The VPN is orthogonal** — it hides what you download from your ISP, and does
nothing about what is inside the file.

What actually reduces risk:

- **Executables are the danger**: `.exe`, `.msi`, `.scr`, `.bat`, `.lnk`,
  `.iso`/`.img`. Media files are far lower risk. A movie release containing a
  "player" or "codec installer" is malware
- **Password-protected archives are a red flag** — the password exists so
  scanners cannot look inside
- **Watch for double extensions** (`movie.mp4.exe`). Enable Explorer →
  View → Show → *File name extensions*
- **Read release comments** — fake uploads usually get called out
- **Let it sit, then rescan.** Antivirus signatures update daily; a scan a day
  later catches what a same-day scan missed
- **Do not exclude your media folder from antivirus**
- **Leave "Run external program on completion" empty** in qBittorrent

For genuinely isolated opening of something questionable, Windows Sandbox or a
VM is the right tool. Docker here is not it.
