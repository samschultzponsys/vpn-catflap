# Test checklist

Automated coverage runs with `python -m pytest -q`. The manual steps are for real
hardware before each release: a Fedora KDE laptop, at least one UniFi site, and
ideally a second network such as a phone hotspot to act as "away from home".

Copy this list into the release issue and tick it off.

## The 13 original problems

| # | Problem | Automated test(s) | Manual check |
|---|---|---|---|
| 1 | Server never created → no handshake | `test_unifi::test_server_never_created_gives_clear_error`, `test_checks::test_health_no_handshake`, `test_core::test_connect_and_failed_health_disconnects` | [ ] Delete/disable the WG server in UniFi → `catflap provision` refuses with "click Create". [ ] Revoke the client in the UI → `catflap up` reports *No WireGuard handshake* and disconnects |
| 2 | Interface name ≤15 chars, safe chars | `test_netutil::test_ifname_*`, `test_networkmanager::test_install_imports_with_sanitized_filename_and_safe_settings` | [ ] `catflap import x.conf "Ponsys Office (Main)"` → `ip link` shows `cf-ponsys-offic`. [ ] It also shows up correctly in KDE network settings |
| 3 | Full tunnel blackholes internet | `test_safety::test_unifi_conf_made_safe`, `test_default_and_v6_routes_dropped` | [ ] Import an unmodified UniFi .conf, connect with the server unreachable → web browsing still works. [ ] `ip route` shows no default route via `cf-*` |
| 4 | DNS hijack via `~` (IPv4 **and** IPv6) | `test_networkmanager::test_verify_catches_ipv6_tilde_and_rolls_back`, `test_split_dns_uses_routing_domain_only` | [ ] `resolvectl status cf-home` shows no DNS server and no `~.` domain. [ ] `nmcli -f ipv4.dns-search,ipv6.dns-search c show "catflap home"` is empty. [ ] After 3 connect/disconnect cycles and a reboot it's still empty |
| 5 | IPv6 ULA | `test_safety::test_default_and_v6_routes_dropped`, NM verify | [ ] `ip -6 addr show cf-home` shows nothing. [ ] `nmcli -f ipv6.method c show "catflap home"` → `disabled` |
| 6 | Keepalive 25 | `test_safety::test_unifi_conf_made_safe`, `test_core::test_provision_end_to_end` | [ ] `sudo wg show cf-home` shows `persistent keepalive: every 25 seconds`. [ ] Tunnel idle for 30 min behind NAT still answers ping |
| 7 | No autoconnect | NM install test | [ ] Reboot → the tunnel is not up. [ ] `nmcli -f connection.autoconnect c show "catflap home"` → `no` |
| 8 | Subnet overlaps | `test_checks::test_overlap_with_hotel_lan_blocks`, `test_overlap_with_other_tunnel`, `test_safety::test_vpn_subnet_collision` | [ ] Join a hotspot using the site's range (e.g. 192.168.1.0/24) → `catflap up home` connects with the workaround notice; site devices reachable, local router and internet still work. [ ] Two sites with the same VPN subnet → warning on the second provision |
| 9 | Dynamic WAN IP | `test_checks::test_endpoint_raw_ip_and_unresolvable`, `test_core::test_provision_end_to_end` (DDNS preferred) | [ ] With UniFi DDNS configured, `catflap show home` endpoint is the hostname. [ ] `catflap check` warns for a raw-IP endpoint |
| 10 | CGNAT | `test_checks::test_cgnat_and_double_nat` | [ ] A site behind CGNAT (or double NAT) produces the CGNAT / double-NAT finding |
| 11 | One client per device | `test_core::test_import_same_key_twice_refused`, `test_provision_same_device_twice_requires_replace`, `test_remove_with_revoke` | [ ] Provision on two laptops → two clients `catflap-<host>-home` in UniFi. [ ] `catflap revoke home` on one → only its client disappears and the other still connects |
| 12 | Site-to-site / on-site | `test_checks::test_on_site_detected`, `test_already_reachable_linked_site` | [ ] At home: `catflap up home` refuses (*on-site*). [ ] At a Site Magic-linked site: `catflap up home` refuses (*reachable without the tunnel*) |
| 13 | Not as root | `test_networkmanager::test_polkit_hint` | [ ] Everything works as the desktop user; `~/.config/catflap` is owned by the user; `sudo catflap up` explains not to use sudo |

## Feature checks

- [ ] **UniFi provisioning:** `gateway add --tofu` shows the fingerprint; `gateway networks`; `provision` with a checklist; `up`; `status`; `down`
- [ ] **Classic API fallback:** a console that rejects the API key on classic endpoints works with `--username`
- [ ] **Import:** Omada `.conf` and generic `.conf` imports
- [ ] **Multiple gateways:** two gateways with different API keys; `catflap list` groups by gateway and vendor
- [ ] **Split DNS:** `--dns-domain corp.lan` resolves `host.corp.lan` through the tunnel and everything else locally
- [ ] **Export/import:** export on PC A, import on PC B → gateways, keys and profiles present, `catflap up` works; a wrong passphrase is refused (`test_core::test_export_import_roundtrip`)
- [ ] **Export `--gateways-only`** then `provision` on PC B creates a separate client
- [ ] **Tray: icon** grey when disconnected; filled with the site's color when connected; multi-color with two sites; red dot after a failure (`test_tray.py`)
- [ ] **Tray: close button** asks tray/quit; *Remember* sticks; *Reset close-button choice* brings the question back
- [ ] **Tray: minimize** sends the window to the tray
- [ ] **Tray: notifications** on connect, disconnect and failure (KDE notification centre)
- [ ] **Tray: autostart** toggle creates/removes `~/.config/autostart/catflap.desktop`; after login the tray appears minimized
- [ ] **Tray: per-site color** via *Color…* and via `catflap set --color`
- [ ] **Updates:** `catflap version` shows the install method (rpm, pipx, source…). With a fake older version (`CATFLAP_INSTALL_METHOD=rpm` and an older `__version__`), `catflap update --check` prints the right command (`test_update_cli.py`)
- [ ] **Versioning:** `scripts/bump_version.py` goes 0.9 → 1.0; a release tag that doesn't match `version.py` fails the release workflow (`test_version.py`)
- [ ] **Flatpak:** install the bundle (or build locally). `flatpak run --command=catflap io.github.samschultzponsys.CatFlap version` says *installed via: flatpak*. Provision/import, then connect from the tray. `nmcli c show` lists `catflap <name>` with the safe settings (problems #2–#7 checks above). Credentials are in KWallet, under the Secret Service collection
- [ ] **Flatpak: polkit** allows the connection change for the desktop user without a password prompt (or prompts once, per your polkit policy)
- [ ] **Flatpak: tray icon** appears in the Plasma system tray (StatusNotifierItem) and notifications show
- [ ] **Flatpak: update** with an older bundle installed, *Check for updates* downloads the new bundle to `~/Downloads` and shows the install command. After `flatpak override --user --talk-name=org.freedesktop.Flatpak io.github.samschultzponsys.CatFlap` it installs it itself
- [ ] **Windows**: installer shows the WireGuard engine component (untickable) and lists the download on the Ready page; no WireGuard window or tray icon appears; adding a site triggers UAC once and creates `C:\Program Files\CatFlap\WireGuard\wireguard.exe`; `sc query WireGuardTunnel$cf-home` → RUNNING after connect, STOPPED after disconnect, no UAC; removing the last site removes the engine; with WireGuard for Windows installed, CatFlap offers to uninstall it or hide its tray icon and sites move to the engine on next connect
- [ ] **Other VPN software**: with Tailscale (or another listed VPN) running on Linux, or installed on Windows, CatFlap lists it once after an update (not on every start); ticking Uninstall runs its uninstaller; "Don't warn again" keeps it quiet until that program's version changes; Settings → Check for other VPN software… lists everything, silenced ones included
