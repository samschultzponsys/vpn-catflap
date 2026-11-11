<img src="packaging/catflap.svg" alt="catflap icon" width="96" align="right">

# VPN CatFlap (`catflap`)

Safe, one-click WireGuard remote access to your sites, from a tray icon. A
self-hosted, vendor-neutral replacement for UniFi Teleport / WiFiman Desktop
that works on **Linux** (Fedora KDE first, Debian next) and, later, **Windows**.

catflap turns *"I want remote access to site X"* into a split-tunnel WireGuard
connection with safe defaults. It works with **UniFi**, **TP-Link Omada**,
**Alta Labs** and any other WireGuard gateway:

- It provisions a client for **this device** via the gateway's API, or imports a
  `.conf` you downloaded.
- It routes **only the site networks you pick**, never `0.0.0.0/0`.
- It never hijacks your DNS, disables IPv6 on the tunnel, sets keepalive 25 and
  connects only when you ask.
- It checks for subnet clashes, CGNAT, stale endpoints and "you're already on
  site" before connecting.
- After connecting, it checks that the tunnel really carries traffic.

> **This README is kept up to date with every change.** It documents every
> command and setting. If something here doesn't match the app, that's a bug.

---

## Contents

1. [Status by vendor and platform](#status-by-vendor-and-platform)
2. [Install](#install)
3. [Quick start](#quick-start)
4. [Creating API keys per vendor](#creating-api-keys-per-vendor)
5. [How-to guides](#how-to-guides)
6. [Tray app](#tray-app)
7. [Command reference](#command-reference)
8. [What catflap enforces (and why)](#what-catflap-enforces-and-why)
9. [Where things are stored](#where-things-are-stored)
10. [Updates and versioning](#updates-and-versioning)
11. [Ansible](#ansible)
12. [Troubleshooting](#troubleshooting)
13. [CGNAT sites](#cgnat-sites)
14. [Development](#development)
15. [License](#license)

---

## Status by vendor and platform

| | List sites/networks | Read WireGuard server | Create / revoke client | Import `.conf` |
|---|---|---|---|---|
| **UniFi** (UniFi OS consoles, Network 9.x/10.x) | ✅ official API | ✅ classic API | ✅ classic API* | ✅ |
| **TP-Link Omada** | planned | planned | planned | ✅ |
| **Alta Labs** | planned | planned | planned | ✅ |
| **Generic WireGuard** | – | – | – | ✅ |

\* UniFi's peer endpoints are undocumented and may change between Network
versions. If they fail, catflap tells you so, and you can download the `.conf`
from the UniFi UI and use `catflap import` instead. Every safety rule still applies.

| Platform | Backend | Status |
|---|---|---|
| Fedora (KDE Plasma, Wayland) | NetworkManager (nmcli, or D-Bus inside the Flatpak) | primary; Flatpak recommended |
| Debian / Ubuntu | NetworkManager | supported |
| Windows 10/11 | WireGuard for Windows tunnel service | under development; not yet tested on real Windows machines |

## Install

Releases appear on the [releases page](https://github.com/samschultzponsys/vpn-catflap/releases)
once a version tag (e.g. `v0.1`) has been pushed. Each release has a Flatpak bundle,
an RPM and a Python wheel.

### Fedora: Flatpak (recommended)

**From a release** (Flathub is used for the KDE runtime and PySide):

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

```bash
curl -L -o ~/Downloads/catflap-<version>.flatpak https://github.com/samschultzponsys/vpn-catflap/releases/download/v<version>/catflap-<version>.flatpak
```

```bash
flatpak install --user -y ~/Downloads/catflap-<version>.flatpak
```

**Or build it yourself from this repository** (no release needed; about 1 GB of
SDK download the first time):

```bash
sudo dnf install flatpak-builder git
```

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

```bash
flatpak install --user -y flathub org.kde.Sdk//6.11 io.qt.PySide.BaseApp//6.11
```

```bash
git clone https://github.com/samschultzponsys/vpn-catflap && cd vpn-catflap
```

```bash
flatpak-builder --user --install --force-clean build-dir packaging/flatpak/io.github.samschultzponsys.CatFlap.yml
```

Start **catflap** from the application menu. On the command line, the CLI is:

```bash
flatpak run --command=catflap io.github.samschultzponsys.CatFlap --help
```

An alias saves typing: `alias catflap='flatpak run --command=catflap io.github.samschultzponsys.CatFlap'`.

What the sandbox is allowed to do, and why:

| Permission | Why |
|---|---|
| System bus: `org.freedesktop.NetworkManager` | Create, connect and disconnect the WireGuard connections. Polkit still decides, as for any desktop app |
| Session bus: `org.freedesktop.secrets` | API keys and private keys in KWallet / GNOME Keyring |
| `org.kde.StatusNotifierWatcher`, `org.freedesktop.Notifications` | Tray icon and notifications |
| Network | Gateway APIs, endpoint lookups, update check |
| `~/.config/autostart` | The *Start at login* option |
| `~/Downloads` | Importing downloaded `.conf` files; downloading updates |

The Flatpak doesn't need `nmcli`, `ip` or `ping`: it talks to NetworkManager
over D-Bus and pings with an unprivileged ICMP socket. Its settings live in
`~/.var/app/io.github.samschultzponsys.CatFlap/config/catflap/`. That is separate from
an RPM/pip install, so use **Export/Import** to move between them.

### Fedora: RPM

```bash
sudo dnf install https://github.com/samschultzponsys/vpn-catflap/releases/download/v<version>/catflap-<version>-1.noarch.rpm
```

The RPM also installs PySide6 (for the tray) and wireguard-tools as recommended packages.

**Debian / Ubuntu (until the .deb is ready):**

```bash
sudo apt install network-manager python3-keyring python3-cryptography python3-pyside6.qtwidgets pipx
```

```bash
pipx install --system-site-packages https://github.com/samschultzponsys/vpn-catflap/releases/download/v<version>/catflap-<version>-py3-none-any.whl
```

**Windows (under development):** install [WireGuard for Windows](https://www.wireguard.com/install/)
and Python 3.11+, then:

```bash
pip install "catflap[tray] @ https://github.com/samschultzponsys/vpn-catflap/releases/download/v<version>/catflap-<version>-py3-none-any.whl"
```

**From source:**

```bash
git clone https://github.com/samschultzponsys/vpn-catflap && cd vpn-catflap
```

```bash
pip install -e ".[tray,test]"
```

Run catflap as **your normal user**. Never use `sudo`. NetworkManager's polkit
rules allow desktop users to manage their own connections.

## Quick start

### UniFi, fully automatic

```bash
# 1. Add the gateway. You'll be asked for the API key; the TLS certificate gets pinned.
catflap gateway add home --vendor unifi --url https://192.168.1.1 --tofu

# 2. See the site's networks.
catflap gateway networks home --check-local

# 3. Create a client for THIS laptop and choose what to route.
catflap provision home home --network LAN --network IoT

# 4. Connect (runs pre-flight checks, connects, then checks the handshake).
catflap up home
```

Or start the tray app (`catflap-tray`) and use **Add gateway…** and **Add site…**.

### Any gateway: import a downloaded .conf

```bash
catflap import ~/Downloads/office.conf office --vendor unifi \
    --route 192.168.10.0/24 --endpoint office.example.net
catflap up office
```

catflap removes `0.0.0.0/0`, `::/0`, `DNS =` and IPv6 from the file, adds
keepalive, gives the interface a safe name, and tells you what it changed.

## Creating API keys per vendor

API keys and passwords are stored **only on this PC**, in the system keyring
(KWallet, GNOME Keyring or Windows Credential Manager). They never go into catflap's
config files. They only leave the PC inside an [encrypted export](#move-catflap-to-another-pc)
that you create yourself.

### UniFi

1. In UniFi Network (9.0 or newer) open **Settings → Control Plane → Integrations**.
2. Click **Create API Key**, give it a name (e.g. `catflap-laptop`) and copy the key.
3. Run `catflap gateway add <name> --vendor unifi --url https://<console-ip> --tofu`
   and paste the key when asked.

The API key gives catflap the sites and networks. Reading the WireGuard server
and creating clients uses UniFi's classic API. Recent consoles accept the same key
there. If yours doesn't, add a **local** admin account (UniFi OS → Admins → add a
local-access-only user; UI.com/SSO accounts are refused by the local API):

```bash
catflap gateway add home --vendor unifi --url https://192.168.1.1 --tofu --username catflap-svc
```

Self-hosted Network Application (no UniFi OS)? Add `--legacy-controller`.

**Before you provision:** the site needs a WireGuard server. Go to **Settings → VPN →
VPN Server → Create New → WireGuard** and **click Create / Apply Changes**. catflap
checks for this and refuses to continue until the server exists (problem #1).

### TP-Link Omada

API provisioning is planned. Today: Omada controller → **Settings → VPN →
WireGuard**, create a peer for this device, export its config and run
`catflap import <file> <name> --vendor omada --route <cidr>`. To prepare for API
support, create an Open API app under **Global View → Settings → Platform
Integration → Open API** (client-credentials mode) and keep the client ID and secret.

### Alta Labs

API provisioning is planned. Today: in the Alta Labs portal create a WireGuard
client for this device, download its config and run
`catflap import <file> <name> --vendor alta --route <cidr>`.

### Generic WireGuard

```bash
catflap import <file> <name> --route <cidr>
```

## How-to guides

### Choose what goes through the tunnel

Pick site networks by name, CIDR or VLAN id. Repeat `--network` for each one:

```bash
catflap provision home home --network LAN --network 20 --network 192.168.30.0/24
```

Without `--network`, `provision` shows a numbered checklist (in a terminal) or
lists the choices and stops (in a script). To change routes later:

```bash
catflap set home --route 192.168.1.0/24 --route 192.168.20.0/24
```

### Several sites, gateways and vendors at once

Each gateway has its own credentials, and each site profile has its own routes,
VPN subnet, endpoint, DNS and colour:

```bash
catflap gateway add home   --vendor unifi --url https://192.168.1.1 --tofu
catflap gateway add ponsys --vendor unifi --url https://10.0.0.1 --tofu
catflap provision home home --network LAN
catflap provision ponsys office --network Servers --network Staff
catflap import ~/Downloads/cabin.conf cabin --vendor omada --route 192.168.50.0/24
catflap list        # grouped by gateway and vendor
```

catflap warns when two sites' networks or VPN subnets overlap. A good plan
is one VPN subnet per site:

```bash
catflap suggest-subnet 3     # -> 10.250.3.0/28
```

### Use a DDNS hostname instead of the WAN IP

During `provision`, catflap uses the gateway's DDNS hostname if one is set up,
otherwise its WAN IP. To change it:

```bash
catflap set home --endpoint home.example.net
```

### Resolve one internal domain through the tunnel (split DNS)

```bash
catflap set office --dns-domain ponsys.lan --dns-server 10.0.0.1
catflap set office --no-dns     # remove it again
```

Only `*.ponsys.lan` queries go to `10.0.0.1`. catflap never sets a catch-all `~`
domain, and refuses a DNS server that isn't inside a routed network.

### Connect, disconnect, status

```bash
catflap check home      # pre-flight only
catflap up home         # pre-flight, connect, health check
catflap status
catflap down home
```

If the health check finds no handshake, catflap disconnects again and tells
you the likely cause. Your normal internet is never affected.
`--force` skips pre-flight errors; `--keep-on-failure` leaves a failing tunnel up
for debugging.

### Revoke a device

```bash
catflap gateway clients home     # all clients on the server; this PC's are marked
catflap revoke home              # delete this device's client on the gateway + local profile
catflap remove home              # local only (e.g. the client was deleted in the UI)
```

Every device gets its own client and key (`catflap-<device>-<site>`), so one
lost laptop can be revoked without touching the others.

### Move catflap to another PC

```bash
catflap export ~/catflap-backup.json          # asks for a passphrase; AES-256-GCM encrypted
# on the new PC:
catflap import-bundle ~/catflap-backup.json   # recreates gateways, credentials and connections
```

The export contains gateways, API keys/passwords, profiles, private keys and
settings. Connections are recreated on the new PC with all safety settings.
**Don't use the same profile on both PCs at the same time**, because they would
fight over one key (problem #11). If you're adding a *second* PC rather than
replacing one, copy only the gateways and provision fresh clients:

```bash
catflap export ~/gateways.json --gateways-only
# on the second PC:
catflap import-bundle ~/gateways.json
catflap provision home home --network LAN
```

For scripts, set `CATFLAP_PASSPHRASE` instead of typing the passphrase. `--plaintext`
writes an unencrypted file; avoid it.

### Re-apply settings

If someone edited the connection in KDE's network settings, or after an upgrade:

```bash
catflap apply home
catflap apply --all
```

## Tray app

Start it with `catflap-tray`, or `catflap tray` (add `--minimized` to start hidden).

- **Icon:** the app's cat in its cat flap. **Grey outline** = nothing connected.
  **Cat and frame in the site's colour** = connected. Several sites up = the cat is
  striped in each site's colour. **Red dot** = the last connection attempt failed.
- **Left-click** the icon to show or hide the window. **Right-click** shows sites grouped by
  gateway and vendor. Click a site to connect or disconnect it. Each entry shows
  its status (connected, handshake age, error).
- **Colours:** select a site in the window → **Colour…**, or
  `catflap set home --color '#1F77D0'`.
- **Minimize / close:** minimizing hides the window to the tray. Closing it asks
  **Keep running in tray** or **Quit catflap**, with *Remember my choice*.
  **Settings → Reset close-button choice** brings the question back. Tunnels stay
  connected either way.
- **Notifications** on connect, disconnect and failure, with the reason.
- **Settings:** *Start at login* (XDG autostart on Linux, the Run key on Windows) and
  *Check for updates automatically*.
- **File:** *Export everything…* and *Import export file…* (encrypted).
- **Help → About** shows the version and **how catflap was installed**.
  **Help → Check for updates…** updates automatically or shows the commands
  to run.
- **Add gateway…** (credentials go to the keyring; the certificate fingerprint
  is shown for you to confirm) and **Add site…**, either *from a gateway* (tick the
  networks to route) or *from a .conf file*.

The tray reloads state every few seconds, so CLI changes show up on their own.

## Command reference

Global options: `--json` (machine-readable output), `-V/--version`.
Exit codes: `0` ok, `1` error, `2` usage, `3` checks failed.

| Command | What it does |
|---|---|
| `catflap version` | Version and install method |
| `catflap update [--check] [-y]` | Check for a new release; update automatically or show how |
| `catflap gateway add NAME --vendor {unifi,omada,alta,generic} [--url URL] [--tofu \| --fingerprint FP \| --ca-file F \| --insecure] [--api-key-stdin] [--username U] [--password-stdin] [--legacy-controller]` | Add or update a gateway |
| `catflap gateway list` | List gateways |
| `catflap gateway remove NAME` | Remove a gateway and its stored credentials |
| `catflap gateway set-key NAME [--password] [--stdin]` | Replace the stored API key or password |
| `catflap gateway sites NAME` | List sites |
| `catflap gateway networks NAME [--site S] [--check-local]` | List networks; flag ones that clash with where you are now |
| `catflap gateway clients NAME [--site S]` | List VPN clients (devices); this PC's are marked |
| `catflap provision GATEWAY NAME [--site S] [--network N]… [--device D] [--server S] [--endpoint H] [--dns-domain D --dns-server IP] [--color C] [--replace] [--no-install]` | Create a client for this device and the connection |
| `catflap import FILE NAME [--vendor V] [--route CIDR]… [--gateway G --site S --network N…] [--endpoint H] [--dns-domain …] [--color C] [--replace] [--no-install]` | Safely import a `.conf` |
| `catflap list` | Sites grouped by gateway, with status |
| `catflap show NAME` | Show a profile |
| `catflap set NAME [--endpoint H] [--route CIDR]… [--color C] [--ping-target IP] [--dns-domain D --dns-server IP \| --no-dns]` | Change a profile |
| `catflap apply NAME \| --all` | Re-create the OS connection from the profile |
| `catflap check NAME [--offline]` | Pre-flight checks only |
| `catflap up NAME [--force] [--keep-on-failure]` | Connect |
| `catflap down NAME` | Disconnect |
| `catflap status [NAME]` | Status, bytes, handshake age (when readable) |
| `catflap remove NAME [--revoke]` | Remove a profile (and optionally its client on the gateway) |
| `catflap revoke NAME` | Revoke on the gateway and remove locally |
| `catflap export FILE [--gateways-only] [--plaintext]` | Export everything, encrypted |
| `catflap import-bundle FILE [--overwrite] [--no-install]` | Import an export |
| `catflap suggest-subnet N` | Suggest `10.250.N.0/28` and check it's free |
| `catflap tray [--minimized]` | Start the tray app |

Environment variables: `CATFLAP_API_KEY`, `CATFLAP_PASSWORD`, `CATFLAP_PASSPHRASE`
(secrets for non-interactive use), `CATFLAP_CONFIG_DIR` (state location),
`CATFLAP_SECRETS=file` (use a 0600 file instead of the keyring, e.g. headless),
`CATFLAP_WIREGUARD_EXE` (Windows: path to `wireguard.exe`),
`CATFLAP_INSTALL_METHOD` (override install-method detection),
`CATFLAP_BACKEND=dbus|nmcli` (choose the NetworkManager backend), `CATFLAP_ALLOW_ROOT=1`
(allow running as root; not recommended).

## What catflap enforces (and why)

Each item maps to a numbered problem in [docs/TEST-CHECKLIST.md](docs/TEST-CHECKLIST.md).

| # | Problem | What catflap does |
|---|---|---|
| 1 | Server never "Created" → no handshake | Checks the server exists and is applied before provisioning; health check after connect; names the cause |
| 2 | Interface name ≤ 15 chars / no spaces | `cf-<name>` sanitized and de-duplicated; verified after import |
| 3 | Full tunnel blackholes internet | `0.0.0.0/0` and `::/0` removed; `never-default`; only chosen networks routed |
| 4 | DNS hijack via `~` search domain | DNS removed; `dns-search` cleared on IPv4 and IPv6 and **read back to verify** |
| 5 | Gateway's ULA IPv6 | IPv6 addresses and routes dropped; `ipv6.method disabled` |
| 6 | Idle tunnel drops behind NAT | `PersistentKeepalive = 25` |
| 7 | Autoconnect at login | `connection.autoconnect no` |
| 8 | Subnet overlaps (local LAN, other tunnels, duplicate VPN subnets) | Pre-flight blocks clashes; warnings; `suggest-subnet` |
| 9 | Dynamic WAN IP | Uses DDNS hostname when available; warns on raw IP or stale DDNS |
| 10 | CGNAT | Compares gateway WAN IP to 100.64.0.0/10 and private ranges; warns or blocks |
| 11 | Shared `.conf` between devices | Keys generated per device on the device; duplicate keys refused; per-device revoke |
| 12 | Site-to-site interplay / on-site | Blocks when you're on the site's LAN or the site is already reachable (e.g. Site Magic) |
| 13 | Running as root | Runs as your user; state in your home; polkit authorizes NetworkManager |

## Where things are stored

| What | Linux | Windows |
|---|---|---|
| Gateways, profiles, settings | `~/.config/catflap/state.json` (0600); Flatpak: `~/.var/app/io.github.samschultzponsys.CatFlap/config/catflap/` | `%APPDATA%\catflap\state.json` |
| API keys, passwords, WireGuard private keys | System keyring (service `catflap`) | Windows Credential Manager |
| Keyring fallback (`CATFLAP_SECRETS=file`) | `~/.config/catflap/secrets.json` (0600) | same folder |
| The live connection | NetworkManager connection `catflap <name>` | `%LOCALAPPDATA%\catflap\tunnels\cf-<name>.conf` + WireGuard tunnel service |

Private keys are generated on the device. With `provision`, the gateway only
ever sees the public key.

## Updates and versioning

- Versions are **`MAJOR.MINOR`**. The minor number runs 0–9 and then rolls over:
  `1.1 → 1.2 → … → 1.9 → 2.0`. Every release is tagged `vMAJOR.MINOR`.
- catflap checks GitHub for a newer release once a day; you can turn this off in the tray settings.
  `catflap update --check` checks on demand.
- catflap **detects how it was installed** and updates accordingly. The method is
  shown by `catflap version` and in **Help → About**:

| Installed via | Automatic update | Manual command |
|---|---|---|
| RPM | `pkexec dnf install <release rpm>` | `sudo dnf install <release rpm URL>` |
| .deb | `pkexec apt-get install ./<deb>` | `sudo apt install ./catflap_<v>_all.deb` |
| Flatpak | downloads the new bundle to `~/Downloads`; installs it too if you allowed it (below) | `flatpak install --user -y ~/Downloads/catflap-<v>.flatpak` |
| pipx | `pipx install --force <wheel>` | same |
| pip | `pip install --upgrade <wheel>` | same |
| Windows installer | downloads and runs the installer | download from the release page |
| Source checkout | – | `git fetch --tags && git checkout v<version>` |

**Flatpak updates.** A sandboxed app can't run `flatpak` on the host by itself.
By default, **Help → Check for updates…** (or `catflap update`) downloads the new
bundle to `~/Downloads` and shows the one command to run. To let catflap install
it too, grant this once:

```bash
flatpak override --user --talk-name=org.freedesktop.Flatpak io.github.samschultzponsys.CatFlap
```

That permission lets the app run commands on the host, so it is off by default.

Maintainers: `scripts/bump_version.py` moves to the next version (with rollover)
in every file. Then add a `CHANGELOG.md` entry, commit, and push tag `v<version>`.
The release workflow attaches these files, each named with the version, to the
GitHub release: `catflap-<v>.tar.gz`, `catflap-<v>-py3-none-any.whl`,
`catflap-<v>-1.noarch.rpm` (about 100 KB each) and `catflap-<v>.flatpak` (about
12 MB). All releases and their files are kept.

**Keeping GitHub usage low:**

- Release files don't count against Actions or Packages storage.
- The only Actions storage is the files passed between release jobs, which
  expire after one day.
- No caches, no GitHub Packages, and no binaries in git.
- CI runs one Linux job, only on `main` and pull requests. It skips docs-only
  changes and cancels superseded runs.
- Windows tests and the Flatpak build run once per release.

## Ansible

Every command works without prompts, supports `--json` and returns meaningful exit codes:

```yaml
- name: Add the home gateway
  ansible.builtin.command:
    cmd: catflap gateway add home --vendor unifi --url https://192.168.1.1 --fingerprint "{{ home_fp }}" --api-key-stdin
    stdin: "{{ vault_unifi_api_key }}"
  become: false
  no_log: true

- name: Provision this laptop
  ansible.builtin.command: >
    catflap --json provision home home --network LAN --device {{ inventory_hostname }}
  become: false
  register: cf
  changed_when: true
  failed_when: cf.rc != 0 and 'already exists' not in cf.stdout
```

Run these tasks as the desktop user (`become: false`). Without a desktop session,
also set `CATFLAP_SECRETS=file`.

## Troubleshooting

| Message | Meaning / fix |
|---|---|
| *No WireGuard handshake (N bytes sent, 0 received)* | Server not created/applied (#1); UDP port not forwarded / CGNAT (#10); stale endpoint (#9); client revoked or its key used elsewhere (#11) |
| *Site network X overlaps local network Y* | You're on a LAN using the same subnet (hotel/client site). Route fewer networks, or renumber the site |
| *You are on <site>'s LAN already* / *reachable without the tunnel* | You're on site, or linked via Site Magic: no tunnel needed |
| *Polkit denied the change* | Run as your desktop user in an active session, not with sudo or SSH |
| *the UniFi classic API refused the API key* | Add a local admin with `--username`, or use `catflap import` |
| *gateway TLS certificate changed* | The console's certificate changed; re-pin with `catflap gateway add … --tofu` |
| *NetworkManager did not keep the safe settings* | An NM plugin or policy overrode them; the connection was removed for safety. Please file a bug |

## CGNAT sites

Plain WireGuard needs the site to be reachable on a public IP and UDP port.
If the gateway's WAN IP is in `100.64.0.0/10`, catflap warns about it and blocks
connecting. Options being evaluated (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#nat-traversal-options)):
a relay on a VPS you control, Headscale/Tailscale-style coordination, or asking
the ISP for a public IP.

## Development

```bash
pip install -e ".[tray,test]"
python -m pytest -q
```

The tests run without NetworkManager, a gateway or a display: the backends and
drivers are tested with fakes, and the tray with Qt's offscreen platform. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design, the driver interface
and how to add a vendor. See [docs/TEST-CHECKLIST.md](docs/TEST-CHECKLIST.md) for
manual tests on real hardware.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). If you
redistribute catflap or build on it, you must include the NOTICE file
(attribution).
