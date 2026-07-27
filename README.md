# qBittorrent behind PrivadoVPN + Jellyfin (Docker)

Stack location: `C:\docker\downloader` (deliberately **not** under OneDrive)

The Compose project name is pinned to `downloader` in `docker-compose.yml`, so
this folder can be renamed or moved freely — see
[Renaming or moving this folder](#renaming-or-moving-this-folder).

| | |
| --- | --- |
| qBittorrent WebUI | <http://localhost:8090> — **this PC only** |
| Jellyfin | <http://localhost:8096> — LAN (+ tailnet if you add Tailscale) |
| gluetun control API | <http://localhost:8010/v1/publicip/ip> — this PC only, API key required |
| Finished downloads | `D:\Torrents\complete` → `/data/complete` |
| In progress | `D:\Torrents\incomplete` → `/data/incomplete` |

---

# QUICK START

Everything below this section is reference material — read it when something
breaks or when you want to know *why*. This section is the whole job, in order.

## What is in this folder

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | The stack: gluetun (VPN) + qBittorrent + Jellyfin |
| `.env` | Your Privado credentials. **Secret.** Created from `.env.example` |
| `.env.example` | Template with placeholders — safe to share |
| `gluetun-auth.toml` | API key for gluetun's control server. **Secret.** Created from the `.example` |
| `gluetun-auth.toml.example` | Template with placeholder key — safe to share |
| `.gitignore` | Keeps `.env` and `gluetun-auth.toml` out of any commit |
| `check-vpn.ps1` | Leak test + kill-switch test. Run before downloading |
| `vpn-toggle.ps1` | Pause/resume the tunnel. **Pause, not bypass** — see below |
| `speedtest.ps1` | Direct vs tunnelled throughput. Exposes nothing |
| `docker-compose.novpn.yml` | ⚠ Separate no-VPN qBittorrent for benchmarking. **Opt-in only** |
| `README.md` | This file |

## Prerequisites

- **Docker Desktop** with the WSL2 backend, running
- **A PrivadoVPN account** — the free tier works (10 GB/month)
- **A drive with space for downloads.** This setup uses `D:\Torrents`. On a
  different machine, change the two `D:/Torrents...` lines in
  `docker-compose.yml` and create the folders
- **An NVIDIA GPU** — *optional*. Without one, delete the `deploy:` block from
  the jellyfin service or that container will not start

## Steps

**1. Create the download folders**

```
New-Item -ItemType Directory -Force D:\Torrents\complete, D:\Torrents\incomplete
```

**2. Put your Privado credentials in `.env`**

Copy `.env.example` to `.env` and fill in both values.

⚠️ These are **not** your website login. Privado issues a separate OpenVPN
username on the admin panel at <https://app.privadovpn.com/admin-panel>, with the
password next to it. Using your email here fails with `AUTH_FAILED`.
→ *Details: [Setup](#setup)*

**3. Generate your own gluetun API key**

Only needed if you got this folder from someone else — never reuse a shared key.

```
docker run --rm qmcgaw/gluetun:v3 genkey
```

Paste the result into the `apikey = "..."` line in `gluetun-auth.toml`.
→ *Details: [gluetun control API authentication](#gluetun-control-api-authentication)*

**4. Start the stack**

```
docker compose up -d
docker logs -f gluetun
```

Wait for `Initialization Sequence Completed` and a public IP line. qBittorrent
will not start until gluetun is healthy. `AUTH_FAILED` means step 2 is wrong.

**5. Verify the VPN before downloading anything**

```
powershell -ExecutionPolicy Bypass -File .\check-vpn.ps1
```

The container's IP must differ from yours. **Do not skip this.**
→ *Details: [Verify the VPN before downloading](#verify-the-vpn-before-downloading)*

**6. Log in to qBittorrent and set a permanent password**

```
docker logs qbittorrent | Select-String "temporary password" | Select-Object -Last 1
```

Open <http://localhost:8090>, log in as `admin`, then **immediately** set your own
password under *Options → Web UI → Authentication*. Until you do, the password
changes on every restart and you will get locked out.
→ *Details: [Setup step 6](#setup)*

**7. Configure qBittorrent**

- *Options → Downloads*: save to `/data/complete`, incomplete `/data/incomplete`
- *Options → Connection*: untick UPnP/NAT-PMP (Privado has no port forwarding)
- *Options → Downloads*: leave *Run external program on completion* empty

→ *Details: [Settings to apply in the WebUI](#settings-to-apply-in-the-webui)*

**8. Set up Jellyfin**

Open <http://localhost:8096>, run the wizard. Library folder is `/media`, content
type Movies. Turn **on** real-time monitoring; turn **off** *Save artwork into
media folders* and *Save metadata as NFO* (the mount is read-only).

Then *Dashboard → Playback → Transcoding* → **NVIDIA NVENC** to use the GPU.
→ *Details: [Jellyfin setup](#jellyfin-setup)*

**9. Watch from your phone or TV** — same network only:
`http://192.168.50.9:8096` (your LAN IP; `ipconfig` shows the current one).

## Pausing the VPN

```
.\vpn-toggle.ps1            # toggle on/off
.\vpn-toggle.ps1 status     # current state + exit IP, changes nothing
.\vpn-toggle.ps1 stop
.\vpn-toggle.ps1 start      # reconnects, then verifies your real IP is not exposed
```

**There is no speed decision to make here.** "Off" does not mean "download
faster without the VPN" — it means qBittorrent has **no internet at all**.
gluetun's firewall stays up when the tunnel stops, so downloads go to 0 B/s, not
to full speed. Measured directly: with the tunnel stopped, `curl` from inside the
qbittorrent container returns nothing.

Use it to stop torrent traffic immediately, or to force a reconnect to a
different Privado server. Not as a performance switch.

### What the VPN actually costs you

Run it yourself — this puts nothing into a torrent swarm:

```
.\speedtest.ps1                 # 25 MB per path
.\speedtest.ps1 -SizeMB 100     # firmer numbers, more data used
```

Measured here (50 MB × 2, best of each):

| Path | Speed |
| --- | --- |
| Direct (no VPN) | ~81 MB/s — **649 Mbps** |
| Through Privado (OpenVPN/UDP) | ~21 MB/s — **167 Mbps** |
| Tunnel overhead | **~74%** |

That is a big cut, and normal for OpenVPN: single-threaded, encryption in
userspace. WireGuard loses far less, but gluetun's Privado support is
OpenVPN-only — switching to a WireGuard-capable provider is the only real fix.

**It almost certainly does not matter.** 167 Mbps is well above what a typical
swarm delivers. Your practical limits are seeder count and the absence of port
forwarding, not this ceiling. If a torrent is slow, the tunnel is rarely why.

> Only the tunnelled runs consume your Privado allowance (free tier: 10 GB/month).
> A `-SizeMB 100 -Runs 2` test spends 200 MB of it.

**Turning the VPN off does not give you the 649 Mbps** — it gives you no
connectivity at all. To actually measure qBittorrent unprotected, use the
separate instance below.

## Current state of this install

| | |
| --- | --- |
| VPN + kill switch | ✅ Working, verified |
| qBittorrent | ✅ Running, permanent password set |
| Jellyfin | ✅ Running, GPU passthrough verified |
| Control API locked down | ✅ API key + localhost-only |
| **Tailscale / remote access** | ⬜ **Not set up.** Server side is ready — `EnableRemoteAccess` is on and the tailnet subnet is registered. Only the Tailscale install is left, whenever you want it → [Tailscale setup](#tailscale-setup) |
| HTTPS | ⬜ Not set up. Everything is plain HTTP on the LAN. The tidy fix is `tailscale serve`, which comes free with the step above |

## Everyday commands

```
docker compose up -d                       # start
docker compose down                        # stop
docker compose pull; docker compose up -d  # update
docker compose restart qbittorrent         # ALWAYS run this after touching gluetun
```

That last one is not optional — [here is why](#why-gluetun-always-needs-docker-compose-restart-qbittorrent-after-it).

---

# REFERENCE

## How it works

`gluetun` builds the OpenVPN tunnel to Privado and owns the network namespace.
qBittorrent joins that namespace with `network_mode: service:gluetun`, so it has
**no network path except the tunnel**. If the VPN drops, qBittorrent goes dark —
it cannot fall back to your home connection. That is the kill switch.

The WebUI port is published on `gluetun`, not on `qbittorrent`. This is required:
a container sharing another's namespace cannot publish ports of its own.

### Changing the ports

The defaults 8080 and 8000 were moved to **8090** and **8010** on purpose — both
are heavily contested on a dev machine (8080: Tomcat, webpack, Spring Boot;
8000: Django, uvicorn, `python -m http.server`). To change them again, edit
**two** things per port and keep them in sync:

| Port | Publish on `gluetun` | Matching setting |
| --- | --- | --- |
| qBittorrent WebUI | `ports: - 127.0.0.1:8090:8090` | `WEBUI_PORT=8090` on the **qbittorrent** service |
| gluetun control API | `ports: - 127.0.0.1:8010:8010` | `HTTP_CONTROL_SERVER_ADDRESS=:8010` on **gluetun** |

The `127.0.0.1:` prefix restricts each to this machine — see
[Security](#security-what-is-exposed-to-whom). Remove it to expose on the LAN.

Then:

```
docker compose up -d
docker compose restart qbittorrent
```

Two gotchas: the `ports:` entry always goes on **gluetun**, never on
`qbittorrent` — Docker hard-errors otherwise. And `WEBUI_PORT` is passed to
qBittorrent as a `--webui-port` command-line flag, so it overrides the port
stored in `qBittorrent.conf`; do not try to change the port from inside the WebUI
as well, or the two will disagree.

Jellyfin is independent — it has its own namespace, so its `8096:8096` can be
changed on the jellyfin service directly with nothing to keep in sync.

**Jellyfin is deliberately *not* behind the VPN.** It has its own normal network
namespace and its own published port. It needs to be reachable from your LAN and
to fetch artwork and metadata; routing it through Privado would break both, and
there is nothing to hide about serving your own files to yourself. Only the
torrent traffic needs the tunnel.

### Why qBittorrent uses one mount (`/data`) and not two

This matters for Jellyfin, and it is not obvious. `D:/Torrents/complete` and
`D:/Torrents/incomplete` as two separate bind mounts are **cross-device** as far
as Linux is concerned, even though they are on the same physical drive — verified
here with a hardlink test:

```
ln: failed to create hard link '/downloads/lt' => '/incomplete/lt': Cross-device link
```

`rename(2)` fails with `EXDEV` across mount points, so qBittorrent falls back to
copy-then-delete when a torrent finishes. During that copy the file exists in the
completed folder at partial, growing size — and Jellyfin's watcher fires on the
*start* of the copy. You get a library entry for a truncated 20 GB file.

Mounting `D:/Torrents:/data` once makes the finish an atomic rename, so the file
appears in `/data/complete` fully formed or not at all. Jellyfin also never sees
`/data/incomplete`, because only `complete` is mounted into it.

## Setup

1. Edit `.env` **before the first `docker compose up`** — it ships with
   placeholders, and starting with those leaves gluetun in an auth-failure
   restart loop against Privado (`AUTH_FAILED` in the logs).

   ```
   PRIVADO_USER=...
   PRIVADO_PASSWORD=...
   ```

   **These are not your website login.** Privado issues a separate OpenVPN
   username on the admin panel at <https://app.privadovpn.com/admin-panel>, with
   the password shown next to it. Their docs are explicit: your email address
   cannot be used as the username for manual setup.

   If the password contains a `$`, double it (`$$`) or Docker Compose will try to
   expand it as a variable.

2. Start it:

   ```
   docker compose up -d
   ```

3. Watch the tunnel come up:

   ```
   docker logs -f gluetun
   ```

   You want to see `INFO [vpn] You are running ... ` and a public IP line.
   qBittorrent will not start until gluetun reports healthy.

4. Get the temporary WebUI password — linuxserver generates a random one on first
   run and it is **only** in the log:

   ```
   docker logs qbittorrent | Select-String -Pattern "password"
   ```

5. Open <http://localhost:8090>, log in as `admin` with that password.

6. **Set a permanent password immediately** — **Options -> Web UI ->
   Authentication**, choose your own username and password, Save.

   This is not optional housekeeping. Until a real password is stored,
   qBittorrent generates a **new random temporary password on every container
   restart**, and the previous one stops working. Any restart of the stack —
   including `docker compose restart qbittorrent` after touching gluetun — locks
   you out until you re-read the log. Setting a password once ends this.

   To recover the current temporary password at any time:

   ```
   docker logs qbittorrent | Select-String "temporary password" | Select-Object -Last 1
   ```

## Settings to apply in the WebUI

Privado has **no port forwarding on any plan**, so incoming connections will never
work. Configure for that instead of letting it retry forever:

- **Options -> Connection**: uncheck *Use UPnP / NAT-PMP port forwarding from my router*
- **Options -> Advanced -> Network interface**: optionally set to `tun0`. This is
  belt-and-braces only — the shared namespace already guarantees the binding — and
  it is the one setting that can lock you out of your own client. See recovery
  below before using it.
- **Options -> Downloads**: Save files to `/data/complete`, tick *Keep incomplete
  torrents in* → `/data/incomplete`. Use these container paths, never `D:\...` —
  qBittorrent cannot see Windows paths.
- **Options -> Downloads**: leave *Run external program on torrent completion*
  **empty** (see the virus section below)
- **Options -> BitTorrent**: enable encryption if you like; leave DHT/PeX on

Practical effect: downloads are fine, seeding is weak (you can only connect
outbound to peers). If ratio matters to you, a provider with port forwarding
(Proton, AirVPN, PIA) would serve you better — gluetun supports those too and only
the `gluetun` environment block would change.

## Jellyfin setup

### 1. Run the first-start wizard

Open <http://localhost:8096>.

**Language** — pick yours, Next.

**Create your admin user** — a username and password of your choosing. This
account is Jellyfin's own; it has nothing to do with qBittorrent or Privado.
Choose a real password now: unlike qBittorrent there is no temporary-password
fallback, and resetting it later means editing Jellyfin's database.

### 2. Add the media library

**Add Media Library**, in the wizard:

- **Content type**: `Movies`
- **Display name**: `Movies`
- **Folders**: click **+**, navigate to `/media`, select it
  (`/media` is `D:\Torrents\complete`, mounted read-only)

Then scroll down inside that same dialog:

| Setting | Value | Why |
| --- | --- | --- |
| Enable real time monitoring | **on** | This is what makes new downloads appear automatically |
| Save artwork into media folders | **off** | `/media` is read-only — leaving it on logs write failures every scan |
| Save metadata as NFO | **off** | Same reason |

Metadata and artwork go into Jellyfin's own config volume instead, which is where
you want them anyway.

**Metadata language** — set your preference. English usually gives the best match
rate even if you run the UI in Dutch.

**Remote access** — leave *Allow remote connections* ticked, but **untick
"Enable automatic port mapping"**. That is UPnP and it does nothing useful here.

Finish, then log in with the account you just created.

### 3. Set the scan backstop

**Dashboard → Scheduled Tasks → Scan Media Library** — set the interval trigger
to every few hours. Real-time monitoring should catch downloads, but this covers
anything it misses and anything you drop in from Explorer. See below for why
those two cases differ.

### 4. Watching from other devices

On any device on the same network:

```
http://192.168.50.9:8096
```

(That is this machine's LAN IP — it can change if your router hands out a new
lease. `ipconfig` will tell you the current one.)

Official Jellyfin apps exist for Android, iOS, Android TV and any web browser.
Nothing is exposed to the internet, which is the right default — this is LAN-only.

### A note on file naming

Jellyfin matches on title and year, so a typical release folder name usually
works. If something is not identified, rename the folder to `Movie Name (Year)` —
the format it parses most reliably — or use **Identify** on the item to fix the
match by hand.

If you will download TV as well as films, make `D:\Torrents\complete\Movies` and
`D:\Torrents\complete\Shows`, use qBittorrent **categories** to route downloads
into each, and add a second Jellyfin library with content type *Shows*. Mixing
both in one folder makes matching noticeably worse.

### Deleting media

The read-only mount means Jellyfin's "delete media" button will not work. That is
intentional — deleting a file qBittorrent is still seeding breaks the torrent.
**Delete through qBittorrent, not through Jellyfin or Explorer.**

### Will it pick up new downloads automatically?

Yes, but the reason is subtle and worth recording. Filesystem watching on Windows
bind mounts is unreliable — tested here directly:

| Write origin | inotify event seen by another container |
| --- | --- |
| From Windows (Explorer / PowerShell) | **no** |
| From inside a container | **yes** |

qBittorrent writes from inside a container, so its completed files *do* generate
events and Jellyfin's real-time monitoring sees them. Files you drag into
`D:\Torrents\complete` from Explorer will **not** be noticed — those need a scan.

Because Jellyfin's watcher is its own implementation and historically flaky on
non-native filesystems, keep a backstop: **Dashboard → Scheduled Tasks → Scan
Media Library**, set it to run every few hours. Costs nothing and covers both the
Explorer case and any missed event.

**Test the watcher once your library exists** — this fakes a completed torrent
without downloading anything:

```
docker exec qbittorrent sh -c "mkdir -p /data/incomplete/WatcherTest && dd if=/dev/urandom of=/data/incomplete/WatcherTest/probe.mkv bs=1M count=8 2>/dev/null && mv /data/incomplete/WatcherTest /data/complete/WatcherTest && echo moved"
```

Wait about a minute, then:

```
docker logs jellyfin --tail 60 | Select-String -Pattern "Library|scan|WatcherTest"
```

Clean up afterwards:

```
docker exec qbittorrent rm -rf /data/complete/WatcherTest
```

If the log shows a library change, real-time monitoring works end to end. If not,
lean on the scheduled scan — everything else still functions.

### Hardware transcoding

Your RTX 3070 Ti works in the container — verified with a real encode
(`h264_nvenc`, not just a compiled-in codec list), and `nvidia-smi` sees the GPU
from inside Jellyfin. The compose file already passes the GPU through.

**This is not automatic** — passing the GPU into the container and *using* it are
two different things. Turn it on under **Dashboard → Playback → Transcoding**:

- Hardware acceleration: **NVIDIA NVENC**
- Tick **H264**, **HEVC**, **HEVC 10bit**, **VP9**
- Tick **Enable hardware decoding** and **Enable hardware encoding**
- Tick **Enable Tone mapping** — DV/HDR10+ content looks washed out on SDR
  screens without it

Save. Without this, transcoding falls back to CPU — the i9-12900H copes, but 4K
HDR will make it work hard.

If you ever move this stack to a machine without an NVIDIA GPU, delete the
`deploy:` block from the jellyfin service or the container will not start.

## Security: what is exposed to whom

| Service | Bound to | Reachable from | Auth |
| --- | --- | --- | --- |
| qBittorrent WebUI | `127.0.0.1:8090` | This PC only | Password |
| gluetun control API | `127.0.0.1:8010` | This PC only | API key |
| Jellyfin | `0.0.0.0:8096` | LAN + tailnet | Password |

A Docker `ports:` entry without an address prefix binds to `0.0.0.0` — every
interface, so every device on your Wi-Fi. Prefixing it with `127.0.0.1:` limits
it to this machine. That is the single cheapest hardening step available here and
it costs nothing when you only use a service locally.

### How `gluetun-auth.toml` works

gluetun reads it **once at startup** from `/gluetun/auth/config.toml`, bind-mounted
read-only by the compose file. Each `[[roles]]` block is an allow-rule:

```toml
[[roles]]
name   = "healthcheck"                                  # label, for logs only
routes = ["GET /v1/publicip/ip", "GET /v1/vpn/status"]  # "METHOD /path" pairs
auth   = "apikey"                                       # none | basic | apikey
apikey = "..."                                          # sent as X-API-Key header
```

Three things worth knowing:

1. **Supplying this file replaces gluetun's built-in defaults entirely.** Any
   route not named in a role is denied — that default-deny is why
   `GET /v1/openvpn/settings` returns 401 even *with* a valid key.
2. **Roles are additive.** A route is reachable if any role lists it. Splitting
   "healthcheck" and "toggle" into two blocks is not required by gluetun — it
   documents intent and lets you revoke the VPN-stopping capability by deleting
   one block.
3. **Changes need a recreate, not a restart** — gluetun only parses the file at
   startup, and a bind-mounted file change alone does not trigger one:

   ```
   docker compose up -d --force-recreate gluetun
   docker compose up -d --force-recreate qbittorrent
   ```

   (The second is mandatory — see [the namespace
   note](#why-gluetun-always-needs-docker-compose-restart-qbittorrent-after-it).)

### Can the key live in `.env` instead, so this file can be committed?

Short answer: **no, and you do not need it to.**

gluetun does **not** support environment-variable interpolation inside
`config.toml` — the value must be written literally. There is one alternative,
`HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE`, which takes a JSON role as an env var
and *could* carry the key from `.env`:

```yaml
- HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE={"name":"default","auth":"apikey","apikey":"${GLUETUN_API_KEY}"}
```

But it sets the role for **every route not covered by a config file** — so used
on its own it hands the same key access to *all* routes, including the ones that
stop your VPN, read your OpenVPN credentials-adjacent settings, and change port
forwarding. That trades away the least-privilege split for tidiness. Not worth it.

**The pattern that actually solves "I want to commit this" is the same one
already used for `.env`:** keep the secret file untracked, commit a template.

| Committed | Ignored |
| --- | --- |
| `gluetun-auth.toml.example` | `gluetun-auth.toml` |
| `.env.example` | `.env` |

`.gitignore` already lists both secrets. A fresh clone copies each `.example`,
generates its own key, and is running — with nothing sensitive ever in history.

> If you later `git init` here, check `git status` **before** the first commit and
> confirm neither `.env` nor `gluetun-auth.toml` appears. Once a secret is
> committed, deleting it in a later commit does not remove it from history.

### gluetun control API authentication

Before [gluetun-auth.toml](gluetun-auth.toml) existed, these answered anyone on
the LAN with no credential at all — verified directly:

```
GET /v1/publicip/ip   -> 200 {"public_ip": "..."}   # no auth
GET /v1/vpn/status    -> 200 {"status":"running"}   # no auth
```

The API is not read-only: `PUT /v1/vpn/status` can **stop your VPN**, and other
routes expose OpenVPN settings and port-forwarding config. Supplying a
`config.toml` **replaces** gluetun's built-in defaults, so anything not listed in
a role is denied outright. Current state:

| Request | Result |
| --- | --- |
| `GET /v1/publicip/ip` without key | 401 |
| `GET /v1/vpn/status` without key | 401 |
| Either, with `X-API-Key` | 200 |
| `GET /v1/openvpn/settings` even with key | 401 — not in any role |

`check-vpn.ps1` reads the key straight out of the toml, so rotate it in one place:

```
docker run --rm qmcgaw/gluetun:v3 genkey
```

**`gluetun-auth.toml` contains a secret.** Do not commit or sync it.

## Remote access to Jellyfin

### Why the "allow remote connections" checkbox is not enough on its own

`EnableRemoteAccess` only tells Jellyfin *"accept clients from outside my local
subnet."* It does **not** open a port on your router, give you a public address,
or add encryption. On its own, nothing changes from the internet's point of view.

The setting that *would* punch a hole is **"Enable automatic port mapping"**
(UPnP), unticked during setup on purpose. Checkbox + UPnP is how people
accidentally publish a plaintext-HTTP Jellyfin login to the open internet.
Jellyfin has had authentication CVEs; this is not a thing to leave exposed.

The checkbox **is** required for Tailscale, though — tailnet clients get
`100.64.0.0/10` addresses, which Jellyfin considers non-local and would reject.
So it is enabled, and the safety comes from Tailscale rather than from Jellyfin
refusing connections.

### Tailscale setup

> **Status: not installed.** The server side is ready — Jellyfin's
> `EnableRemoteAccess` is on and `100.64.0.0/10` is registered as a local
> network, so this works the moment you install the client. Nothing below has
> been done yet; pick it up whenever you want remote access.

Tailscale builds an encrypted private mesh between your own devices. **No router
ports are opened and nothing is exposed to the public internet** — devices
authenticate to your account and talk directly.

1. Create a free account at <https://tailscale.com> (free tier covers 100 devices)
2. Install the Windows client on this PC from
   <https://tailscale.com/download/windows>, sign in
3. Install Tailscale on each device you want to watch from — phone, laptop,
   Android TV — and sign in to the **same account**
4. Find this machine's tailnet address: `tailscale ip -4` (a `100.x.y.z` address)
5. Watch from any signed-in device at `http://100.x.y.z:8096`

With **MagicDNS** on (Tailscale admin console) you can use the machine name
instead: `http://your-pc-name:8096`.

### Real HTTPS, free, without a domain

`tailscale serve` fronts Jellyfin with a **browser-trusted Let's Encrypt
certificate** on a `*.ts.net` hostname — no domain purchase, no port forwarding,
no certificate warnings:

```
tailscale serve --bg 8096
```

Gives you `https://your-pc-name.your-tailnet.ts.net`, reachable only by devices
on your tailnet. This is the tidiest answer to "I want HTTPS and remote access"
for a home setup.

> Do **not** use `tailscale funnel` unless you mean it — that deliberately
> publishes the service to the entire internet, which is exactly what we avoided.

### Caveats

- This is a **laptop**. Remote access works only while it is awake and online.
  Check Windows sleep settings if streams cut out.
- qBittorrent is bound to `127.0.0.1`, so it is **not** reachable over the tailnet
  either. To change that, drop the `127.0.0.1:` prefix from its `ports:` entry —
  and add HTTPS before you do, since the login would otherwise cross the network
  in plaintext.

## Turning the VPN on and off

Three different things people mean by this. Only two of them are settings.

### 1. Pause / resume the tunnel — `vpn-toggle.ps1`

```
.\vpn-toggle.ps1            # toggle
.\vpn-toggle.ps1 status     # show state + exit IP
.\vpn-toggle.ps1 stop
.\vpn-toggle.ps1 start      # reconnects, then verifies your real IP is not exposed
```

**This is a pause button, not a bypass.** Stopping the tunnel does *not* give
qBittorrent a direct connection — gluetun's firewall stays up, so qBittorrent
loses all connectivity. Verified directly:

```
PUT /v1/vpn/status {"status":"stopped"}  ->  {"outcome":"stopped"}
curl from inside qbittorrent             ->  no response at all
```

That is the kill switch doing its job. Use this when you want torrent traffic to
stop *now* without tearing the stack down, or to force a reconnect to a different
Privado server.

It needs the `PUT /v1/vpn/status` role in `gluetun-auth.toml`. That route is
localhost-only and API-key protected — but it is the one route that can disable
your VPN, so do not widen its exposure.

### 2. Change country / exit server — a setting

In `.env`:

```
PRIVADO_COUNTRIES=Netherlands
```

Then `docker compose up -d && docker compose restart qbittorrent`.
See the available list with:

```
docker run --rm qmcgaw/gluetun:v3 format-servers -privado
```

gluetun picks a random server within the country each time it connects, which is
why the exit IP changes between reconnects.

### 3. Running qBittorrent with no VPN — `docker-compose.novpn.yml`

Built as a **separate instance**, never a mode of the protected one, so the main
stack cannot accidentally end up unprotected.

```
docker compose -f docker-compose.novpn.yml up -d     # start
docker compose -f docker-compose.novpn.yml down      # stop -- do this when done
```

WebUI: <http://localhost:8091> (localhost only).

⚠ **Any torrent added here puts your real IP into the swarm** — visible to every
peer, and to anyone monitoring it, which on public trackers includes
copyright-enforcement firms who collect IPs exactly this way. There is no kill
switch; that is the entire point of the file, and why it is separate.

What keeps it from contaminating the protected setup — verified while both ran
side by side:

| | Protected | No-VPN |
| --- | --- | --- |
| Project | `downloader` | `downloader-novpn` |
| Container | `qbittorrent` | `qbittorrent-novpn` |
| WebUI port | 8090 | 8091 |
| Config volume | `downloader_qbittorrent-config` | `downloader-novpn_qbittorrent-novpn-config` |
| Downloads | `D:\Torrents` | `D:\Torrents-novpn` |
| Exit IP observed | `91.148.240.149` (Privado) | `143.179.55.141` (yours) |
| `restart:` | `unless-stopped` | **`no`** — never survives a reboot |

Two safety features worth knowing:

- It **cannot start by accident** — the explicit `-f docker-compose.novpn.yml` is
  required, and `restart: "no"` means it never comes back on its own.
- `check-vpn.ps1` **prints a red warning** if it finds this instance running, so a
  forgotten benchmark session gets caught the next time you check the VPN.

For measuring raw throughput you do not need this at all — `speedtest.ps1`
answers that without touching a swarm. Use this only to observe qBittorrent's own
behaviour (peer counts, stall patterns, swarm connectivity) without the tunnel,
and prefer a torrent nobody objects to you having: a well-seeded official Linux
distro ISO is both the safe choice and the better benchmark.

#### First run: fix the save path before adding anything

A fresh config volume ships with qBittorrent's factory-default save path,
`/downloads` — a path that does not exist in this container; only `/data` is
mounted. Adding a torrent before fixing this fails with `Permission denied` file
errors, because qBittorrent (as UID 1000) cannot create `/downloads` at the
container root.

Fix once — it persists in the volume from then on:

```
docker compose -f docker-compose.novpn.yml stop qbittorrent-novpn
docker run --rm -v downloader-novpn_qbittorrent-novpn-config:/config alpine sh -c "sed -i 's|=/downloads/incomplete/|=/data/incomplete/|g; s|=/downloads/|=/data/complete/|g' /config/qBittorrent/qBittorrent.conf"
docker compose -f docker-compose.novpn.yml up -d
```

Or set it by hand in the WebUI first: *Options → Downloads* → save to
`/data/complete`, incomplete to `/data/incomplete`.

#### The torrenting port and why it matters here specifically

Unlike the WebUI port, `6882` is published on **all interfaces**
(`ports: - 6882:6882/tcp` and `/udp`), not `127.0.0.1`. This is the one thing
Privado can never offer this stack: a real chance at incoming peer connections.

Two things to know:

- **Docker publishing it is necessary but not sufficient.** Your router also
  needs to forward `6882` (TCP+UDP) to this PC before it works from the open
  internet. Without that router step, this instance is exactly as
  inbound-blocked as the protected one — just for a different reason (ISP NAT,
  not Privado's policy) — and a speed comparison between them will not show the
  benefit port forwarding is supposed to provide.
- **If you don't do the router step, don't expect this instance to be faster.**
  A missing router forward was exactly why an early speed test here looked
  identical to the VPN instance — this container could send outbound requests
  to peers but nothing could connect back in.

#### Picking a torrent to test with

A torrent with almost no swarm will be slow **everywhere**, VPN or not — that
is not a VPN comparison, it is a "this torrent is dead" result. Verified case:
a torrent with `num_seeds: 1` measured at ~1.7 Mbps on this no-VPN instance,
which tells you nothing about the stack. Use `torrents/info` (or the WebUI's
Seeds/Peers columns) to check seed count before drawing any conclusion, and
prefer a well-seeded official Linux ISO for an actual comparison.

## Verify the VPN before downloading

```
powershell -ExecutionPolicy Bypass -File C:\docker\downloader\check-vpn.ps1
```

It prints your real IP, the IP the container actually uses, and then stops gluetun
to confirm qBittorrent really loses connectivity. All three must look right.

For an end-to-end check, use the magnet link at <https://ipleak.net> ("Torrent
Address detection") — add it in qBittorrent and the page should show the Privado
IP, not yours.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Unauthorized` in the WebUI when using a hostname/LAN IP | Options -> Web UI -> uncheck *Enable Host header validation* (localhost works by default) |
| gluetun `AUTH_FAILED` | Use the OpenVPN username from <https://app.privadovpn.com/admin-panel>, not your email; then try `OPENVPN_PROTOCOL=tcp` |
| qBittorrent never starts | gluetun isn't healthy — `docker logs gluetun` |
| Slow / stalled torrents | Expected without port forwarding; prefer well-seeded torrents |
| Ran out of data | Privado's free tier caps at 10 GB/month |
| Jellyfin shows nothing after a download | Dashboard -> Scheduled Tasks -> *Scan Media Library* -> run manually. If that finds it, real-time monitoring missed the event |
| Jellyfin logs write/permission errors on scan | Untick *Save artwork into media folders* and *Save metadata as NFO* — `/media` is read-only by design |
| Jellyfin container won't start | Almost always the GPU `deploy:` block on a machine without NVIDIA — remove it |
| Jellyfin can't identify a movie | Rename the folder to `Movie Name (Year)`, or use *Identify* on the item |
| 4K HDR looks washed out | Tick *Enable Tone mapping* under Dashboard -> Playback -> Transcoding |
| Forgot the Jellyfin admin password | No temporary-password fallback like qBittorrent — recovery means editing the database. Worst case: `docker volume rm qbittorrent_jellyfin-config` and redo the wizard (loses watch history, not media) |
| Playback stutters on 4K | Enable NVENC under Dashboard -> Playback, or use a client that direct-plays HEVC |
| A torrent shows "missing files" after the mount change | Right-click it -> *Set Location* -> `/data/complete` |
| **Can't log in to qBittorrent any more** | The temporary password is regenerated on **every restart**. Get the current one with `docker logs qbittorrent \| Select-String "temporary password" \| Select-Object -Last 1`, then set a permanent one — see below |
| WebUI unreachable after changing a port | The `ports:` pair and `WEBUI_PORT` disagree, or the entry was put on `qbittorrent` instead of `gluetun`. See *Changing the ports* |
| qBittorrent WebUI refused from another device | Intended — it is bound to `127.0.0.1`. Use this PC, or drop the prefix in `ports:` |
| `check-vpn.ps1` step 4 says "key rejected" | The key in `gluetun-auth.toml` and the one gluetun loaded differ — `docker compose up -d` after editing the file |
| Control API returns 401 from a script | Send the key as an `X-API-Key` header; only `GET /v1/publicip/ip` and `GET /v1/vpn/status` are permitted |
| `check-vpn.ps1` reports an odd city | GeoIP databases are inaccurate on Privado's ranges (the same block has reported Medemblik, Lelystad and Copenhagen). Judge by *the IP differing from yours*, not by the city |
| Jellyfin unreachable over Tailscale | Confirm `EnableRemoteAccess` is true and `100.64.0.0/10` is in *LAN Networks* (Dashboard -> Networking) |
| No-VPN instance: torrent errors with "Permission denied" on every file | Fresh config volume still has the factory default save path `/downloads`, which is not mounted. See *First run: fix the save path* under the no-VPN section |
| WebUI dead, but both containers show `Up` | You restarted gluetun on its own — run `docker compose restart qbittorrent`. See *Why gluetun always needs...* below |

### Recovery: bad setting in the WebUI

The config lives in a Docker named volume, so it is not editable from Explorer.
To undo a `Network interface` setting that broke connectivity:

```
docker exec qbittorrent sed -i 's/^Session\\Interface=.*//' /config/qBittorrent/qBittorrent.conf
docker compose restart qbittorrent
```

## Renaming or moving this folder

Safe now, but it was not always. Compose derives the **project name** from the
folder name unless told otherwise, and the project name prefixes every named
volume:

```
folder "qbittorrent"  ->  volume qbittorrent_qbittorrent-config
folder "downloader"   ->  volume downloader_qbittorrent-config
```

Rename the folder without pinning the project, and `docker compose up -d` looks
for a volume that does not exist, **creates an empty one, and starts a blank
qBittorrent** — torrents, settings and password gone. No error, no warning. The
fixed `container_name:` values turn it into a name-conflict error instead, which
is the only reason it fails loudly rather than silently.

This is now prevented by the first line of `docker-compose.yml`:

```yaml
name: downloader
```

With the project pinned, the folder name is irrelevant. Move or rename it freely.

### If you ever do need to migrate volumes between project names

Copy, never move, so the originals remain as a rollback:

```
docker stop qbittorrent jellyfin gluetun
docker rm   qbittorrent jellyfin gluetun     # containers only -- volumes survive
docker volume create newproject_qbittorrent-config
docker run --rm -v oldproject_qbittorrent-config:/from -v newproject_qbittorrent-config:/to alpine sh -c "cd /from && cp -a . /to/"
```

Verify by file count and size before deleting anything:

```
docker run --rm -v oldproject_qbittorrent-config:/v alpine find /v -type f | Measure-Object
```

## Daily use

```
docker compose up -d      # start
docker compose down       # stop
docker compose pull; docker compose up -d    # update
```

### Why gluetun always needs `docker compose restart qbittorrent` after it

```
docker restart gluetun                 # <- never do this on its own
docker compose restart qbittorrent     # <- always follow with this
```

qBittorrent has no network stack of its own. `network_mode: service:gluetun`
means it is *joined to gluetun's* network namespace — same interfaces, same IP,
same routing table. That is exactly what makes the kill switch airtight, and it
is also the catch.

A network namespace belongs to the process that created it. When gluetun stops,
Docker tears its namespace down. When gluetun starts again it creates a **brand
new** namespace — it does not reclaim the old one. qBittorrent is still pointed
at the old, now-dead namespace, and nothing re-attaches it automatically: it ends
up with no working interface at all. The container looks `Up` in `docker ps`, but
the WebUI is unreachable and no torrent moves.

Restarting the qbittorrent container is what makes it join the current namespace.
`depends_on` only controls *startup* ordering — it does nothing on a restart of
an already-running stack.

Rules of thumb:

- `docker compose restart` / `up -d` / `down` on the **whole stack** — fine, both
  containers are handled in order.
- Touching **gluetun alone** (restart, recreate, changing anything in its
  `environment:`) — always follow with `docker compose restart qbittorrent`.
- After `--force-recreate` on gluetun, `restart` is **not enough** — it fails with
  `joining network namespace ... No such container`, because qBittorrent still
  references the old container ID. Use
  `docker compose up -d --force-recreate qbittorrent` instead.
- `vpn-toggle.ps1` does **not** need any of this — it pauses the tunnel inside the
  running container, so the namespace is never destroyed.
- Symptom that you forgot: WebUI dead, containers both `Up`, and
  `docker exec qbittorrent curl -s ifconfig.me` returns nothing.

`check-vpn.ps1` already does this for you after its kill-switch test.

## Does Docker protect me from viruses?

Partly, and it is worth being precise about which part — the protection is
narrower than it looks.

**What the container does protect.** If a malicious torrent or peer exploited a
bug *in qBittorrent itself*, the attacker would land inside the container as an
unprivileged user, with the VPN as their only network path. They would see
`/config` and `/data` — not the rest of the PC. That is a real gain over running
qBittorrent natively on Windows, but it is an uncommon attack.

**What it does not protect, which is the actual risk.** The container never opens
your files. That happens in Windows, after they have been written to
`D:\Torrents\complete`. A downloaded `.exe`, `.msi` or macro-laden document is
exactly as dangerous as it would have been without Docker. The container is a box
around the *downloader*, not around the *downloads*. It is also not a security
sandbox in the guarantee sense — it is process isolation, which can be escaped,
just rarely.

**The VPN is orthogonal.** It hides what is being downloaded from the ISP. It
does nothing about what is inside the file.

What actually reduces the risk:

- **Executables are the danger**: `.exe`, `.msi`, `.scr`, `.bat`, `.lnk`,
  `.iso`/`.img`. Media files (`.mkv`, `.mp3`, `.pdf`) are far lower risk. A movie
  torrent containing a "player" or "codec installer" is malware.
- **Password-protected archives are a red flag** — the password exists so scanners
  cannot look inside.
- **Watch for double extensions** (`movie.mp4.exe`). Turn on Explorer →
  View → Show → *File name extensions* so they are visible.
- **Read the torrent comments** before opening anything; fake uploads usually get
  called out there.
- **Let it sit, then rescan.** Defender's signatures update daily, so a scan a day
  later catches things a same-day scan misses. Right-click → *Scan with Microsoft
  Defender*.
- **Keep Defender on and do not exclude `D:\Torrents`.** Check with:
  `Get-MpComputerStatus | Select RealTimeProtectionEnabled, AntivirusSignatureAge`
- **Leave "Run external program on torrent completion" empty**
  (Options → Downloads). It runs inside the container, so the blast radius is
  limited, but there is no reason to hand a torrent-triggered code path to
  anything.

For genuinely isolated opening of something questionable, Windows Sandbox
(Pro/Enterprise) or a VM is the right tool. Docker here is not it.
