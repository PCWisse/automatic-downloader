# Running this stack on Proxmox

End-to-end setup for a headless mini PC: bare hardware → Proxmox → an LXC
container → this stack, with AMD/Intel VAAPI transcoding.

Written against an **AMD Ryzen mini PC with integrated graphics** (Vega/Radeon,
no discrete card) — the common shape for this. An Intel NUC works identically;
both use VAAPI.

> **Docker in an LXC, not one LXC per app.** Docker Compose behaves the same
> in an LXC as anywhere else, and LXC is OS-level virtualization sharing the
> host kernel — so there's no nested-VM overhead the way Docker Desktop on
> Windows has. Splitting the stack into eleven separate LXCs would cost you
> the whole `docker-compose.yml` + `setup/configure.py` automation, Compose's
> service-name DNS, and gluetun's kill-switch mechanism, in exchange for very
> little RAM.

### How to read the commands below

Anything in `<angle-brackets>` — `<CTID>`, `<GID>`, `<lxc-ip>` — is a
**placeholder, not a literal value**. Replace the whole token, brackets
included, with your own actual number or address before running the command.

This is deliberate, not just a formatting habit: `<` and `>` are special
characters to bash. If you copy-paste one of these commands **without**
replacing the placeholder, bash refuses to run it and tells you exactly
that — e.g. `bash: CTID: No such file or directory` — rather than silently
doing something to the wrong container. A command that fails loudly here is
working as intended; it's telling you to go back and substitute the real
value. Nothing with a bare, real-looking number in it (like `debian-12` or
`8096`) is ever a placeholder — those are literal, run them as shown.

Get your own real values with:

```bash
pct list          # your actual <CTID> -- run this before ANY command below
                   # that mentions a container ID. Never assume it's 100 or 101.
ls -ln /dev/dri    # your actual <GID> -- fourth column of the renderD128 line
```

---

## Before you start

| You need                    | Notes                                                                         |
| --------------------------- | ----------------------------------------------------------------------------- |
| A USB stick (≥1 GB)        | Gets wiped                                                                    |
| A keyboard + monitor        | For the install only; headless afterwards                                     |
| The mini PC's storage plan  | Proxmox**wipes the disk** — Windows and anything on it is gone         |
| A wired ethernet connection | Proxmox's installer does not do Wi-Fi. Wi-Fi on a server is a bad idea anyway |

⚠ **Back up anything on the machine first.** There is no dual-boot path here
and no undo.

---

## 1. Build the installer

Download the Proxmox VE ISO from
[proxmox.com/downloads](https://www.proxmox.com/en/downloads), then flash it:

- **Windows:** [Rufus](https://rufus.ie) → select the ISO → when prompted,
  choose **DD Image mode**, not ISO mode. The default mode produces a stick
  that won't boot.
- **Linux/Mac:** `dd if=proxmox-ve_*.iso of=/dev/sdX bs=1M status=progress`
  (check `lsblk` first — wrong device = wiped disk).

---

## 2. BIOS settings

Boot into BIOS (usually `Del` or `F2` on these mini PCs):

| Setting                    | Value     | Why                                                                                                                                  |
| -------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **SVM Mode / AMD-V** | Enabled   | Hardware virtualization. Required                                                                                                    |
| **IOMMU / AMD-Vi**   | Enabled   | Only needed for PCIe passthrough to a*VM*. Not needed for the LXC route below, but harmless and saves a reboot if you ever want it |
| **Secure Boot**      | Disabled  | Simplest path; avoids driver-signing friction                                                                                        |
| **Boot order**       | USB first | Or use the one-time boot menu (`F7`/`F11`/`F12`)                                                                               |

---

## 3. Install Proxmox

Boot the stick, pick **Install Proxmox VE (Graphical)**.

**Filesystem: choose `ext4`, not ZFS.** ZFS's ARC cache wants a real RAM
budget, and on a 16 GB box it competes with your containers for memory. ext4
with LVM-thin (the installer's default) is lighter and fine for a single node.

Set the password (this is your root login) and an email (anything
valid-looking; it's only for cron/alert mail).

### The network screen

The four network fields are the ones worth getting right, because fixing them
afterwards on a headless box means plugging a monitor back in. **Find your own
values first** — from any machine already on the same network:

```powershell
Get-NetIPConfiguration | Where-Object IPv4Address
```

```bash
ip route | grep default && ip -4 addr show
```

You want that machine's **IPv4 address**, its **prefix length** (usually
`/24`), and its **default gateway**. Everything below follows from those.

| Field                          | What to enter                                                                                          | What it does                                                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| **Management interface** | The wired NIC — likely the only option, named like`enp1s0` / `eno1`                               | Which physical port Proxmox binds its web UI and all VM/LXC traffic to. If two are listed, pick the one showing link up |
| **Hostname (FQDN)**      | `pve.local`                                                                                          | The machine's name.**Must contain a dot** — a bare `pve` is rejected                                           |
| **IP address**           | Same first three octets as the machine you checked, with a free last octet — e.g.`192.168.1.100/24` | The fixed address you'll type to reach it. Note it wants**CIDR** (`/24`), not a bare address                    |
| **Gateway**              | Your router's address, from the command above                                                          | How the box reaches the internet. Wrong here =`apt` and every Docker pull fail                                        |
| **DNS server**           | Your router's address, or`1.1.1.1`                                                                   | Resolves hostnames. The router is fine and gives you local name resolution                                              |

The installer usually pre-fills these from DHCP, and those defaults are
normally correct — you're confirming them, not inventing them.

⚠ **Afterwards, reserve that address in your router** (DHCP reservation by MAC).
Most home routers hand out nearly the whole subnet by default, so a static IP
you picked by hand can later be leased to a phone, and you'll get an address
conflict that looks like the server "randomly" disappearing.

Reboot, pull the USB stick. From now on everything is done from your laptop's
browser at **`https://<that-ip>:8006`** (log in as `root`, realm "Linux PAM").
The certificate warning is expected — it's self-signed. The box won't show up
in your router's client list until this first boot completes; look for the
hostname you set.

---

## 4. Post-install cleanup

Proxmox nags about a missing subscription and, by default, points `apt` at two
enterprise repos you don't have access to (`pve-enterprise`, and `ceph` if you
ever touch Ceph, which this setup never does). Fix in the shell
(**Datacenter → your node → Shell** — you're root there already, no `sudo`):

```bash
mv /etc/apt/sources.list.d/pve-enterprise.sources /etc/apt/sources.list.d/pve-enterprise.sources.disabled
mv /etc/apt/sources.list.d/ceph.sources /etc/apt/sources.list.d/ceph.sources.disabled
echo "deb http://download.proxmox.com/debian/pve $(. /etc/os-release && echo $VERSION_CODENAME) pve-no-subscription" > /etc/apt/sources.list.d/pve-no-subscription.list
apt update && apt full-upgrade -y
```

Proxmox VE 8+ moved these to the `.sources` (deb822) format — plain
`sed 's/^deb/#deb/'` on a `.list` file, the old fix, does nothing here: the
file isn't named `.list` anymore, and the new format doesn't have lines
starting with `deb` to match even if it did. Renaming to `.disabled` (rather
than deleting) works regardless of format — apt only reads files ending in
`.list` or `.sources` — and keeps them on disk if you ever do buy a
subscription. A clean `apt update` afterwards should show no `401 Unauthorized` errors, only hits against `pve-no-subscription` and Debian's
own repos.

Then install the GPU driver **on the host** — this is the part people miss.
The LXC shares the host kernel, so the driver has to be here, not just in the
container:

```bash
apt install -y mesa-va-drivers vainfo
vainfo
```

`vainfo` should list your GPU and a set of supported profiles (`VAProfileH264*`,
`VAProfileHEVCMain*`, …). If it errors out, hardware transcoding will not work
in the container either — fix it here before continuing.

Note the render device's group ID, you'll need it shortly:

```bash
ls -ln /dev/dri
```

Look at the `renderD128` line — the **fourth column** is the numeric GID
(commonly `104` or `993`). Write it down.

---

## 5. Create the LXC

**Download the template first** — it isn't bundled with the install, and the
Create CT wizard's template dropdown stays empty until this is done. It's a
separate screen from the wizard itself, easy to miss:

1. Left sidebar → **your node → local** (the default storage)
2. **CT Templates**
3. **Templates** button (top right) — fetches the list from Proxmox's online
   repository, needs internet
4. Search `debian-12-standard`, select it, **Download**
5. Wait for it to finish (check **Tasks** at the bottom)

**Check how much disk you actually have before setting a size.** `400 GB+` (below)
assumes more free space than a 512GB SSD often has left once Proxmox's own
install and the `local` storage (ISOs, templates, backups) are accounted for.
Check the real ceiling first:

```bash
pvesm status
```

Look at `local-lvm`'s `Total` (in KiB — divide by ~1,048,576 for GiB). Set the
container's disk to comfortably *under* that number, not over it — a resize
that exceeds the pool's actual physical capacity either fails outright or, on
a thin pool, silently over-provisions, which is worse. On a 512GB SSD this
number is often closer to **~340–350 GB**, not 400.

Now **Datacenter → your node → Create CT**. Settings that matter:

| Field                  | Value                                                                | Why                                                                                |
| ---------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Template               | `debian-12-standard` (now available since you downloaded it above) |                                                                                    |
| **Unprivileged** | **Unchecked** (= privileged)                                   | Docker-in-LXC is far less painful privileged, and`/dev/dri` passthrough needs it |
| Disk                   | Whatever you calculated above (e.g. `340` GB), not the wizard's tiny default | Media lives here. The wizard defaults to **8 GB** if you don't change it — easy to miss, and 8 GB won't even hold Docker's own images |
| Cores                  | 8                                                                    | The 5800H has 8C/16T; the host needs almost nothing                                |
| Memory                 | 12288 MB (12 GB)                                                     | Leaves ~4 GB for the Proxmox host itself                                           |
| Swap                   | 2048 MB                                                              |                                                                                    |
| Network → IPv4         | **DHCP** — set explicitly, don't leave it on "None"                  | Leaving this at the wizard's default of **None** means Proxmox never writes network config into the container at all — not "DHCP fails," but "networking is never attempted." The container boots with `eth0` present but permanently `state DOWN` |
| Nesting                | **Enabled**                                                    | Under**Options → Features**. Docker will not run without it                 |

⚠ **Before touching any `.conf` file on the host, confirm your container's real
ID:**

```bash
pct list
```

`<CTID>` appears in every command below — it means **the number `pct list`
just showed you**, not a literal string, and not `100` or `101` just because
those happen to be common first values. This isn't optional caution: running
`echo ... >> /etc/pve/lxc/<CTID>.conf` with the brackets left in fails loudly
(good — that's the point). But typing in a *wrong number that happens to
exist* wouldn't fail at all — it would **silently create or edit a container
you didn't mean to touch**, with no error to warn you. That's exactly what
happened during this guide's own testing: a stray placeholder number created
a phantom, permanently-broken container with no disk attached. If you ever
end up with one, it's safe to delete:

```bash
rm /etc/pve/lxc/<wrong-id>.conf
```

With your real `<CTID>` confirmed, add the GPU passthrough from the **host**
shell. `226:*` is a blanket grant for DRM's whole device class, not tied to
any specific number, so `<CTID>` is the only substitution these two lines
need — the `<GID>` you noted in step 4 isn't used here at all, only much
later in [step 10](#10-verify-hardware-transcoding-actually-works), and only
if it turns out to be needed:

```bash
echo "lxc.cgroup2.devices.allow: c 226:* rwm" >> /etc/pve/lxc/<CTID>.conf
echo "lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir" >> /etc/pve/lxc/<CTID>.conf
```

Start the container and open its console. Verify networking actually came up
before moving on — this is the single most common snag in this whole guide:

```bash
ip addr show
```

`eth0` should show `state UP` with a real `192.168.x.x` address. If it shows
`state DOWN` with no address, go back to **your node → `<CTID>` → Network**
in the UI (or `pct set <CTID> -net0 ...,ip=dhcp` from the host) and set IPv4
explicitly — then `pct reboot <CTID>` and check again.

---

## 6. Install Docker inside the LXC

⚠ **Run this in the three stages below, not as one paste.** Docker's official
install is normally one block, but each stage here depends on the previous one
actually succeeding — if `curl` fails to install, every step after it fails
too, with error messages that don't obviously point back at the real cause.
Confirm each stage worked before running the next.

**Stage 1 — base tools:**

```bash
apt update
apt install -y ca-certificates curl git
```

If this fails with GPG/signature errors mentioning `download.docker.com`, a
`docker.list` file already exists from an earlier attempt, pointing at a key
that was never actually downloaded — a chicken-and-egg problem where the
broken repo entry blocks `apt update`, which blocks installing `curl`, which
is what you needed to fix the repo entry in the first place. Clear it and
retry this stage:

```bash
rm -f /etc/apt/sources.list.d/docker.list
```

**Stage 2 — Docker's GPG key**, now that `curl` actually exists:

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
ls -la /etc/apt/keyrings/docker.asc
```

That `ls` must show a real file with content (a few KB) before you continue —
if it says "No such file," `curl` itself failed silently upstream (check
stage 1 actually completed) rather than something wrong with this stage.

**Stage 3 — the repo entry and Docker itself:**

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Confirm the GPU is visible **inside** the container:

```bash
apt install -y vainfo
vainfo
ls -ln /dev/dri
```

Both should work the same as they did on the host. If `/dev/dri` is missing
here, the two `lxc.*` lines in step 5 didn't apply — stop and fix that.

---

## 7. Get the stack

```bash
git clone https://github.com/<your-username>/automatic-downloader.git /opt/downloader
cd /opt/downloader
cp .env.example .env
```

Create the media folders — **all under one path**, or hardlinks break and
every import silently becomes a slow full copy:

```bash
mkdir -p /mnt/media/{torrents,usenet}/{complete,incomplete} /mnt/media/library/{movies,tv}
chown -R 1000:1000 /mnt/media
```

---

## 8. Configure `.env`

```ini
MEDIA_ROOT=/mnt/media
TZ=Europe/Amsterdam

# AMD/Intel iGPU
GPU_VENDOR=amd
COMPOSE_PATH_SEPARATOR=:
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu-amd.yml

# Headless box -- without this, no web UI is reachable from your laptop
BIND_ADDRESS=0.0.0.0
```

Plus your VPN credentials (see [ONE-TIME-SETUP.md](ONE-TIME-SETUP.md)
step 1), or skip them and use `docker-compose.novpn.yml` as your base in
`COMPOSE_FILE` instead.

⚠ `BIND_ADDRESS=0.0.0.0` puts Sonarr/Radarr/Prowlarr/qBittorrent on your LAN
with no authentication in front of them. Fine on a home network you control.
**Never port-forward these from your router.**

Because `COMPOSE_FILE` is set, every command below is a plain
`docker compose …` with no `-f` flags — it picks up both files automatically.

---

## 9. Run the normal setup

From here it's the standard flow in
[ONE-TIME-SETUP.md](ONE-TIME-SETUP.md) — steps 3 onward, unchanged:

```bash
docker compose up -d
docker logs -f gluetun            # wait for "Initialization Sequence Completed"
docker logs qbittorrent 2>&1 | grep "temporary password"
```

Set a permanent qBittorrent password in its UI at
`http://<lxc-ip>:8090`, put it in `.env`, then:

```bash
docker compose run --rm setup
```

Add your indexers in Prowlarr (`http://<lxc-ip>:9696`), then re-run
`docker compose run --rm setup` once more.

---

## 10. Verify hardware transcoding actually works

This is the step most likely to silently fail. `GPU_VENDOR=amd` sets Jellyfin
to VAAPI, and the compose override passes `/dev/dri` in — but the container
still has to be *able to open* the device.

```bash
docker exec jellyfin ls -ln /dev/dri
```

**Compare the GID in the fourth column against the one you noted in step 4.**
The device's group ownership comes from the host's numeric GID, which usually
does *not* match the container distro's own `render`/`video` group. When they
don't match, Jellyfin (running as UID 1000) cannot open the device, silently
falls back to CPU, and everything *looks* fine until 4K playback pins all
8 cores.

If they differ, add the numeric GID to the jellyfin service — create
`docker-compose.override.yml` next to the others (Compose picks it up
automatically, no `COMPOSE_FILE` change needed):

```yaml
services:
  jellyfin:
    group_add:
      - "<GID>"      # replace with the actual number, e.g. "993" -- whatever
                      # `ls -ln /dev/dri` printed for YOUR machine, not this example
```

```bash
docker compose up -d jellyfin
```

Then play something that must transcode (a 4K HEVC file to a browser is
reliable) and check:

```bash
docker exec jellyfin ps aux | grep ffmpeg
```

The ffmpeg command line should contain `-hwaccel vaapi`. In the Jellyfin UI,
**Dashboard → Playback** will also show the active session as
`Transcoding (VAAPI)` rather than plain `Transcoding`.

⚠ **Tone mapping stays off by default on VAAPI** — it needs an OpenCL runtime,
and without one HDR transcodes *fail* instead of falling back. Once normal
transcoding is confirmed working, enable it (**Dashboard → Playback → Enable
Tone mapping**) and specifically test a 4K HDR file. If that breaks, turn it
back off.

---

## 11. Optional: Tailscale, for access from outside the house

`BIND_ADDRESS=0.0.0.0` covers your home network. Tailscale covers everywhere
else — an encrypted private mesh between your own devices, with **no router
ports opened and nothing exposed to the public internet**.

**Install it in the LXC, not as a Docker service.** Reasons, in order:

- One install gives the whole LXC a tailnet IP, and *every* service is
  immediately reachable there on its normal port — `:8096`, `:5055`, `:7878`,
  all of them. No per-service configuration.
- The gluetun pattern (`network_mode: service:tailscale`) **cannot** work here:
  a container can only be in one network namespace, and qBittorrent, Prowlarr,
  and Byparr are already in gluetun's. Trying to add Tailscale that way would
  force a choice between VPN and tailnet for exactly the containers that need
  the VPN most.
- Tailscale in Docker needs the same TUN device access *plus* container
  capabilities — strictly more moving parts for the same result.

First give the LXC access to the TUN device. From the **Proxmox host** shell
(`<CTID>` = the same real container ID from `pct list` you used in step 5 —
confirm it again with `pct list` if it's been a while, don't assume you
remember it correctly), with the container stopped:

```bash
echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> /etc/pve/lxc/<CTID>.conf
echo "lxc.mount.entry: /dev/net dev/net none bind,create=dir" >> /etc/pve/lxc/<CTID>.conf
```

Start it again, then **inside the LXC**:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

It prints a URL — open it, sign in, and the LXC joins your tailnet. Then:

```bash
tailscale ip -4
```

Every service is now at `http://100.x.y.z:<port>` from any device signed into
the same Tailscale account, anywhere.

Two follow-ups worth doing:

- **Jellyfin:** add `100.64.0.0/10` to **Dashboard → Networking → LAN
  Networks**, or it treats tailnet clients as remote and rejects them.
- **MagicDNS** (in the Tailscale admin console) lets you use the machine name
  instead of the IP.

You can also install Tailscale on the **Proxmox host itself** as a second
tailnet node, which gets you the Proxmox web UI (`:8006`) remotely too. That's
independent of the LXC's install — useful if you ever need to fix the box while
away from home.

> **No reverse proxy needed for any of this.** A proxy (Caddy/nginx +
> Let's Encrypt) solves a different problem: letting people who *don't* have
> Tailscale reach a service over the public internet. If everyone using
> Jellyseerr will install Tailscale, skip the proxy entirely — and if you do
> want browser-trusted HTTPS on the tailnet, `tailscale serve` provides it
> without one.

---

## 12. Alternative: your router's own VPN server

Most Asus routers (and plenty of others) have a built-in **VPN Server**
(OpenVPN, and WireGuard on newer firmware) under **VPN → VPN Server** in the
admin UI. It does the same job as Tailscale above — encrypted remote access
with nothing exposed as plain HTTP — with no third-party account or
coordination service involved at all. Once connected, your device is on the
home LAN directly, reaching everything, not just this stack.

### Check this before doing anything else

This only works if your ISP gives you a real public IP. Many now use **CGNAT**
(Carrier-Grade NAT), where you don't have one — and if that's the case, no
amount of router configuration makes port forwarding work. Compare:

```bash
curl -s https://icanhazip.com
```

against the WAN IP your router itself reports (Asus: **Network Map** on the
main dashboard, or **Advanced Settings → WAN → Internet Connection**).

- **Match** → real public IP. Continue below.
- **Different** — especially something in `100.64.0.0`–`100.127.255.255`, or
  any other address that looks private — **you're behind CGNAT.** Stop here;
  use Tailscale instead. There is no router setting that fixes this, because
  the limitation is on your ISP's network, not yours.

### If you have a real public IP

1. **VPN → VPN Server** → enable **OpenVPN** (or **WireGuard** if your
   firmware offers it — noticeably lighter on the router's CPU, prefer it if
   available)
2. Forward the matching port (OpenVPN default `1194/UDP`) to the router
   itself — most Asus firmware does this automatically when you enable the
   VPN server, but confirm it under **WAN → Virtual Server / Port Forwarding**
3. Set up **DDNS** (**WAN → DDNS**, free `*.asuscomm.com` hostname) — your
   home IP changes periodically, and this keeps clients able to find it
   without you updating anything by hand
4. Export a client profile per device (**VPN Server → OpenVPN → Export**),
   install a standard OpenVPN or WireGuard client, import it

Two things worth knowing before you commit to this over Tailscale:

- **Router CPU does the encryption.** OpenVPN in particular can bottleneck
  older/weaker router chipsets — some cap out around 20–50 Mbps even on a
  gigabit line. WireGuard is much lighter if your model supports it.
- **Broader access, more manual setup per device.** A connected client reaches
  your whole LAN, not just this stack, and there's no equivalent of
  Tailscale's one-command onboarding — each device needs its own exported
  profile and a VPN client app installed.

Forwarding the VPN port itself is a reasonable thing to expose, unlike
forwarding Jellyseerr's port directly. OpenVPN/WireGuard require a valid
cryptographic key before responding to anything, so there's no scannable
login page sitting on the open internet the way a bare app port would be.

---

## 13. Snapshots

The one genuine advantage Proxmox has over bare Debian here, and it's free:

**Container → Snapshots → Take Snapshot**, before every upgrade. Rolling back
is one click. Take one now that everything works.

---

## Everyday operation

The box is headless from here on:

```bash
ssh root@<lxc-ip>
cd /opt/downloader
docker compose ps
docker compose logs -f sonarr
docker compose pull && docker compose up -d      # update everything
```

| Service        | URL                        |
| -------------- | -------------------------- |
| Jellyfin       | `http://<lxc-ip>:8096`   |
| Jellyseerr     | `http://<lxc-ip>:5055`   |
| Radarr         | `http://<lxc-ip>:7878`   |
| Sonarr         | `http://<lxc-ip>:8989`   |
| Prowlarr       | `http://<lxc-ip>:9696`   |
| Bazarr         | `http://<lxc-ip>:6767`   |
| SABnzbd        | `http://<lxc-ip>:8080`   |
| qBittorrent    | `http://<lxc-ip>:8090`   |
| Proxmox itself | `https://<host-ip>:8006` |

---

## Troubleshooting

| Symptom                              | Cause / fix                                                                                                              |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `apt update` fails with `Temporary failure resolving ...` inside the LXC | Networking never came up. Check `ip addr show` — if `eth0` is `state DOWN` with no address, IPv4 was left on "None" in the container's Network settings instead of "DHCP". Fix, then `pct reboot <CTID>` |
| `curl: command not found` right after installing it, or `NO_PUBKEY` on `download.docker.com` | A stale `docker.list` from an earlier failed attempt is blocking `apt update`, which blocks installing `curl` in the first place. `rm -f /etc/apt/sources.list.d/docker.list`, then redo [step 6](#6-install-docker-inside-the-lxc) in its three stages, confirming each one before the next |
| Docker won't start in the LXC        | Nesting not enabled (**Options → Features**), or the container is unprivileged                                    |
| `/dev/dri` missing inside the LXC  | The two`lxc.*` lines in step 5 are absent, on the wrong container ID (check with `pct list`), or the container wasn't restarted after adding them |
| A `.conf` file you edited shows almost nothing (`pct config <CTID>` only lists the `lxc.*` lines you added, no `arch:`/`net0:`/`rootfs:`) | You appended to an ID that didn't exist yet — `echo ... >>` created a new, empty container instead of erroring. Run `pct list` to find your real container, redo the `lxc.*` lines there, and `rm` the phantom `.conf` file (safe — it has no disk attached) |
| `vainfo` fails on the host         | `mesa-va-drivers` not installed, or the kernel doesn't see the iGPU. Fix on the host before touching the container     |
| Transcoding works but pins the CPU   | The`/dev/dri` GID mismatch from step 10. Add `group_add:`                                                            |
| HDR content fails to play            | Tone mapping on without OpenCL. Turn it off (**Dashboard → Playback**)                                            |
| Jellyfin container won't start       | The NVIDIA`deploy:` block is still active — `COMPOSE_FILE` isn't set, or `docker-compose.gpu-amd.yml` isn't in it |
| Web UIs unreachable from your laptop | `BIND_ADDRESS=0.0.0.0` missing from `.env`, then `docker compose up -d` to re-apply                                |
| Imports slow, disk filling up        | Downloads and library aren't under one`MEDIA_ROOT`, so hardlinks are failing                                           |

Full stack troubleshooting: [README.md](../README.md#troubleshooting)
