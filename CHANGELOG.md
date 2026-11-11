# Changelog

Versions are `MAJOR.MINOR`; the minor number runs 0–9 and then rolls over
(1.9 → 2.0). Every release is tagged `vMAJOR.MINOR`.

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
- **Advanced view:** sites and gateways tables, a details pane, checks, edit, colour,
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
- Tray app: per-connection colours, minimize to tray, close-to-tray prompt, notifications,
  autostart.
- Multiple gateways/instances, each with its own credentials; credentials stored only in
  the local keyring.
- Encrypted export/import to move a setup to another PC.
- Update checks with install-method-aware update instructions and automatic update.
- Flatpak (KDE 6.11 + PySide base app, 12 MB bundle) with a NetworkManager D-Bus
  backend that needs no nmcli/ip/ping inside the sandbox.
