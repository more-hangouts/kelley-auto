# VPS Hardening — Applied 2026-04-26

Operational record of the hardening pass executed against the production VPS (`bellasxv`, 50.28.105.191, Liquid Web, Ubuntu 24.04.4 LTS). This document is the source of truth for what is actually on the box — deviations from the original brief are noted inline.

**Status:** Steps 1, 2, 3, 4, 5, 6, 8 applied. Steps 7 (UptimeRobot) and 9 (Backblaze B2 off-server backups) deferred — both require manual web signups not done yet. End-of-phase validation: 30/30 checks pass.

---

## What this box looks like now

| Layer | State |
|---|---|
| RAM | 4 GB (brief assumed 6 GB — actual is 4) |
| Swap | 4 GB file at `/swapfile`, swappiness=10. Pre-existing 2 GB partition swap on `/dev/vda2` was disabled in `/etc/fstab`. |
| API | `bellas-xv-api.service` capped at MemoryMax=1G / MemoryHigh=768M, StartLimitBurst=5/60s |
| OS patching | `unattended-upgrades` enabled, security archive only, auto-reboot 04:00 if no users logged in, mail-on-change to `luis@morehangouts.com` |
| SSH | Key-only, root login off, `AllowUsers luis`, MaxAuthTries 3, ClientAliveInterval 300, LogLevel VERBOSE. Single key in `authorized_keys` (ed25519, `luis@morehangouts.com`). |
| Kernel | Network sysctls hardened — strict rp_filter, no source-routing, no ICMP redirects, SYN cookies, log_martians, tcp_max_syn_backlog 2048 |
| Logs | journald capped at 500M / keep 1G free; app logs rotate weekly, 8 generations, gzipped via delaycompress |
| Mail | Postfix loopback-only, gmail accepts delivery (250 OK) despite no SPF/DKIM — likely lands in spam |
| Health checks | `/home/luis/bellas_xv/scripts/health_check.sh` runs daily at 08:00 America/Chicago via cron, alerts to email + appends to `logs/health_check.log` |

---

## Where the brief was wrong or incomplete

These are the corrections to fold into the brief's next revision:

### 1. RAM assumption was off by 2 GB
Brief says 6 GB; actual `free -h` shows 4.0 GiB. Doesn't change Step 1's 4 GB swap target or Step 2's 1 GB MemoryMax — but the "Memory Budget" table is wrong. Postgres + OS headroom is ~3 GB, not ~5 GB.

### 2. Step 1 — pre-existing 2 GB swap partition
Box came with `/dev/vda2` as a 2 GB swap partition (UUID `cf84133a-...`). The brief assumed no existing swap. Procedure used: `swapoff /dev/vda2`, comment its line in `/etc/fstab`, then create the 4 GB swapfile per the brief. Partition is unused but not repartitioned — reclaim later if disk gets tight. Backup: `/etc/fstab.bak.pre-hardening`.

### 3. Step 2 — `StartLimitIntervalSec` / `StartLimitBurst` belong in `[Unit]`, not `[Service]`
The brief puts them in `[Service]`. systemd accepts them there for backward compat but **silently ignores the interval** (treats as default 10s instead of 60s). `systemctl show ... -p StartLimitIntervalUSec` confirms. Override file at `/etc/systemd/system/bellas-xv-api.service.d/override.conf` was written with both directives in `[Unit]`.

### 4. Step 4 — **silent-failure precondition the brief is missing**
The brief assumes drop-ins under `/etc/ssh/sshd_config.d/*.conf` are read by sshd. **They aren't, unless `Include /etc/ssh/sshd_config.d/*.conf` exists in the main `sshd_config`.** This box's `/etc/ssh/sshd_config` is an ancient template (still has deprecated `UsePrivilegeSeparation`, `RSAAuthentication`, etc.) with **no Include line**. PRODUCTION_DEPLOY.md must have edited the main config directly, which is why `PermitRootLogin no` worked.

The danger: `sshd -t` validates a drop-in just fine even when sshd will never read it. The override looks correct, the restart succeeds, the hardening doesn't exist.

**Mitigation applied:** added 3-line block at top of `/etc/ssh/sshd_config`:
```
# Include drop-in config files (added 2026-04-26 by hardening pass).
# Placed BEFORE main directives so first-occurrence-wins makes drop-ins authoritative.
Include /etc/ssh/sshd_config.d/*.conf
```
Backup: `/etc/ssh/sshd_config.bak.pre-include.20260426`. Path B (replace ancient main config with modern Ubuntu template) was deferred to a later change window.

**Brief edit needed:** add a precondition to Step 4 that runs `grep -q '^Include /etc/ssh/sshd_config.d/' /etc/ssh/sshd_config`. If absent, add it before applying drop-ins.

### 5. Step 4 — `restart` vs `reload`
Brief says `systemctl restart ssh`. Reload (SIGHUP) works for re-reading config — including new `Include` directives — and **preserves existing connections**. Restart kills inflight sessions. Used reload here. The Claude Code agent in this session is itself running inside an SSH session (visible as a second `sshd: luis [priv]` from the same source IP); restart would kill it mid-procedure. Reload is the safer default.

### 6. Step 4 — `at` is not installed on Ubuntu minimal server
Brief's auto-revert pattern uses `at`. Not present on this image. Used `systemd-run --on-active=15min --unit=sshd-auto-revert` for the same semantics with zero install. Cancel via `systemctl stop sshd-auto-revert.timer`.

### 7. Step 4 — pre-existing keys in `authorized_keys`
Found three keys before our session — `root@sta-jenkins-slave-03.sourcedns.com` and `JM831T` (both unaccounted for, dropped) plus the user's `luis@morehangouts.com` (kept). Also `/root/.ssh/authorized_keys` existed with content despite `PermitRootLogin no` — wiped. End state: single key in `~luis/.ssh/authorized_keys`, pre-cleanup snapshot at `authorized_keys.pre-cleanup.20260426`.

### 8. Step 6 — log files were root-owned, breaking logrotate-as-user
systemd opens files for `StandardOutput=append:` as PID 1 (root) **before** forking and dropping to `User=luis`. The service writes via the inherited fd regardless of filesystem ownership, but the files themselves get created root-owned. logrotate's `su luis luis` then can't `copytruncate` them ("Permission denied").

**Fix applied:** `chown luis:luis /home/luis/bellas_xv/logs/*.log`. Service kept writing through its existing root-opened fd (no service restart required), and logrotate worked on the next run. Future restarts open the existing files (still luis-owned) — no regression.

### 9. Step 8 — `/home/luis/projects/bellas_xv` path doesn't exist on this box
Brief inherits a `projects/` subfolder convention from a different project. Actual layout is `/home/luis/bellas_xv`. All paths substituted.

### 10. Step 8 — system timezone is UTC, not local
Brief schedules cron at `0 8 * * *` assuming local time. System tz here is UTC. Crontab uses `CRON_TZ=America/Chicago` to make 8 AM mean 8 AM San Antonio time (= 13:00 UTC during CDT).

### 11. Step 8 — postfix listens on all interfaces by default
Out-of-the-box postfix has `inet_interfaces = all`. UFW blocks port 25 externally so this isn't reachable, but defense-in-depth: tightened to `loopback-only` since we only need outbound. Verified with `ss -tlnp | grep ':25 '`.

### 12. Step 8 — file-log fallback added to health_check.sh
Brief's script only emails. If mail breaks silently (residential block list, deferred queue, postfix dead) you'd never know. Added `LOG_FILE=/home/luis/bellas_xv/logs/health_check.log` — alerts append a full block, "ok" runs append a single timestamped line. Tail it to confirm the cron is firing even when no mail arrives.

---

## Procedural lessons from this run

- **Test the rollback path under load before you need it.** Mid-Step-4 we did a full `cp .bak / reload` cycle from the escape session and verified config reverted byte-for-byte without dropping connections. That dry run is what made the second attempt a non-event.
- **Console must be tested, not assumed.** "Yes the option exists in their UI" is not "yes I clicked into it and saw a prompt." Liquid Web's KVM console was confirmed live before any sshd change.
- **The escape session must be a real workstation SSH session, not the Claude Code terminal.** `whoami` returning `luis` from the agent's terminal proves nothing about whether you can SSH in from your laptop. `who` agreement on both ends is the right test.
- **Belt-and-suspenders auto-revert is cheap.** `systemd-run --on-active=15min` takes one line, gives you a guaranteed rollback even if every human walks away. Default for any sshd-restart-class change.

---

## What was deferred

- **Step 7 — UptimeRobot.** Manual signup (account + 2 monitors). Deferred to a follow-up session. Internal Step-8 cron is independent and already covers most of what UptimeRobot would, except external-perspective uptime.
- **Step 9 — Backblaze B2 off-server backups.** Manual signup (account + bucket + scoped app key). Daily local Postgres dumps continue to land in `~/backups/` (set up in PRODUCTION_DEPLOY.md). Off-server is the layer that protects against complete VPS loss; pending until B2 credentials exist.
- **Path B — replace ancient main `sshd_config` with modern Ubuntu template.** The deprecated-option warnings on every sshd parse are pre-existing and not new to this pass. Cleanup deserves its own change window with its own validation. Ticket-worthy but not urgent.

---

## Files changed / created on the box

```
/etc/fstab                                           (commented partition swap, added /swapfile)
/etc/sysctl.d/99-swap.conf                           (new — swappiness, vfs_cache_pressure)
/etc/sysctl.d/99-network-hardening.conf              (new — Step 5 hardening)
/etc/systemd/system/bellas-xv-api.service.d/override.conf   (new — memory caps + restart limits)
/etc/systemd/journald.conf.d/size.conf               (new — 500M/1G journal cap)
/etc/logrotate.d/bellas-xv                           (new — weekly app log rotation)
/etc/apt/apt.conf.d/52unattended-upgrades-local      (new — reboot + mail config)
/etc/ssh/sshd_config                                 (modified — Include line at top)
/etc/ssh/sshd_config.d/99-bellas-xv-hardening.conf   (new — MaxAuthTries 3, AllowUsers luis, etc.)
/etc/postfix/main.cf                                 (modified — inet_interfaces=loopback-only)
/swapfile                                            (new — 4 GB swap)
/home/luis/bellas_xv/scripts/health_check.sh        (new — committed to repo)
/home/luis/.ssh/authorized_keys                      (cleaned — single key only)
/home/luis/bellas_xv/logs/*.log                     (chowned luis:luis)
crontab (luis)                                       (added — health_check.sh, CRON_TZ=America/Chicago)
```

Pre-change snapshots retained on the box:

```
/etc/fstab.bak.pre-hardening
/etc/ssh/sshd_config.bak.pre-include.20260426
/home/luis/.ssh/authorized_keys.pre-cleanup.20260426
```

---

## End-of-phase validation results

```
Step 1: 4G swapfile is enabled and persistent              ✓
Step 1: vm.swappiness=10                                   ✓
Step 2: bellas-xv-api MemoryMax=1G                         ✓
Step 2: bellas-xv-api MemoryHigh=768M                      ✓
Step 2: StartLimitBurst=5 effective                        ✓
Step 3: unattended-upgrades timer enabled                  ✓
Step 3: 52unattended-upgrades-local present with email     ✓
Step 4: Include line in main sshd_config                   ✓
Step 4: hardening drop-in present                          ✓
Step 4: maxauthtries 3 effective                           ✓
Step 4: allowusers luis effective                          ✓
Step 4: clientaliveinterval 300 effective                  ✓
Step 4: loglevel VERBOSE effective                         ✓
Step 4: /root/.ssh/authorized_keys removed                 ✓
Step 4: luis authorized_keys = 1 line ed25519 morehangouts ✓
Step 5: tcp_syncookies=1                                   ✓
Step 5: rp_filter=1                                        ✓
Step 5: log_martians=1                                     ✓
Step 5: tcp_max_syn_backlog=2048                           ✓
Step 6: journald size cap configured                       ✓
Step 6: logrotate config present                           ✓
Step 6: rotated log files exist (proof rotation worked)    ✓
Step 8: mailutils installed                                ✓
Step 8: postfix loopback-only                              ✓
Step 8: health_check.sh present + executable               ✓
Step 8: cron entry present with America/Chicago tz         ✓
Step 8: health_check.log has 'ok' line from dry run        ✓
Step 8: smoke-test mail accepted by gmail (status=sent)    ✓
API still healthy after all changes                        ✓
All four core services active                              ✓

PASS: 30   FAIL: 0
```
