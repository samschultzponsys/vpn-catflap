<p align="center">
  <img src="catflap/resources/catflap.svg" alt="CatFlap icon" width="128">
</p>

<h1 align="center">CatFlap</h1>

<p align="center"><b>Safe, one-click WireGuard remote access to your sites, from a tray icon.</b><br>
UniFi · TP-Link Omada · Alta Labs · any WireGuard router</p>

A self-hosted, vendor-neutral replacement for UniFi Teleport / WiFiman Desktop
that works on **Linux** (Fedora KDE first, Debian next) and, later, **Windows**.

CatFlap turns *"I want remote access to site X"* into a split-tunnel WireGuard
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
4. [Setting up each brand](#setting-up-each-brand)
5. [How-to guides](#how-to-guides)
6. [Using the app](#using-the-app)
7. [Command reference](#command-reference)
8. [What CatFlap enforces (and why)](#what-catflap-enforces-and-why)
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
versions. If they fail, CatFlap tells you so, and you can download the `.conf`
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

Start **CatFlap** from the application menu. On the command line, the CLI is:

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

### Windows (preview)

1. Download `catflap-<version>-setup.exe` from the
   [releases page](https://github.com/samschultzponsys/vpn-catflap/releases) and run it.
   It installs for your user only, so no administrator rights are needed for CatFlap
   itself.
2. If **WireGuard for Windows** isn't installed yet, the installer downloads the
   official installer from download.wireguard.com and runs it. Windows asks for
   permission once, because WireGuard installs a network driver. If WireGuard is
   missing later, CatFlap offers to install it the same way.
3. Start **CatFlap** from the Start menu.

**On Windows:**
- Adding a site asks for administrator rights **once** (Windows' UAC prompt), to set the
  site up in WireGuard and let your account start and stop it. Connecting and
  disconnecting from the tray don't need any prompts after that.
- Removing a site asks once more.
- If **WiFiman Desktop** (Ubiquiti's Teleport app) is installed, CatFlap offers to
  uninstall it when it starts, since both change routes and DNS. Choose **Keep it**
  or **Don't ask again** to leave it.
- Updates: **Help → Check for updates…** downloads the new installer, runs it silently,
  and CatFlap reopens as the new version.
- The command line is `catflap-cli.exe` in the install folder. Windows file names
  ignore case, so it can't also be called `catflap.exe` next to `CatFlap.exe`.

The Windows version is new. Please report anything that doesn't work.

**From source:**

```bash
git clone https://github.com/samschultzponsys/vpn-catflap && cd vpn-catflap
```

```bash
pip install -e ".[tray,test]"
```

Run CatFlap as **your normal user**. Never use `sudo`. NetworkManager's polkit
rules allow desktop users to manage their own connections.

## Quick start

### UniFi, fully automatic

```bash
# 1. Add the console through the UniFi cloud. You'll be asked for the unifi.ui.com API key.
catflap gateway add home --vendor unifi --cloud --console "Home UDM"

# 2. See the site's networks.
catflap gateway networks home --check-local

# 3. Create a client for THIS laptop and choose what to route.
catflap provision home home --network LAN --network IoT

# 4. Connect (runs pre-flight checks, connects, then checks the handshake).
catflap up home
```

Or just open the app: the setup guide walks you through the same steps without
a terminal (see [Using the app](#using-the-app)).

### Any gateway: import a downloaded .conf

```bash
catflap import ~/Downloads/office.conf office --vendor unifi \
    --route 192.168.10.0/24 --endpoint office.example.net
catflap up office
```

CatFlap removes `0.0.0.0/0`, `::/0`, `DNS =` and IPv6 from the file, adds
keepalive, gives the interface a safe name, and tells you what it changed.

## Setting up each brand

CatFlap always ends up with a **WireGuard** connection on this computer. The
brand decides how it gets there. Every route is also in the app's **Add a site**
guide, step by step.

| Brand | How CatFlap reaches it | What you do |
|---|---|---|
| **UniFi** | **UniFi cloud** (unifi.ui.com Site Manager API, through Ubiquiti's cloud connector). Works from anywhere. | Paste one account API key, pick your console |
| **TP-Link Omada** | No API yet (planned: Omada Open API). | Add this computer as a WireGuard peer, type 4 values back |
| **Alta Labs** | No public API. | Download a WireGuard file for this computer, pick it |
| **Other WireGuard routers** | — | A WireGuard file, or the typed-in route |

### UniFi (automatic, through the cloud)

CatFlap doesn't need your router's local address. It talks to the console
through Ubiquiti's cloud, the same way unifi.ui.com does.

1. Sign in at [unifi.ui.com](https://unifi.ui.com).
2. In the **dropdown at the top left**, choose the **site group** that contains the
   console you want. UniFi calls these groups a Fabric or organization; consoles
   that aren't in one are under **Independent Sites**. **A key only works for the
   group selected when it's created.**
3. Open **API Keys** in the left menu → **Create New API Key**:
   - **Name:** `catflap`.
   - **Expiration:** the longest you're comfortable with; a year is a good balance.
     When it expires, CatFlap says so, and you paste a new key in **Advanced view →
     Gateways → Change API key**.
   - **Minimum scope:**
     - **Site Manager:** on. It lets CatFlap find your consoles.
     - **UniFi Applications:** on, with only **Network** (Protect isn't needed).
     - **Sites:** the sites you want to reach, or All Sites.

   Copy the key; it's shown only once.
4. In CatFlap: **Add a site → UniFi → Through the UniFi cloud**, paste the key and
   pick your console from the list.

**Several groups?** Consoles in another group need that group's own key. Run
**Add a site** again with it; each key is stored separately.

From the command line:

```bash
catflap gateway consoles
```

```bash
catflap gateway add home --vendor unifi --cloud --console "Home UDM"
```

**Which key is which:**
- **Cloud key (unifi.ui.com, used above):** covers the consoles in the site group
  (Fabric) it was created in. The console must show as **Online** there (Remote
  Management on).
- **Console key (UniFi Network → Settings → Control Plane → Integrations):** only
  works on that console's own network. Use it with **Directly** in the guide, or
  `catflap gateway add … --url https://<console address>`.

**Before adding a site,** the console needs a WireGuard server. The guide checks
for this (problem #1) and shows these steps:

1. **Network → Settings → VPN → VPN Server → Create New → WireGuard.** Keep port
   51820. For the subnet, use a range used nowhere else, different per site:
   `10.250.1.1/24`, then `10.250.2.1/24`, and so on.
2. UniFi asks for a client: name it after this computer. You only need its file if
   CatFlap later asks for one (when it can't create its own client through the cloud).
   To be safe, click **download** at the top of the client window **before Save**.
   It can't be downloaded after.
3. **Save**, then **Apply Changes**.

In the client window, leave Pre-Shared Key and Networks Behind Client off. Don't
press ↻ next to the Public Key after downloading: it makes the file stop
working. If CatFlap creates its own client (`catflap-…`), you can delete this
one afterwards.

**What the cloud allows:**
- UniFi's official API, which is all that unifi.ui.com keys reach, covers sites,
  networks and the list of VPN servers. It **can't** read a WireGuard server's
  details or create VPN clients (checked against Network 10.6).
- CatFlap first tries UniFi's internal API through the cloud connector. Keys made
  with the new **Create New API Key** screen are refused there. In that case the
  guide says so and offers **Continue with a downloaded WireGuard file**.
- With that file, CatFlap still checks the WireGuard server exists, still lists
  your networks to tick, and still applies every safety fix.

**Downloading the file:** use the client file from when you created the server.
For another computer: open the WireGuard server, **Add Client**, click **download**
before **Save**, then **Apply Changes**.

**Fully automatic** creation of clients works with **Directly** (a console key,
while on the console's network) or a self-hosted controller.

### TP-Link Omada (typed in)

Omada's WireGuard doesn't hand out a configuration file. Instead:

1. **Add a site → TP-Link Omada → Type the details.** CatFlap creates this
   computer's keys and shows its **public key**.
2. In your Omada controller (5.9 or later, or Omada Central), open the site →
   **Settings → VPN → WireGuard**:
   - If there's no WireGuard VPN yet: **Create New**, keep listen port **51820**,
     and set **Local IP Address** to a range just for the VPN, e.g. `10.250.1.1`.
     Copy its **Public Key**.
   - **Peers → Create New:** paste this computer's public key, set **Allowed
     Address** to e.g. `10.250.1.2/32`, set **Persistent Keepalive** to 25, and save.
3. Back in CatFlap, enter:
   - the router's public key
   - the site's internet address (your Dynamic DNS name from **Settings → Services →
     Dynamic DNS**, or the WAN IP)
   - the port
   - this computer's VPN address (`10.250.1.2`)
4. Tick or add the networks to reach. You'll find them under **Settings → Wired
   Networks → LAN**.

Menu names can differ slightly between Omada versions. Automatic setup through
the **Omada Open API** is being added next. It will use **Settings → Platform
Integration → Open API** (Client ID, Client Secret, Omada ID). On Omada Central,
that needs the Standard tier; Essentials has no Open API.

### Alta Labs (file)

Alta Labs doesn't publish an API, so CatFlap uses a file (Route10, Control 1.0q
or later):

1. Sign in at [manage.alta.inc](https://manage.alta.inc) → your site → your
   **Route10** → **VPN** tab.
2. Turn on the **WireGuard server**.
3. Add a **user profile** for this computer (one per device), then download its
   WireGuard configuration.
4. In CatFlap: **Add a site → Alta Labs**, pick the file, and add the networks to
   reach.

### Other WireGuard routers

**Add a site → Another WireGuard router**, then either:
- **pick a WireGuard file** made for this computer, or
- **type the details**, as for Omada.

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
VPN subnet, endpoint, DNS and color:

```bash
catflap gateway add home   --vendor unifi --url https://192.168.1.1 --tofu
catflap gateway add ponsys --vendor unifi --url https://10.0.0.1 --tofu
catflap provision home home --network LAN
catflap provision ponsys office --network Servers --network Staff
catflap import ~/Downloads/cabin.conf cabin --vendor omada --route 192.168.50.0/24
catflap list        # grouped by gateway and vendor
```

CatFlap warns when two sites' networks or VPN subnets overlap. A good plan
is one VPN subnet per site:

```bash
catflap suggest-subnet 3     # -> 10.250.3.0/28
```

### Use a DDNS hostname instead of the WAN IP

During `provision`, CatFlap uses the gateway's DDNS hostname if one is set up,
otherwise its WAN IP. To change it:

```bash
catflap set home --endpoint home.example.net
```

### Resolve one internal domain through the tunnel (split DNS)

```bash
catflap set office --dns-domain ponsys.lan --dns-server 10.0.0.1
catflap set office --no-dns     # remove it again
```

Only `*.ponsys.lan` queries go to `10.0.0.1`. CatFlap never sets a catch-all `~`
domain, and refuses a DNS server that isn't inside a routed network.

### Several sites at once

Connect as many sites as you like; each gets its own tunnel, and only its own
networks go through it:
- **Different networks:** they simply work side by side.
- **The same networks at two sites** (e.g. both 192.168.1.0/24): only one can be
  up at a time. CatFlap offers to **switch** (disconnect the other and connect
  this one); on the command line, `catflap up <site> --switch`.
- **Only the VPN server address is the same** (UniFi often gives every site
  192.168.3.1): that's fine. CatFlap leaves that one address to the site that was
  connected first and checks the other connection by its handshake instead.

### Connect, disconnect, status

```bash
catflap check home      # pre-flight only
catflap up home         # pre-flight, connect, health check
catflap status
catflap down home
```

If the health check finds no handshake, CatFlap disconnects again and tells
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

### Move CatFlap to another PC

```bash
catflap export ~/catflap-backup.json          # asks for a passphrase; AES-256-GCM encrypted
# on the new PC:
catflap import-bundle ~/catflap-backup.json   # recreates gateways, credentials and connections
```

The export contains gateways, API keys/passwords, profiles, private keys and
settings. **Importing merges** with whatever is already on the computer:
- **New** sites and gateways are added.
- **Identical** ones are recognised and left alone.
- **Same name, different settings:** you choose once for all of them:
  - **Keep mine:** this computer's version stays.
  - **Replace with imported:** the imported version replaces it.
  - **Keep both:** the imported site is added as "<name> (imported)"; a gateway
    becomes `<name>-imported`.

  On the command line: `--on-conflict keep|replace|both`. Connections are recreated on the new PC with all safety settings.
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

## Using the app

Start **CatFlap** from your application menu (or run `catflap-tray`; add
`--minimized` to start hidden in the tray).

### First start

A short setup guide opens the first time:

1. **Welcome.** What CatFlap does, in plain words.
2. **Choose a look,** with a live example of each:
   - **Basic:** one big card per site with a **Connect** button. Best if you
     just want it to work.
   - **Advanced:** tables with every detail (routes, endpoints, gateways, checks)
     and all the management tools.

   Switch any time from **View → Basic / Advanced** or the link at the bottom
   of the window.
3. **Add your first site.** The *Add a site* guide starts right away.

You can run the setup guide again from **Help → Setup guide…**.

### Adding a site (the guide)

**File → Add a site…**, the **Add a site** button, or **Add a site…** in the tray
menu. The guide asks one thing at a time:

1. **What kind of router is at the site?** UniFi, TP-Link Omada, Alta Labs, or
   another WireGuard router. If you've already added a UniFi console, you can
   pick it.
2. **UniFi (automatic):**
   - **Connect to UniFi:** *Through the UniFi cloud* (default) with the exact
     steps to create a unifi.ui.com API key in the right site group, or
     *Directly* when you're on the console's network. No router address is
     needed for the cloud.
   - **Which console?** A list of the consoles in that key's site group.
   - **Which site?** If the console manages several.
   - **VPN server check.** If the console has no WireGuard server yet, or it was
     never applied, the page shows exactly what to click and has a **Check
     again** button. It also warns in plain words about CGNAT or a second router
     in front.
   - **What do you want to reach?** Tick the networks. Networks that clash with
     the one you're on now are marked.
3. **TP-Link Omada / other: type the details.** CatFlap shows this computer's
   public key and the exact steps to add it as a WireGuard peer on the router.
   You type back four values: the router's public key, the site's internet
   address, the port, and this computer's VPN address.
4. **Alta Labs / other: pick a WireGuard file.** The page says exactly where to
   get it for that brand.
5. **What do you want to reach?** (Omada / Alta / other) Networks from the file
   are pre-ticked. Add others as e.g. `192.168.1.0/24`; the page says where to
   find them for that brand.
6. **Name and color:** e.g. "Home", "Mum's house". The color is used by the tray
   icon while connected. Tick *Connect as soon as it's set up* if you like.
7. **Done:** a summary of what was set up and how it's kept safe.

**Add a gateway…** (File menu, or the Gateways tab in Advanced) adds a UniFi
router without adding a site yet.

### Basic view

One card per site: its name, its color, its state (*Connected*, *Not
connected*, or *Couldn't connect: <reason>*), and one **Connect / Disconnect**
button. With no sites yet, it shows a big **Add your first site** button.

### Advanced view

- **Sites tab:** a table (site, gateway, status, interface, routes, endpoint)
  and a details pane (tunnel address, VPN subnet, split DNS, device/client id,
  traffic, warnings, last check results). Buttons:
  - **Connect / Disconnect**
  - **Run checks:** pre-flight only.
  - **Edit…:** name, DDNS name, networks, split DNS.
  - **Color…**
  - **Re-apply settings**
  - **Remove…:** from this computer only, or also delete this computer's access
    on the router.
- **Gateways tab:** your routers, with **Change API key…**, **Remove gateway…**
  and **Add gateway…**.

### Tray icon

- **The icon:** the app's cat, sitting in its tunnel and reaching up.
  - **Grey outline:** nothing connected.
  - **Solid icon with a green dot:** connected. With several sites connected, the
    dot shows how many. The tile color is set under **Settings → Tray icon when
    connected**:
    - **Site colors** (default): the site's own color, or a blend of all connected
      sites' colors.
    - **Cycle through site colors:** fades from one site's color to the next.
    - **Blue:** always the blue app icon.
  - **Grey outline with a red dot:** the last connection attempt failed.
- **Clicking it:**
  - **Left-click** shows or hides the window.
  - **Right-click** lists your sites with their status. Click one to connect or
    disconnect it; each shows a short status (*Connected*, *Not connected*,
    *You're here*…). The menu also has **Add a site…**, **Open CatFlap** and
    **Quit**.
- **Notifications** on connect, disconnect and failure, with the reason in plain words.

### Window and settings

- **Minimize / close:** minimizing hides the window to the tray. Closing it asks
  **Keep running in tray** or **Quit CatFlap**, with *Remember my choice*.
  **Settings → Ask again what the close button does** brings the question back.
  Connected sites stay connected either way.
- **Settings:** *Start CatFlap when I log in* and *Check for updates automatically*.
- **File:** *Export everything…* and *Import from another computer…* (locked with
  a passphrase).
- **Help:** *Setup guide…*, *Check for updates…*, and *About* (version, and **how
  CatFlap was installed**).

The window picks up changes made with the command line within a few seconds.

## Command reference

Global options: `--json` (machine-readable output), `-V/--version`.
Exit codes: `0` ok, `1` error, `2` usage, `3` checks failed.

| Command | What it does |
|---|---|
| `catflap version` | Version and install method |
| `catflap update [--check] [-y]` | Check for a new release; update automatically or show how |
| `catflap gateway add NAME --vendor {unifi,omada,alta,generic} [--cloud [--console C]] [--url URL] [--tofu \| --fingerprint FP \| --ca-file F \| --insecure] [--api-key-stdin] [--username U] [--password-stdin] [--legacy-controller]` | Add or update a gateway |
| `catflap gateway consoles` | List the UniFi consoles on a unifi.ui.com account |
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

## What CatFlap enforces (and why)

Each item maps to a numbered problem in [docs/TEST-CHECKLIST.md](docs/TEST-CHECKLIST.md).

| # | Problem | What CatFlap does |
|---|---|---|
| 1 | Server never "Created" → no handshake | Checks the server exists and is applied before provisioning; health check after connect; names the cause |
| 2 | Interface name ≤ 15 chars / no spaces | `cf-<name>` sanitized and de-duplicated; verified after import |
| 3 | Full tunnel blackholes internet | `0.0.0.0/0` and `::/0` removed; `never-default`; only chosen networks routed |
| 4 | DNS hijack via `~` search domain | DNS removed; `dns-search` cleared on IPv4 and IPv6 and **read back to verify** |
| 5 | Gateway's ULA IPv6 | IPv6 addresses and routes dropped; `ipv6.method disabled` |
| 6 | Idle tunnel drops behind NAT | `PersistentKeepalive = 25` |
| 7 | Autoconnect at login | `connection.autoconnect no` |
| 8 | Subnet overlaps (local LAN, other tunnels, duplicate VPN subnets). Several sites can be connected at once as long as their networks don't overlap | Same addresses where you are as at the site: connects anyway, routing around your own address and local router. Clashes with another active tunnel are blocked. Warnings; `suggest-subnet` |
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
- CatFlap checks GitHub for a newer release once a day; you can turn this off in the tray settings.
  `catflap update --check` checks on demand.
- CatFlap **detects how it was installed** and updates accordingly. The method is
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

**Updating from the app.** **Help → Check for updates…** (or clicking the
"update available" notification) opens the update window. It shows:

- what's new
- the **exact commands** to update by hand, with **Copy commands** and **Open
  download page** buttons
- one button to update for you

When the update is installed, CatFlap **restarts by itself** after a short
countdown (**Don't restart now** cancels it), or you can click **Restart
CatFlap**. It closes the app and opens the new version. Connected sites stay
connected. After the restart, CatFlap confirms the version it is now running, or
tells you if the update wasn't installed yet.

**Flatpak specifics.** A sandboxed app can't install updates on the host by
itself, so **Download and install** downloads `catflap-<v>.flatpak` to
`~/Downloads` and shows the one remaining command:

```bash
flatpak install --user -y ~/Downloads/catflap-<v>.flatpak
```

Run it in a terminal. CatFlap checks every few seconds which version is
installed, and as soon as the install finishes it closes and reopens as the new
version by itself. The restart uses the Flatpak portal's "latest version"
launch, so the reopened app really is the new one. To let CatFlap
run the install command itself, grant this once (it lets the app run commands
on the host, so it's off by default):

```bash
flatpak override --user --talk-name=org.freedesktop.Flatpak io.github.samschultzponsys.CatFlap
```

Maintainers: `scripts/bump_version.py` moves to the next version (with rollover)
in every file. Then add a `CHANGELOG.md` entry and push to `main`: the release
workflow sees the new version, builds everything and creates tag `v<version>`
and the release. Pushing a `v<version>` tag works too.
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
| *The network you're on now uses the same addresses as …* | Not an error: CatFlap connects with a workaround. Everything at the site is reachable except your own address and your local router's. To avoid it entirely, give the site a less common range (e.g. 10.20.x.0/24) |
| *You're at <site> right now* / *reachable without the tunnel* | You're on site, or linked via Site Magic: no VPN needed |
| *Polkit denied the change* | Run as your desktop user in an active session, not with sudo or SSH |
| *the UniFi classic API refused the API key* | Add a local admin with `--username`, or use `catflap import` |
| *gateway TLS certificate changed* | The console's certificate changed; re-pin with `catflap gateway add … --tofu` |
| The taskbar or app menu still shows an old CatFlap icon | Fixed from 0.8: the launcher's icon name changes whenever the artwork does, so KDE's icon cache can't reuse an old one. For older versions: `rm -f ~/.cache/icon-cache.kcache`, then log out and back in |
| *NetworkManager did not keep the safe settings* | An NM plugin or policy overrode them; the connection was removed for safety. Please file a bug |

## CGNAT sites

Plain WireGuard needs the site to be reachable on a public IP and UDP port.
If the gateway's WAN IP is in `100.64.0.0/10`, CatFlap warns about it and blocks
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
redistribute CatFlap or build on it, you must include the NOTICE file
(attribution).
