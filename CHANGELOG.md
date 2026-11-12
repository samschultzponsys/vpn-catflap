# Changelog

Versions are `MAJOR.MINOR`; the minor number runs 0–9 and then rolls over
(1.9 → 2.0). Every release is tagged `vMAJOR.MINOR`.

## 1.2

- Tray icon with several sites connected: the tile blends their colors (default), or
  cycles through them, or stays blue: Settings → Tray icon when connected. With
  one site it uses that site's color.
- Several sites at once, handled automatically: two sites using the same networks
  offer to **switch** (`catflap up --switch`); sites that only share a VPN server
  address both connect, with that address left to the first tunnel.
- Fixed: the red failure dot also showed for informational notes such as "You're
  here".
- Fixed: a site reachable only through another connected CatFlap tunnel was
  mistaken for "you're on site".

## 1.1

- Tray icon: turns into the solid blue app icon with a green dot when connected (the
  dot shows the count when several sites are up); red dot after a failure.
- Tray menu: short statuses ("Connected (workaround)", "You're here"…); the window
  keeps the full text.
- Icon name now changes with every release, so the app menu can't end up with a
  missing (?) or old icon after an update.

## 1.0

- The app is called **CatFlap** everywhere you see it: menu, taskbar, window titles,
  notifications and the setup guides. The command stays `catflap`.
- README header: icon centered above the title, so the heading line no longer runs
  through it.

## 0.9

- 24 site colors in two rows plus **Custom…** for any other color, in the Add a site
  guide and the Advanced view's Color… button.
- American spelling everywhere ("color").
- Shorter UniFi server steps, with what matters on the client screen: click
  **download** before **Save** (only needed if catflap asks for the file), and
  don't regenerate the public key afterwards.
- Smarter about address clashes: when the network you're on uses the same
  addresses as the site, catflap connects anyway, routing everything at the site
  through the VPN except your own address and your local router, and tells you.
- Better "you're at this site already" detection (public IP vs the site's
  endpoint or WAN IP, or your router being the site's router), shown as a note
  instead of an error.
- Full messages on the site cards instead of cut-off text.

## 0.8

- Icon: the launcher's icon name now changes whenever the artwork changes (Flatpak
  and RPM), so KDE can't keep showing an old cached icon in the taskbar and app
  menu, even across reinstalls.
- UniFi: exact steps for creating the WireGuard server, including the client UniFi
  requires at the same time (make it this computer and download its file), and
  what to put in each field (port, a unique VPN subnet per site, DNS).

## 0.7

- UniFi through the cloud: when the cloud connector refuses UniFi's internal API
  (it does for keys made with the new "Create New API Key" screen), the guide
  explains why and continues with a downloaded WireGuard file. It still checks
  the WireGuard server exists and lists your networks from the API.
- The guide lists the minimum key scope (Site Manager + UniFi Applications →
  Network, the sites you need) and recommends an expiry; an expired key gets a
  clear message.

- Updater: once the new version is installed, catflap restarts by itself after a
  short countdown, which you can cancel. In the Flatpak it notices when you've run
  the install command and then restarts into the new version.
- README: how to clear KDE's icon cache when the taskbar keeps an old icon.
- UniFi cloud keys: the guide, error messages and README now explain that a
  unifi.ui.com key only covers the site group (Fabric) chosen in the top-left
  dropdown when it was created; other groups need their own key.

## 0.6

- New app icon (Z1a): a white cat sitting in the blue tunnel, reaching up.
- Tray icon redrawn from the same cat outline: grey outline when disconnected, the
  site's color when connected (split for several sites), red dot after a failure.

## 0.5

- **UniFi through the cloud:** the guide now uses an account API key from
  unifi.ui.com and lets you pick your console. Traffic goes through Ubiquiti's cloud
  connector, so no local address is needed. Direct access is still there for when
  you're on site. CLI: `gateway add --cloud --console`, `gateway consoles`.
- **Exact steps per brand** in the Add a site guide (UniFi, Omada, Alta Labs).
- **Omada / other routers without a config file:** a new typed-in route where
  catflap creates this computer's keys and you add it as a WireGuard peer.
- No more "router address" question for file imports: pick the networks to reach
  (pre-ticked from the file), with brand-specific help on where to find them.
- The cat smiles now.
- The title bar shows the current icon: windows load it from the icon shipped
  inside catflap instead of the desktop's icon cache.

## 0.4

- New app icon (T1): the first icon's cat sitting in a blue tunnel.
- Tray icon redrawn to match: grey outline when disconnected, cat and tunnel in
  the site's color when connected, red dot after a failure.
- Windows, wizards and the update window use the installed app icon, so changing
  `catflap/resources/catflap.svg` changes it everywhere.

## 0.3

- New update window: what's new, the exact commands to update by hand (with
  Copy commands and Open download page), and an Update / Download button.
- catflap restarts itself into the new version after an update (Flatpak uses the
  portal's latest-version launch), then confirms the version it's running, or
  says if the update wasn't installed yet.
- Clicking the "update available" notification opens the update window.
- Four more icon options combining A's design with C's blue palette (O–R).

## 0.2

- First-start setup guide: choose a **Basic** or **Advanced** look (with a live example
  of each), then add the first site.
- **Basic view:** one big card per site with a Connect button; a clear "Add your
  first site" screen when empty.
- **Advanced view:** sites and gateways tables, a details pane, checks, edit, color,
  re-apply, remove/revoke, and API-key management.
- **Add a site** guide: UniFi automatic setup (API key help, connection test, site
  choice, VPN server check with fix-it steps, network picker that flags clashes),
  or a downloaded WireGuard file for Omada, Alta Labs and others, with per-vendor
  download instructions. **Add a gateway** guide for UniFi.
- Sites have friendly names ("Mum's house"); the tray menu has "Add a site…".
- Plain-language error messages.
- Fixed: settings could be briefly lost when the window saved while the
  background refresh reloaded them.
- Six icon options in `docs/icon-options/`.

## 0.1

- First milestone: vendor driver interface; UniFi driver (official integration API for
  sites/networks, classic API for the WireGuard server, peers, WAN IP and DDNS).
- Omada, Alta Labs and generic drivers: safe import of a downloaded `.conf`.
- NetworkManager backend (Linux) with enforced safe defaults and read-back verification.
- Windows backend (WireGuard for Windows tunnel service): under development.
- Pre-flight checks (overlaps, on-site/linked site, CGNAT, DDNS) and post-connect health check.
- Tray app: per-connection colors, minimize to tray, close-to-tray prompt, notifications,
  autostart.
- Multiple gateways/instances, each with its own credentials; credentials stored only in
  the local keyring.
- Encrypted export/import to move a setup to another PC.
- Update checks with install-method-aware update instructions and automatic update.
- Flatpak (KDE 6.11 + PySide base app, 12 MB bundle) with a NetworkManager D-Bus
  backend that needs no nmcli/ip/ping inside the sandbox.
