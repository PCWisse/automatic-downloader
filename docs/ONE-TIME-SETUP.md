# One-Time Setup

Follow this once, top to bottom. **~20 minutes**, most of it waiting for
containers to start.

This is the *do this* guide. It deliberately does not explain why — for that,
read [README.md](../README.md), which documents every decision in the stack.

**What you get at the end:** you add a movie or show to a list, and it gets
found, downloaded over a VPN, renamed, filed, given subtitles, and shows up in
Jellyfin ready to watch. No manual steps per download.

> **Installing on Proxmox?** Follow [PROXMOX.md](PROXMOX.md)
> instead — it covers the host install, the LXC, and AMD/Intel GPU
> transcoding, then hands back to this guide for the stack itself.

> **Don't want a VPN at all?** Most of steps 1–6 below are VPN setup —
> credentials, an API key, verifying the tunnel. None of that applies to you.
> Skip straight to [Running the whole stack without a
> VPN](#optional-running-the-whole-stack-without-a-vpn) instead of following
> the numbered steps in order.

---

## Before you start

Get these ready first — the setup stops dead without them.

| You need | Notes |
| --- | --- |
| **Docker Desktop** (Windows/Mac) or **Docker + Compose v2** (Linux) | Must be running before step 1 |
| **A VPN subscription** | Not needed if you're going the [no-VPN route](#optional-running-the-whole-stack-without-a-vpn) instead. Otherwise: ships configured for [PrivadoVPN](https://privadovpn.com); free tier works (10 GB/month). Other providers need a small edit — see [Using a different VPN provider](../README.md#using-a-different-vpn-provider) |
| **Disk space on ONE drive** | 4K TV seasons run 50–100 GB. Downloads *and* library must share one drive — this is mandatory, see [why](../README.md#why-everything-shares-one-mount-hardlinks) |
| **At least one indexer** | A torrent tracker and/or Usenet indexer you have access to. **Nothing downloads without this** — no script can supply it |

**Optional:**

- **NVIDIA, AMD, or Intel GPU** — for hardware transcoding. NVIDIA works as
  shipped. AMD/Intel need `GPU_VENDOR=amd` in `.env` plus
  `docker-compose.gpu-amd.yml` layered on with `-f` — see
  [README](../README.md#using-an-amd-or-intel-gpu-instead-of-nvidia). No GPU at
  all? Delete the `deploy:` block from the `jellyfin` service in
  `docker-compose.yml`, or that container won't start.
- **Usenet subscription** — a provider (~€3–10/mo) *plus* an indexer (~€10–15/yr).
  You need **both** or SABnzbd does nothing. Skip it and use torrents only.

---

## 1. Create your config file

```bash
cp .env.example .env
```

<details>
<summary>Windows PowerShell</summary>

```powershell
Copy-Item .env.example .env
```
</details>

Open `.env` and fill in **three** things — it ships pre-filled for Privado
over OpenVPN; using a different provider or WireGuard needs a couple of
different lines, see the note at the bottom of `.env.example`:

```ini
VPN_SERVICE_PROVIDER=privado
VPN_TYPE=openvpn
OPENVPN_USER=your-openvpn-username
OPENVPN_PASSWORD=your-openvpn-password
MEDIA_ROOT=D:/media
TZ=Europe/Amsterdam
```

⚠ **These are NOT your website login**, whichever provider you use. Nearly
every provider issues a *separate* OpenVPN/WireGuard credential on a "manual
setup" page in your account dashboard — Privado's is at its
[admin panel](https://app.privadovpn.com/admin-panel). Using your email
address here fails with `AUTH_FAILED`. This trips up almost everyone,
regardless of provider.

⚠ If your password contains a `$`, type it **twice** (`pa$$word` → `pa$$$$word`)
or Docker Compose will treat it as a variable and silently mangle it.

**`MEDIA_ROOT`** — forward slashes, no trailing slash. `D:/media` on Windows,
`/srv/media` on Linux.

---

## 2. Create the media folders

```bash
mkdir -p /srv/media/{torrents,usenet}/{complete,incomplete} /srv/media/library/{movies,tv}
```

<details>
<summary>Windows PowerShell</summary>

```powershell
$root = "D:\media"   # must match MEDIA_ROOT in .env
New-Item -ItemType Directory -Force `
  "$root\torrents\complete", "$root\torrents\incomplete", `
  "$root\usenet\complete",   "$root\usenet\incomplete", `
  "$root\library\movies",    "$root\library\tv"
```
</details>

You should end up with exactly this:

```
MEDIA_ROOT/
├── torrents/complete
├── torrents/incomplete
├── usenet/complete
├── usenet/incomplete
└── library/movies
    library/tv
```

**Do not split these across two drives.** The apps move finished downloads into
the library using hardlinks, which only work within one filesystem. Split them
and every import silently becomes a slow full copy needing double the space.

---

## 3. Generate your VPN API key

```bash
cp gluetun-auth.toml.example gluetun-auth.toml
docker run --rm qmcgaw/gluetun:v3 genkey
```

Copy the generated key into **both** `apikey = "..."` lines in
`gluetun-auth.toml`.

⚠ Generate your own. Never reuse a key from someone else's copy of this repo.

---

## 4. Start everything

```bash
docker compose up -d
docker logs -f gluetun
```

**Wait for:** `Initialization Sequence Completed` and a line showing a public
IP. Then press `Ctrl+C` to stop watching the log (this does not stop the
container).

qBittorrent and Prowlarr deliberately will not start until the VPN is healthy.

**If you see `AUTH_FAILED`:** your credentials in step 1 are wrong. It's almost
always the email-instead-of-OpenVPN-username mistake.

---

## 5. Verify the VPN actually works

**Do not skip this.** It confirms your real IP is not exposed.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-vpn.ps1
```

<details>
<summary>Linux / Mac (no script — check manually)</summary>

```bash
echo "Your real IP:"
curl -s https://icanhazip.com
echo "qBittorrent's IP (must be DIFFERENT):"
docker exec qbittorrent curl -s https://icanhazip.com
```
</details>

The two IPs **must differ**. If they match, stop and fix the VPN before
downloading anything.

---

## 6. Set a permanent qBittorrent password

This is the **only** step the automation can't do for you. qBittorrent prints
its random first-run password only to its Docker log, never to a file.

```bash
docker logs qbittorrent 2>&1 | grep "temporary password"
```

<details>
<summary>Windows PowerShell</summary>

```powershell
docker logs qbittorrent | Select-String "temporary password" | Select-Object -Last 1
```
</details>

1. Open <http://localhost:8090>
2. Log in as `admin` with that temporary password
3. Go to **Options → Web UI → Authentication** and set your own password
4. Put it in `.env`:

```ini
QBITTORRENT_USER=admin
QBITTORRENT_PASSWORD=your-chosen-password
```

⚠ Do this now, not later. Until you set a permanent password, **every container
restart generates a new random one** and locks you out.

**Optional but recommended** — also add a Jellyfin account to `.env`, and the
next step will set up Jellyfin **and Jellyseerr** completely for you (Jellyseerr
signs in with Jellyfin, so it's the same credentials):

```ini
JELLYFIN_ADMIN_USER=yourname
JELLYFIN_ADMIN_PASSWORD=your-password
```

---

## 7. Run the automated setup

```bash
docker compose run --rm setup
```

This configures **everything else**: download clients, folders, quality rules,
size caps, seeder minimums, app-to-app connections, SABnzbd categories, Bazarr,
Jellyfin's wizard + libraries + hardware transcoding + a startup trigger on
the library scan (so new media shows up right after a restart, not just on the
next scheduled scan), and Jellyseerr's whole setup — admin account, libraries,
and its Sonarr/Radarr connections.

You'll see a list of `[ok]` and `[skip]` lines, then a summary. **Safe to re-run
any time** — it checks what already exists and never duplicates or deletes.

It ends by telling you exactly what's left, which brings you to:

---

## 8. Add your indexers

**Nothing will download until you do this.** The script can't — it needs your
accounts.

1. Open <http://localhost:9696> (Prowlarr)
2. **Indexers → Add Indexer**
3. Search for the trackers or Usenet indexers you have access to, add them
4. **Settings → Apps → Sync App Indexers** to push them to Sonarr and Radarr

Prowlarr is the *only* place indexers get configured — Sonarr and Radarr inherit
them automatically.

**Using Usenet?** Also open <http://localhost:8080> (SABnzbd) →
**Config → Servers** and enter your provider's details.

---

## 9. Re-run the setup script

```bash
docker compose run --rm setup
```

Now that indexers exist, this applies the minimum-seeder filter to them (it had
nothing to configure on the first run). Takes seconds.

---

## 10. Invite people to Jellyseerr

Jellyseerr (<http://localhost:5055>) is the request page other people use
instead of getting a Radarr login — they search, click **Request**, and it
lands in Radarr/Sonarr automatically.

Step 7 already set it up completely: admin account, both libraries, and the
Sonarr/Radarr connections. **Sign in with the same Jellyfin username and
password** — there's nothing to configure.

All that's left is **Settings → Users → Invite** for anyone else who should
use it. Their requests need your approval by default.

> Requests go in at the `HD-1080p` quality profile unless you set
> `JELLYSEERR_QUALITY_PROFILE` in `.env` (e.g. `Ultra-HD`) and re-run setup.

---

## ✅ You're done

Test it end to end:

1. Open <http://localhost:7878> (Radarr) → **Add New** → search a movie
2. Pick a **Quality Profile** — ⚠ *not* "Any", which despite the name **excludes
   4K**. Use `HD-1080p` or `Ultra-HD`
3. Tick **Start search for missing movie** → **Add**
4. Watch **Activity → Queue** — it should find and grab something within a minute

When it finishes, it appears automatically in Jellyfin at <http://localhost:8096>.

### Your services

| Service | URL | For |
| --- | --- | --- |
| **Jellyfin** | <http://localhost:8096> | Watching |
| **Jellyseerr** | <http://localhost:5055> | Requesting (see step 10) |
| **Radarr** | <http://localhost:7878> | Movies |
| **Sonarr** | <http://localhost:8989> | TV |
| Prowlarr | <http://localhost:9696> | Indexers |
| Bazarr | <http://localhost:6767> | Subtitles |
| SABnzbd | <http://localhost:8080> | Usenet |
| qBittorrent | <http://localhost:8090> | Torrents |

In normal use you only touch the first three.

**On a headless server?** All of these except Jellyfin bind to `127.0.0.1`, so
none of them are reachable from another machine. Set `BIND_ADDRESS=0.0.0.0` in
`.env` and re-run `docker compose up -d` — then use `http://<server-ip>:<port>`
instead of `localhost`. See
[Reaching the web UIs from another machine](../README.md#reaching-the-web-uis-from-another-machine)
for what that exposes.

### Two things worth doing next

**Watch from your phone/TV** — Jellyfin needs a firewall rule first:

```powershell
New-NetFirewallRule -DisplayName "Jellyfin (8096)" -Direction Inbound -Protocol TCP -LocalPort 8096 -Action Allow -Profile Private
```

Run that in an **Administrator** PowerShell.

<details>
<summary>Linux</summary>

```bash
sudo ufw allow 8096/tcp
```

(or your distro's equivalent — `firewall-cmd --add-port=8096/tcp --permanent && firewall-cmd --reload` on firewalld systems)
</details>

Then browse to `http://<your-lan-ip>:8096`. See
[Jellyfin shows several servers](../README.md#jellyfin-shows-several-servers-and-none-work)
if the app finds multiple entries.

**Add media in bulk** — instead of one at a time, point Radarr/Sonarr at a
Trakt or IMDb list under **Settings → Import Lists**. Add a film to that list
from your phone and it downloads automatically. See
[Adding media](../README.md#adding-media-you-do-not-add-things-one-at-a-time).

---

## Optional: running the whole stack without a VPN

`docker-compose.novpn.yml` is an **alternative** to everything above, not an
addition to it — same project, same container names, same ports, same
volumes as the stack you just set up, minus gluetun. You run one file or the
other, never both; starting one while the other's containers exist fails
outright on a naming conflict rather than silently running both.

> ⚠️ **qBittorrent and Prowlarr connect directly to the internet in this
> mode.** Every peer in a torrent swarm sees your real IP. Every indexer
> search reveals which sites you query to your ISP. There is **no kill
> switch**. Switching to it should always be a decision you consciously
> make, never a default.

**If you just want to know what the VPN costs you in speed, you don't need
any of this** — that's a separate, safe question:

```powershell
.\scripts\speedtest.ps1
```

```bash
./scripts/speedtest.sh
```

That compares direct vs. tunnelled throughput without ever exposing your IP
to a swarm.

### Setting this up fresh (skipping the VPN entirely)

The same numbered steps as above, minus everything VPN-specific. Steps 2 and
6 are identical either way, so they're linked rather than repeated.

1. **Create `.env`** — same as [step 1](#1-create-your-config-file), but you
   only need two lines, not six:
   ```ini
   MEDIA_ROOT=D:/media
   TZ=Europe/Amsterdam
   ```
   Skip `VPN_SERVICE_PROVIDER`, `VPN_TYPE`, `OPENVPN_USER`,
   `OPENVPN_PASSWORD` entirely — `docker-compose.novpn.yml` has no gluetun
   service to read them.
2. **Create the media folders** — identical regardless of VPN, follow
   [step 2](#2-create-the-media-folders) exactly as written.
3. **Skip steps 3, 4, and 5 entirely** — no API key to generate, no gluetun
   to wait for, no tunnel to verify. There isn't one.
4. **Start it:**
   ```bash
   docker compose -f docker-compose.novpn.yml up -d
   ```
5. **Set a permanent qBittorrent password** — identical mechanics to
   [step 6](#6-set-a-permanent-qbittorrent-password); the container is still
   named `qbittorrent`, so every command there works unchanged.
6. **Run the automated setup:**
   ```bash
   docker compose -f docker-compose.novpn.yml run --rm setup
   ```
   Same script as step 7, told to reach qBittorrent/Prowlarr directly instead
   of through gluetun.
7. **Add your indexers** — identical to [step 8](#8-add-your-indexers).
8. **Re-run setup** — identical to [step 9](#9-re-run-the-setup-script), same
   `-f docker-compose.novpn.yml` flag as step 4 above.

You're done — same "✅ You're done" verification as the VPN path applies here
too, just without ever having touched a VPN.

### What actually changes, if you're switching an existing setup instead

If you already did the full VPN setup above and want to toggle modes rather
than start fresh, this is what's actually happening under the hood.

Verified directly: every named volume and container name is identical
between the two files except `gluetun` itself, which simply doesn't exist in
this one. `qbittorrent` and `prowlarr` publish their own ports directly
instead of going through gluetun's shared network namespace — Sonarr, Radarr,
Bazarr, SABnzbd, Jellyfin, and Recyclarr are byte-for-byte the same file,
since none of those were ever VPN-routed in the protected stack either.

This means switching **reuses all your existing data.** Your library, your
Sonarr/Radarr configuration, your indexers, your watch history — none of it
is duplicated or lost. You're only changing *how* qBittorrent and Prowlarr
reach the internet.

### Switch to no-VPN

```bash
docker compose down
docker compose -f docker-compose.novpn.yml up -d
```

Everything comes up exactly as configured before — same Sonarr library, same
indexers — just with qBittorrent and Prowlarr now reaching the internet
directly.

### Switch back to VPN-protected

```bash
docker compose -f docker-compose.novpn.yml down
docker compose up -d
```

Don't leave the no-VPN mode running longer than you need it.

### Verify which mode you're actually in

```bash
docker ps --format "{{.Names}}"
```

No `gluetun` in the list means you're in no-VPN mode. Or check the IPs
directly:

```bash
curl -s https://icanhazip.com
docker exec qbittorrent curl -s https://icanhazip.com
```

Matching IPs means no-VPN mode — the opposite of what step 5 verifies for the
protected stack.

---

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| `AUTH_FAILED` in gluetun | Use the **OpenVPN** username from your provider's admin panel, not your email |
| qBittorrent/Prowlarr never start | Normal until the VPN is healthy. Check `docker logs gluetun` |
| Can't log in to qBittorrent | The temp password changed on restart. Re-read the log (step 6) and set a permanent one |
| Setup script warns about qBittorrent | Credentials in `.env` are missing or wrong. Fix and re-run |
| Nothing downloads, nothing found | No indexers yet, or they didn't sync. Do step 8, then Prowlarr → **Sync App Indexers** |
| Searches find only 1080p | The profile is "Any", which excludes 4K despite the name. Switch to `Ultra-HD` |
| Jellyfin library empty | Files only appear after Sonarr/Radarr *import* them. Raw downloads aren't in the library |
| `Category does not exist` | Re-run the setup script — it creates SABnzbd's categories |
| Jellyfin container won't start | No NVIDIA GPU. Delete the `deploy:` block from the `jellyfin` service, or switch to [`docker-compose.gpu-amd.yml`](../README.md#using-an-amd-or-intel-gpu-instead-of-nvidia) if you have an AMD/Intel GPU instead |
| Not sure if the VPN is actually protecting you right now | Run `scripts\check-vpn.ps1` / `scripts/check-vpn.sh` — it warns loudly if it detects you're running the no-VPN mode |

Full troubleshooting: [README.md](../README.md#troubleshooting)

---

## Everyday commands

```bash
docker compose up -d                        # start
docker compose down                         # stop
docker compose pull && docker compose up -d # update
docker compose logs -f sonarr               # watch one service
```

⚠ After changing anything about **gluetun**, always restart its dependants —
they share its network and won't reconnect on their own:

```bash
docker compose restart qbittorrent prowlarr
```
