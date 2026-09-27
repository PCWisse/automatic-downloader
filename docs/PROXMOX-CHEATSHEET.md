# Proxmox cheatsheet

The commands you actually need once the stack is running, what each one does,
and why. [PROXMOX.md](PROXMOX.md) is how you get here; this is what you keep
open afterwards.

Every command runs on the **Proxmox host** (Shell in the web UI, or
`ssh root@<host-ip>`), not inside the container, unless it says otherwise.

Same rule as PROXMOX.md: anything in `<angle-brackets>` is a placeholder.
Replace the whole token, brackets included — bash refuses to run it otherwise,
which is the point. Find your real `<CTID>` with `pct list` first.

---

## The four ideas that explain everything below

**Containers vs VMs.** Proxmox runs two kinds of guest. A **VM** emulates a
whole computer with its own kernel, managed with `qm`. A **container (CT /
LXC)** shares the host's Linux kernel — lighter, but Linux-only — managed with
**`pct`** (Proxmox Container Toolkit). This stack lives in a container, so
`pct` is the one you'll use.

**Two storages.** A default install gives you:

| Storage     | What it is                              | Holds                            |
| ----------- | --------------------------------------- | -------------------------------- |
| `local`     | A plain folder, `/var/lib/vz`           | ISOs, CT templates, **backups**  |
| `local-lvm` | An **LVM-thin pool** (`pve/data`)       | The virtual disks of your guests |

**Thin provisioning.** The container's disk says 340 GB, but `local-lvm`
doesn't reserve 340 GB up front — it hands out real space only as data is
written. That lets you promise more than you have. If it all actually gets
used, the pool runs out, and then **every disk on it starts failing writes at
once**. This is the failure mode to watch for on a download box.

**Snapshots cost space over time.** A snapshot starts almost free. But every
block the container changes afterwards has to be kept twice — the new version
for the container, the old one for the snapshot. On a box that downloads
hundreds of GB a week, a snapshot left lying around quietly eats the pool.

---

## Everyday container commands

| Command                        | What it does                                                                   |
| ------------------------------ | ------------------------------------------------------------------------------ |
| `pct list`                     | All containers, their IDs and status. Run this first, always                   |
| `pct status <CTID>`            | Running or stopped?                                                            |
| `pct start <CTID>`             | Start it                                                                       |
| `pct shutdown <CTID>`          | **Clean** shutdown — services get to stop properly                             |
| `pct stop <CTID>`              | **Hard** stop — pulling the plug. Only when `shutdown` hangs                   |
| `pct reboot <CTID>`            | Clean restart                                                                  |
| `pct enter <CTID>`             | Root shell **inside** the container. `exit` to leave                           |
| `pct exec <CTID> -- <command>` | Run one command inside it, e.g. `pct exec <CTID> -- df -h`                     |
| `pct config <CTID>`            | Its settings: CPU, RAM, disk, network, the `lxc.*` lines from step 5           |
| `pct set <CTID> --memory 8192` | Change a setting (here: RAM to 8 GB). Most need a restart to apply             |
| `pct df <CTID>`                | How full the container's disks are, from the host's point of view              |
| `pct push <CTID> <file> <dest>` | Copy a file from the host into the container                                  |
| `pct pull <CTID> <src> <file>` | Copy a file out of the container to the host                                   |

The config file itself is `/etc/pve/lxc/<CTID>.conf` — plain text, the same
thing `pct config` prints.

**Chaining commands.** `pct start <CTID> && pct fstrim <CTID>` runs the second
command only if the first **succeeded** — no point trimming a container that
didn't start. `||` is the opposite (only if the first **failed**); a plain `;`
runs both regardless.

---

## Disk space — check this before it bites

| Command                          | What it tells you                                                                        |
| -------------------------------- | ---------------------------------------------------------------------------------------- |
| `pvesm status`                   | Every storage and its `%` used. **The one to glance at regularly**                       |
| `lvs`                            | LVM volumes. `Data%` on the `data` line is how full the thin pool really is             |
| `vgs`                            | The volume group. `VFree` is unallocated space you can still give the pool               |
| `df -h`                          | The host's own filesystems (`/`, where `local` lives)                                   |
| `lvextend -l +100%FREE pve/data` | Grows the thin pool into all of `VFree`. Safe, doesn't touch data, can't be shrunk back |

Keep `local-lvm` under roughly **80–85%**. Above 90% you're one big download
away from the failure described in [Troubleshooting](#troubleshooting).

### `pct fstrim <CTID>` — give deleted space back to the pool

When the container deletes a file, ext4 marks those blocks free **in its own
bookkeeping only**. The thin pool underneath never hears about it and still
counts them as used. So after Sonarr/Radarr import and clean up a few hundred
GB, the container thinks it has room — and the pool is still full.

`fstrim` walks the filesystem and sends a "discard" for every free block, and
the pool takes them back. The first run on this box reclaimed **73.8 GB**.

- The container must be **running** (it trims what's mounted).
- Safe any time — it never touches data that's in use.
- Blocks a snapshot still needs stay allocated. Delete old snapshots first.

**Make it automatic** — pick one:

- **Weekly trim** (simplest). On the host:
  ```bash
  echo '0 3 * * 0 root /usr/sbin/pct fstrim <CTID>' > /etc/cron.d/fstrim-ct
  ```
  Runs every Sunday at 03:00.
- **Trim on delete**: **Container → Resources → Root Disk → Edit → Mount
  options → `discard`**, then restart the container. Space goes back the
  moment a file is deleted.

---

## Snapshots

[Step 13](PROXMOX.md#13-snapshots) says take one before every upgrade — do,
but **delete it once the upgrade has proven fine** (see
[the idea above](#the-four-ideas-that-explain-everything-below)).

| Command                            | What it does                                                    |
| ---------------------------------- | --------------------------------------------------------------- |
| `pct listsnapshot <CTID>`          | List them                                                       |
| `pct snapshot <CTID> <name>`       | Take one, e.g. `pct snapshot <CTID> before-v2-upgrade`          |
| `pct rollback <CTID> <name>`       | ⚠ Go back to it. **Everything since is gone**                   |
| `pct delsnapshot <CTID> <name>`    | ⚠ Delete it. Permanent — and the fastest way to free pool space |

**A snapshot is not a backup.** It lives on the same disk as the container. If
that disk dies, the snapshot dies with it.

---

## Backups (the real kind)

| Command                                                                | What it does                                                   |
| ---------------------------------------------------------------------- | -------------------------------------------------------------- |
| `vzdump <CTID> --storage local --mode snapshot --compress zstd`        | Full backup to `local`, while the container keeps running      |
| `ls -lh /var/lib/vz/dump/`                                             | Where those backup files end up                                |
| `pct restore <new-CTID> /var/lib/vz/dump/<file>.tar.zst --storage local-lvm` | Restore as a **new** container, leaving the original alone |

For a schedule, use the web UI: **Datacenter → Backup → Add**. A backup on the
same SSD protects against mistakes, not against the SSD dying — copy the
important ones somewhere else.

Worth knowing: the media library is most of the disk and doesn't need backing
up (it can be re-downloaded). What's painful to lose is the stack's config —
`.env`, `gluetun-auth.toml`, and each app's config folder.

---

## When something breaks

| Command                                              | What it tells you                                                                                          |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `lxc-start -n <CTID> -F -l DEBUG -o /tmp/lxc-<CTID>.log` | Starts the container in the foreground with a full debug log. `grep -iE "error\|fail" /tmp/lxc-<CTID>.log` to find the real reason a start failed |
| `journalctl -u pve-container@<CTID> -n 50`           | Last 50 log lines of the container's start service                                                         |
| `dmesg -T \| tail -30`                               | Kernel messages with readable times. Disk and thin-pool errors show up here first                          |
| `journalctl -p err -b`                               | Every error since the host last booted                                                                     |
| `pct fsck <CTID>`                                    | Check and repair the container's filesystem. Container must be **stopped**                                 |
| `pveversion -v`                                      | Exact Proxmox versions — paste this when asking for help online                                           |

### Reading `pct fsck` results

fsck ("file system check" — Linux's `chkdsk`) exits with a code, and Proxmox
reports anything non-zero as `failed`. It isn't always:

| Exit code | Meaning                                              |
| --------- | ---------------------------------------------------- |
| `0`       | Nothing wrong                                        |
| `1`       | **Errors found and fixed** — fine, start it          |
| `4`+      | Errors it could **not** fix — stop and investigate   |

Lines like `extent tree could be narrower. IGNORED.` are optimisation hints,
not damage.

---

## Keeping the host updated

| Command                            | What it does                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------------------- |
| `apt update && apt dist-upgrade`   | Update Proxmox. **`dist-upgrade`**, not `apt upgrade` — plain upgrade can leave Proxmox half-updated |
| `pveam update && pveam available`  | Refresh and list downloadable container templates                                     |

Updating the stack inside the container is separate — see
[Everyday operation](PROXMOX.md#everyday-operation).

---

## Troubleshooting

| Symptom | Cause / fix |
| ------- | ----------- |
| Container won't start: `run_buffer: 569 Script exited with status 32` / `Failed to run lxc.hook.pre-start` | Before booting, Proxmox mounts the container's disk; `32` is `mount` failing. Run the `lxc-start ... -l DEBUG` command above — if the log says `can't read superblock`, check `lvs`. `Data%` at **100** on `data` means the thin pool is full (see below) |
| `lvs` shows `data` at 100%, `dmesg` says `out-of-data-space (error IO) mode` | The thin pool filled up and now rejects every write. Free space in this order: **1.** `pct delsnapshot` any snapshot you don't need, **2.** `lvextend -l +100%FREE pve/data` if `vgs` shows `VFree`, **3.** `pct fsck <CTID>` (exit `1` is fine), **4.** `pct start <CTID> && pct fstrim <CTID>`. Then set up [automatic trimming](#pct-fstrim-ctid--give-deleted-space-back-to-the-pool) so it doesn't happen again |
| `WARN: Systemd 252 detected. You may need to enable nesting.` on start | Proxmox's generic advice for Debian 12 containers. Harmless here if the stack runs — [step 5](PROXMOX.md#5-create-the-lxc) covers nesting |
