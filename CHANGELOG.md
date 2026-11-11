# Changelog

Versions are `MAJOR.MINOR`; the minor number runs 0–9 and then rolls over
(1.9 → 2.0). Every release is tagged `vMAJOR.MINOR`.

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
