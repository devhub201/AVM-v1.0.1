<div align="center">

# ⚡ AVM — Legacy Cloud VPS Manager

### `AVM v1.1 "Atlas"` — premium Discord bot for running a VPS hosting brand on LXC / Docker / KVM nodes

Free VPS rewards for **invites & boosts** • **Auto-deploy** • **Anti-mining guard** • **Live node dashboard**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![LXD](https://img.shields.io/badge/LXC%2FLXD-powered-E95420?style=for-the-badge&logo=ubuntu&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-storage-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Status](https://img.shields.io/badge/status-production-00ff88?style=for-the-badge)

</div>

---

## ✨ Features

| | Feature | What it does |
|---|---|---|
| 🎁 | **Invite rewards** | Tracks who invited whom. Fake/new accounts (< 7 days) are ignored, leavers subtract, rejoin farming doesn't count twice. |
| ⚡ | **Booster rewards** | Boost on → credited instantly + DM. Boost off → removed and staff are alerted. |
| 🚀 | **One-click free VPS** | `.claimfree` → pick plan + OS → VPS is built automatically. Failed deploy? Your claim is refunded. |
| ⛏️ | **Mining Guard** | Scans every VPS every 2 min for miners, pool URLs and pool ports. Strike system: **kill → suspend → blacklist**. |
| 🚨 | **Alert channel** | Rich alert embeds with evidence + **Unsuspend / Whitelist / Clear Strikes** buttons. Falls back to a DM if the channel breaks, never silent. |
| 📡 | **Live node status** | One auto-updating message: CPU, RAM, disk, network, fleet size, sparkline history, node health. |
| 🖼️ | **Branding** | Your logo + banner on every embed. Set from `install.sh` or straight from Discord. |
| 🔄 | **Reinstall** | Wipe and rebuild a VPS with a fresh OS, same specs. |
| 🛠️ | **Full VPS management** | Create, resize, snapshot, clone, share, suspend, port-forward and more. |
| 🏷️ | **Versioning** | `.version` shows the current AVM version and changelog. |
| 💎 | **Premium UI** | Dropdowns, buttons, progress bars and clean embeds everywhere. |

---

## 🆕 v1.1 — Legacy Cloud

### Upgrade an existing install
```bash
cd legacycloud                # your AVM folder
./install.sh upgrade          # git pull + pip install (Pillow) + restart
./install.sh set brand_name "Legacy Cloud"   # only if you want to force it (old "AxionHost" is migrated automatically)
```
`Legacy Cloud` is now the default brand, shown in every footer, and new VPS containers are named `legacy-cloud-vps-<id>-<n>` (existing containers keep their names).

### 🎞️ Animated emoji pack (all GIF)
The bot draws **43 animated hosting icons** itself and uploads them (`ax_*` names, so it can share the pack with the AxionHost all-in-one bot).
Give the bot **Manage Expressions** and it installs them on start, or run `.emoji install` (`.emoji install overwrite` to redraw, `.emoji list` to preview).
Every ✅ ❌ 🟢 🔴 🖥️ ⚙️ … in every embed, button and dropdown is swapped for its animated twin automatically — no code changes. Until the pack is installed, normal emojis are used.

### 🛰️ Nodes — LXC, Docker, KVM over SSH / Pinggy / SSHX
```text
.node join [pinggy]                    command to run on the new machine (authorises the bot's key; pinggy prints tcp://host:port)
.node add <name> <lxc|docker|kvm> <host | tcp://host:port> [port] [user]
.node list | info | test <name>        live CPU / RAM / disk / guests / ping
.node setpass <name>                   password login (needs sshpass) instead of key
.node sshx <name> <url> / terminal     SSHX web-terminal link, sent to your DMs
.node limit <name> <max_vps>           capacity     .node pool <name> <lxd_pool>
.node default <name|off>               preferred deploy node (otherwise least-loaded)
.node enable|disable <name>            .node board #channel   (live public node board)
.node scan <name>                      anti-mining sweep of the node host
.node exec <name> <cmd> / remove <name> [force]    (main admin)
```
* New VPS (`.claimfree`, `.create`) are built on the best online node with room; everything else (`.manage`, start/stop, SSH access, snapshots, mining guard) follows the VPS to its node automatically.
* **LXC** nodes: everything. **Docker** nodes: create, start/stop/restart/delete, mining guard (disk limit is not enforced by Docker). **KVM** nodes: monitor and start/stop/restart/delete existing VMs (auto-create needs a VM template — not available yet).
* Offline / back-online alerts go to the alert channel; remote hosts get a mining sweep every ~5 minutes.
* Control-only bot (no VPS on this machine)? set `local_lxc` to `0` in config and `.node disable local`.
* Pinggy free tunnels expire after ~60 min — fine for testing, use a Pinggy token or a public IP for permanent nodes. SSHX is an interactive web terminal, so it is a staff terminal link only; SSH/Pinggy do the automation.
* The bot's SSH key lives in `lc_data/` (never commit it). Password logins are stored in `vps.db` in plain text, so prefer key login.

### 📜 VPS creation log
```text
.setlogchannel #vps-creations          every new VPS is logged: owner, plan, OS, node, source, build time + step-by-step build log
.logoff                                disable
```
Only successful creations are posted (not deletes, edits or failures).

### 🏆 More
`.leaderboard` — top inviters and boosters with progress bars toward the biggest invite reward.

---

## 🧱 Requirements

- Ubuntu 22.04 / 24.04 (or Debian 12) server with **root** access
- **LXD** installed and initialised (`lxc list` works)
- Python **3.10+**
- A Discord bot token

---

## 🚀 Quick Start

### 1. Prepare the server

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip curl unzip snapd
snap install lxd
lxd init --auto
lxc list          # must run without errors
```

### 2. Create the Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → **Bot** → copy the token.
2. Enable **Server Members Intent** and **Message Content Intent**.
3. Invite the bot with the `bot` scope and these permissions:
   `Manage Server` • `Manage Roles` • `Send Messages` • `Embed Links` • `Attach Files` • `Read Message History`
4. Drag the bot's role **above** the VPS-owner role so it can assign it.

### 3. Install

```bash
git clone https://github.com/devhub201/AVM-v1.0.1.git legacycloud
cd legacycloud
chmod +x install.sh
./install.sh
```

The installer asks for your token, admin ID, server ID, node IP, node name and logo URL, then builds the virtualenv and installs dependencies.

### 4. Run it 24/7

```bash
sudo ./install.sh service     # systemd service, auto-restart
./install.sh logs             # follow the logs
```

Look for `premium modules ready` in the logs.

### 5. Finish inside Discord

```text
.setalertchannel #mining-alerts
.setstatuschannel #node-status
.branding                       <- shows config + health, fix anything marked ❌
```

---

## 🖼️ Logo & Banner

Pick whichever is easier:

```bash
./install.sh logo   https://raw.githubusercontent.com/<you>/<repo>/main/logo.png
./install.sh banner https://raw.githubusercontent.com/<you>/<repo>/main/banner.png
./install.sh restart
```

or in Discord, attach an image to the message:

```text
.setlogo      (attach image)
.setbanner    (attach image)
```

> ⚠️ Discord CDN links **expire in ~24h**, so the bot refuses them as URLs. When you attach an image, the bot hosts it itself and refreshes it automatically — just **don't delete that message**.

---

## ⚙️ Configuration

`config.json` (created by `install.sh`, see `config.example.json`):

| Key | Description |
|---|---|
| `token` | Discord bot token |
| `brand_name` | Brand shown on embeds (default `AxionHost`) |
| `prefix` | Command prefix (default `.`) |
| `main_admin_id` | Your Discord user ID |
| `guild_id` | Your server ID (invite/boost tracking) |
| `server_ip` | Public IP of the node |
| `node_name` | Name shown on the status dashboard |
| `logo_url` / `banner_url` | Direct image links |
| `alert_channel_id` / `status_channel_id` | Channel IDs (or set via commands) |
| `storage_pool` | LXD storage pool (default `default`) |

Every key also works as an environment variable: `AXION_TOKEN`, `AXION_NODE_NAME`, …

```bash
./install.sh show                       # print config (token hidden)
./install.sh set node_name Mumbai-1     # change one value
./install.sh restart
```

---

## ⛏️ Mining Guard

```text
 detect ──► 1st strike: kill miner + warn user
        ──► 2nd strike: kill + SUSPEND the VPS
        ──► 3rd strike: suspend + BLACKLIST from free claims
```

- **HIGH** — known miner binary, stratum/pool URL, mining algorithm flags, Monero wallet in args → automatic punishment.
- **MEDIUM** — sustained high CPU **and** connections on pool-style ports → alert only, staff decide.
- **Whitelisted VPS / staff-owned VPS** → alert only, never punished.
- Every detection is logged (`.minerlog`) and posted to the alert channel with action buttons.

| Command | Description |
|---|---|
| `.miningguard [on\|off\|status]` | Control the guard |
| `.minerscan <container>` | Manual scan — report only |
| `.minerlog [container]` | Recent detections |
| `.strikes @user` / `.clearstrikes @user` | View / reset strikes |
| `.blacklist add\|remove\|list` | Free-claim blacklist |
| `.whitelist-vps` | Exempt a trusted VPS |

---

## 🎁 Rewards

| Command | Description |
|---|---|
| `.plans` | See all invite & booster plans |
| `.invites [@user]` | Progress bars toward each reward |
| `.claimfree` | Claim and auto-deploy |
| `.dailycredit` / `.credits` | Daily credits |
| `.addinv` `.removeinv` `.addboost` `.removeboost` | Manual adjustments (Admin) |
| `.syncboosts` | Credit current server boosters (Admin) |
| `.resetclaim @user [type]` | Let a user claim again (Admin) |
| `.freeclaims on\|off` | Pause all free claims (Main Admin) |
| `.edit-plans` | Edit plan specs/requirements — saved permanently (Admin) |

---

## 📡 Node Status Dashboard

One message, edited every 60 seconds — no channel spam.

```text
⚡ AxionHost — Node Status
Node-1 • 🟢 Operational

🧠 CPU      ▰▰▰▱▱▱▱▱▱▱ 31%      🧩 Memory  ▰▰▰▰▱▱▱▱▱▱ 42%
💽 Disk     ▰▰▱▱▱▱▱▱▱▱ 23%      🌐 Network ⬇️ 4.2 Mbps ⬆️ 1.1 Mbps
🖥️ VPS Fleet: 24 total • 🟢 21 running • 🔴 3 stopped
🛡️ Mining Guard: 🟢 Active • 0 detections / 24h
```

Sustained overload also pings the alert channel.

| Command | Description |
|---|---|
| `.setstatuschannel #channel` | Create the live dashboard |
| `.setnode <name>` | Rename the node |
| `.refreshstatus` | Force an update |
| `.version` | AVM version + changelog |

---

## 🧰 VPS Management (highlights)

`.create` • `.manage` • `.myvps` • `.vpsinfo` • `.vps-stats` • `.restart-vps` • `.resize-vps` • `.add-resources` • `.snapshot` • `.restore-backup` • `.clone-vps` • `.migrate-vps` • `.share-user` • `.suspend-vps` / `.unsuspend-vps` • `.ports` • `.delete-vps` • `.serverstats` • `.maintenance`

Run `.help` in Discord for the full categorized menu.

---

## 🛡️ Permissions & Safety

- Admin-only commands are checked on every call; panel buttons are locked to the person who opened them.
- Failures are **surfaced, never swallowed** — staff see the real error; users get a clean message.
- Settings, plans, strikes, invite history and the dashboard message ID live in `vps.db` and survive restarts.
- Keep `config.json` private (the installer sets `chmod 600`). **Never commit it.**

Add this to your `.gitignore`:

```gitignore
config.json
vps.db
*.log
venv/
__pycache__/
```

---

## 🩺 Troubleshooting

| Problem | Fix |
|---|---|
| `LXC command not found` | `snap install lxd && lxd init --auto` |
| Invite tracking says ❌ in `.branding` | Give the bot **Manage Server** + enable **Server Members Intent** |
| Dashboard not updating | `.branding` shows the exact error — usually missing channel permissions |
| Logo missing | Use a direct image link (not a webpage) or attach an image with `.setlogo` |
| First VPS takes minutes | Normal — the OS image is downloaded once, later ones are fast |
| Alerts not arriving | Set `.setalertchannel`; until then alerts are DM'd to the main admin |

---

## 📜 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

© 2026 Legacy Cloud

<div align="center">

**AVM v1.1 "Atlas" — built for ⚡ Legacy Cloud**

</div>
