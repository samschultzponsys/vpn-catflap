# CatFlap architecture

## Language: Python 3.11+

Why Python over Go or Rust for this project:

- The tray needs a KDE-native StatusNotifierItem on Wayland *and* a Windows
  notification-area icon. Qt (PySide6) handles both with one code base, and Fedora
  and Debian package it.
- NetworkManager, the system keyring (Secret Service / KWallet / Windows
  Credential Manager) and the vendor REST APIs all have mature Python tooling, and the
  core uses only the standard library plus `keyring` and `cryptography`.
- RPM/deb packaging of a noarch Python app is small (≈100 KB). This keeps
  releases light, as requested.
- The prototype (`wg-unifi-import.py`) was Python, and Ansible users read Python.

Go would give a single static binary. That matters most on Windows, where a
frozen Python build is an option later if needed.

## Layers

```
            ┌───────────────┐   ┌──────────────────┐
            │  CLI (cli.py) │   │ Tray (tray.py)   │   input / display only
            └───────┬───────┘   └────────┬─────────┘
                    └──────────┬─────────┘
                       ┌───────▼────────┐
                       │ core.Manager   │  workflows: provision, import, connect,
                       │                │  revoke, export/import, apply
                       └─┬─────┬─────┬──┘
        ┌────────────────┘     │     └──────────────────┐
┌───────▼────────┐   ┌─────────▼─────────┐   ┌──────────▼──────────┐
│ drivers/*      │   │ safety + checks   │   │ platform/*          │
│ vendor APIs →  │   │ (pure policy)     │   │ NetworkManager /    │
│ normalized     │   │                   │   │ WireGuard-Windows   │
│ model          │   │                   │   │                     │
└────────────────┘   └───────────────────┘   └─────────────────────┘
        model.py (Site, Network, WireGuardServer, PeerConfig, Profile)
        store.py (JSON state)  secrets.py (keyring)  bundle.py (encrypted export)
```

- **Drivers** only talk to gateways. They never touch the local machine.
- **Platform backends** only touch the local machine. They never talk to gateways.
- **safety.py** is the one place where a `PeerConfig` is made safe. Every path
  (provision, import, apply, import-bundle) goes through `make_safe()`.
- **checks.py** holds pre-flight and health checks as pure functions over
  inputs (local routes, other tunnels, resolver, ping). This makes them easy to test.

## Vendor driver interface (`catflap/drivers/base.py`)

```python
class Driver:
    vendor: str; display_name: str; capabilities: Capabilities

    def list_sites(self) -> list[Site]
    def list_networks(self, site) -> list[Network]                 # name, CIDR, VLAN, gateway IP
    def get_wireguard_server(self, site) -> WireGuardServer        # endpoint port, VPN subnet, pubkey, created?
    def get_wan_info(self, site) -> WanInfo                        # WAN IP (CGNAT check), DDNS hostname
    def list_clients(self, site, server) -> list[ClientRecord]
    def create_client(self, site, server, device_name, public_key, address) -> ClientRecord
    def revoke_client(self, site, server, client_id) -> None
    def parse_config(self, path) -> PeerConfig                      # vendor .conf quirks
    def allocate_address(server, used) -> str                       # shared helper
```

- `create_client` receives a **public key**. The key pair is generated on the
  device, so the private key never travels.
- A method the vendor can't do raises `NotSupported` with a message that points to
  the `.conf` import fallback. `Capabilities` tells the UI what to offer.
- Credentials come from the keyring as a `secrets` dict (`api_key`, `password`).
  Drivers never store anything themselves.

### Adding a vendor

1. Create `catflap/drivers/<vendor>.py` with a subclass of `Driver`, and register it in
   `drivers/__init__.py`.
2. Implement what the API allows, and set `capabilities` to match.
3. Override `parse_config` if the vendor's `.conf` has quirks.
4. Add tests with a fake HTTP client, as in `tests/test_unifi.py`.

### UniFi specifics

| Need | API | Endpoint |
|---|---|---|
| Sites | official integration | `GET /proxy/network/integration/v1/sites` |
| Networks + subnets | official integration | `GET …/sites/{id}/networks`, `…/networks/{id}` (`ipv4Configuration`) |
| VPN server list | official integration | `GET …/sites/{id}/vpn/servers` (id/name/type only) |
| WireGuard port, subnet, pubkey | classic | `GET /proxy/network/api/s/{site}/rest/networkconf` (`purpose=remote-user-vpn`) |
| Peers | classic v2 (undocumented) | `GET/POST /proxy/network/v2/api/site/{site}/wireguard/{server}/users[/batch_add\|/batch\|/batch_delete]` |
| WAN IP, DDNS | classic | `stat/health`, `rest/dynamicdns` |

The integration API (Network 10.1) has no WireGuard port, public key or peer
management, so the classic API fills the gap. It uses the same API key first, and
falls back to a local admin login (`/api/auth/login` plus CSRF token). If the server
config doesn't include the public key, CatFlap derives it from the server's
private key in the same response.

## Platform backends (`catflap/platform/`)

- **NetworkManager** (`nmcli`): writes the safe config to a 0700 temp directory as
  `<ifname>.conf`, imports it, sets the safe properties with `nmcli connection
  modify`, then **reads them back** (`verify()`). If NetworkManager didn't keep them,
  the connection is deleted. Status comes from `/sys/class/net/<if>/statistics`,
  which is readable as a normal user. The handshake time comes from `wg show`
  only when that is permitted.
- **NetworkManager over D-Bus** (`nm_dbus.py`, via the pure-Python `jeepney`):
  used inside the Flatpak and wherever `nmcli` is missing (`CATFLAP_BACKEND`
  overrides the choice). It builds the connection directly from settings with
  `AddConnection`, so no `.conf` file is written at all, then reads it back with
  `GetSettings` and applies the same `check_settings()` verification as the nmcli
  backend. Local routes and addresses come from each device's `IP4Config`, and
  pings use an unprivileged ICMP socket.
- **Windows** (under development): `wireguard.exe /installtunnelservice` on
  connect and `/uninstalltunnelservice` on disconnect, elevated through UAC for
  those two calls only. Config files live in `%LOCALAPPDATA%\catflap\tunnels`,
  restricted to the user and SYSTEM.

## Flatpak

`packaging/flatpak/io.github.samschultzponsys.CatFlap.yml` builds on the KDE 6.11
runtime with Flathub's `io.qt.PySide.BaseApp`. `trim.sh` removes what CatFlap
doesn't use (LLVM/clang, PyOpenGL, FFmpeg, every Qt binding except
Core/Gui/Widgets/DBus), taking the app from about 314 MB to 50 MB installed and a
12 MB bundle. Python dependencies are pinned wheels with checksums, listed in
`python-deps.json` and regenerated with `gen-python-deps.py`. The sandbox
permissions and why each is needed are listed in the README.

## Security model

- CatFlap runs as the user. Privileged changes go through NetworkManager and polkit
  (Linux) or UAC (Windows).
- Secrets live in the OS keyring. A 0600 file is used only when explicitly requested.
- Gateway TLS uses a pinned SHA-256 fingerprint (trust on first use, with the
  fingerprint shown to the user), a CA file, or, only with `--insecure`, no verification.
- Exports are encrypted with scrypt and AES-256-GCM. A plaintext export needs an
  explicit flag.
- Update checks contact only `api.github.com` for this repository's latest
  release. The pre-flight "on-site" check asks a public-IP service, configurable
  with the `public_ip_url` setting; set it to `""` to disable.

## NAT traversal options

For CGNAT sites (problem #10), still open:

1. **Relay on your VPS:** the gateway and the laptop both dial out to a WireGuard
   hub on the VPS. This is simple, works everywhere, and all traffic passes through
   the VPS. CatFlap could provision the hub as just another "gateway" driver.
2. **Headscale + Tailscale clients:** real NAT traversal with DERP fallback. This is
   the most Teleport-like option, but it adds a coordination server and a client
   daemon on the gateway, which UniFi, Omada and Alta can't run natively.
3. **Document the limitation:** ask the ISP for a public IPv4 address, or use IPv6
   inbound where available.

The current recommendation is (1), as a future `vps` driver, because it keeps
CatFlap's model unchanged.

## Roadmap

- Milestone 2: Omada Open API driver; .deb package; Windows testing and installer;
  possibly a Flatpak remote (OSTree repo) so `flatpak update` works without bundles.
- Milestone 3: Alta Labs driver; VPS relay driver for CGNAT sites; privileged
  helper (polkit) for handshake age without root.
