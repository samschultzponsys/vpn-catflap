"""UniFi driver.

Two ways to reach a console:

* **Cloud (default):** an account-level *Site Manager* API key from
  unifi.ui.com. Requests go through Ubiquiti's cloud connector,
  ``https://api.ui.com/v1/connector/consoles/<console id>/proxy/network/...``,
  so the console never has to be reachable from where you are. One key covers
  every console the account owns. :func:`list_cloud_consoles` finds them.
* **Direct:** the console's own address and a console-level Network API key
  (Settings > Control Plane > Integrations). Only works on the local network.

On top of either, two APIs are used, each where it is strongest:

* **Official Network integration API** (``/proxy/network/integration/v1``,
  ``X-API-KEY``): sites, networks (with subnets) and the list of VPN servers.
  It is stable and documented, but (as of Network 10.1) it does not expose a
  WireGuard server's port/public key or let you manage its peers.
* **Classic/internal API** (``/proxy/network/api`` and ``/proxy/network/v2/api``):
  WireGuard server details, peers (create/revoke), WAN IP and DDNS. Used as a
  fallback, and for everything the integration API cannot do. The same API key
  is tried first; a local admin username/password is used if the key is
  refused (cloud/SSO accounts do not work on the local API).

The peer endpoints are not officially documented and have changed between
Network versions, so every call has a clear error that points the user at the
``catflap import`` fallback.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any, Callable

from .. import keys
from ..http import HttpError, JsonClient
from ..model import ClientRecord, Network, PeerConfig, Site, WanInfo, WireGuardServer
from .base import Capabilities, Driver, DriverError, GatewayConfig, NotSupported

ClientFactory = Callable[[GatewayConfig, dict[str, str]], JsonClient]


def _default_client(gw: GatewayConfig, headers: dict[str, str]) -> JsonClient:
    return JsonClient(gw.url, headers=headers, fingerprint=gw.fingerprint,
                      ca_file=gw.ca_file, insecure=gw.insecure)


CLOUD_API = "https://api.ui.com"


def cloud_url(console_id: str) -> str:
    return f"{CLOUD_API}/v1/connector/consoles/{console_id}"


def list_cloud_consoles(api_key: str, client: JsonClient | None = None) -> list[dict[str, str]]:
    """Consoles on the unifi.ui.com account that owns ``api_key``: [{id, name}]."""
    c = client or JsonClient(CLOUD_API, headers={"X-API-KEY": api_key})
    out: list[dict[str, str]] = []
    token = None
    for _ in range(20):                          # pagination guard
        params = {"pageSize": 100, **({"nextToken": token} if token else {})}
        try:
            resp = c.get("/v1/hosts", params=params)
        except HttpError as e:
            if e.status in (401, 403):
                raise DriverError(
                    "unifi.ui.com refused the API key. Use a key from unifi.ui.com (Settings > "
                    "API Keys), created by the account that owns the console.") from e
            raise
        for h in (resp or {}).get("data", []):
            if h.get("type") not in (None, "console", "network-server"):
                continue
            rs = h.get("reportedState") or {}
            name = rs.get("name") or rs.get("hostname") or (h.get("userData") or {}).get("name") \
                or h.get("ipAddress") or h.get("id", "?")
            model = (rs.get("hardware") or {}).get("name") or rs.get("model") or ""
            out.append({"id": h.get("id", ""), "name": name, "model": model,
                        "online": str(rs.get("state", "connected")) in ("connected", "online")})
        token = (resp or {}).get("nextToken")
        if not token:
            break
    return [h for h in out if h["id"]]


def _first(d: dict[str, Any], *names: str) -> Any:
    for n in names:
        if d.get(n) not in (None, ""):
            return d[n]
    return None


class UniFiDriver(Driver):
    vendor = "unifi"
    display_name = "UniFi"
    capabilities = Capabilities(list_sites=True, list_networks=True, read_server=True,
                                create_client=True, revoke_client=True, wan_info=True)

    def __init__(self, gw: GatewayConfig, secrets: dict[str, str | None],
                 client_factory: ClientFactory = _default_client):
        super().__init__(gw, secrets)
        self._factory = client_factory
        opts = gw.options or {}
        self.cloud = opts.get("mode") == "cloud"
        # Self-hosted Network Application (no UniFi OS) has no /proxy/network prefix.
        self.prefix = "" if opts.get("legacy_controller") else "/proxy/network"
        api_key = secrets.get("api_key")
        self._key_headers = {"X-API-KEY": api_key} if api_key else {}
        self._integration = client_factory(gw, dict(self._key_headers))
        self._classic = client_factory(gw, dict(self._key_headers))
        self._logged_in = False

    # ------------------------------------------------------------------ plumbing
    def _int_get(self, path: str, **kw: Any) -> Any:
        if not self._key_headers:
            raise NotSupported("no API key configured for the integration API")
        try:
            return self._integration.get(f"{self.prefix}/integration{path}", **kw)
        except HttpError as e:
            if e.status in (401, 403):
                raise DriverError(
                    "UniFi rejected the API key. Create one under Settings > Control Plane > "
                    "Integrations and run 'catflap gateway set-key'.") from e
            raise

    def _paged(self, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._int_get(path, params={"offset": offset, "limit": 200})
            data = page.get("data", []) if isinstance(page, dict) else []
            out.extend(data)
            total = page.get("totalCount", len(out)) if isinstance(page, dict) else len(out)
            if not data or len(out) >= total:
                return out
            offset += len(data)

    def _login(self) -> None:
        if self.cloud:
            raise DriverError(
                "UniFi's cloud connector refused this request. Ubiquiti sometimes limits the "
                "cloud connector to the official API; if it keeps happening, add the site with "
                "a downloaded WireGuard file instead (Add a site > UniFi > 'use a file').")
        user, pw = self.gw.username, self.secrets.get("password")
        if not (user and pw):
            raise DriverError(
                "the UniFi classic API refused the API key and no local admin username/password "
                "is configured. Add one with 'catflap gateway add --username' (must be a LOCAL "
                "account, not a UI.com/SSO account), or download the client .conf from the UI and "
                "use 'catflap import'.")
        login_path = "/api/login" if not self.prefix else "/api/auth/login"
        self._classic = self._factory(self.gw, {})
        try:
            _, headers, _ = self._classic.request(
                "POST", login_path, body={"username": user, "password": pw, "remember": False})
        except HttpError as e:
            raise DriverError(f"UniFi login failed for local user {user!r}: {e}") from e
        csrf = headers.get("x-csrf-token") or headers.get("x-updated-csrf-token")
        if csrf:
            self._classic.headers["X-CSRF-Token"] = csrf
        self._logged_in = True

    def _classic_call(self, method: str, path: str, body: Any = None) -> Any:
        for attempt in (0, 1):
            try:
                return self._classic.request(method, f"{self.prefix}{path}", body=body)[2]
            except HttpError as e:
                if e.status in (401, 403) and attempt == 0 and not self._logged_in:
                    self._login()
                    continue
                raise
        raise AssertionError("unreachable")

    def _classic_data(self, path: str) -> list[dict[str, Any]]:
        resp = self._classic_call("GET", path)
        if isinstance(resp, dict) and "data" in resp:
            return resp["data"]
        return resp if isinstance(resp, list) else []

    # ------------------------------------------------------------------ sites
    def list_sites(self) -> list[Site]:
        try:
            return [Site(id=s["id"], name=s.get("name") or s.get("internalReference", s["id"]),
                         ref=s.get("internalReference", "default"))
                    for s in self._paged("/v1/sites")]
        except NotSupported:
            pass
        # Classic fallback: /api/self/sites
        return [Site(id=s.get("_id", s["name"]), name=s.get("desc") or s["name"], ref=s["name"])
                for s in self._classic_data("/api/self/sites")]

    # ------------------------------------------------------------------ networks
    def list_networks(self, site: Site) -> list[Network]:
        try:
            return self._integration_networks(site)
        except (NotSupported, HttpError):
            return self._classic_networks(site)

    def _integration_networks(self, site: Site) -> list[Network]:
        nets: list[Network] = []
        for n in self._paged(f"/v1/sites/{site.id}/networks"):
            if not n.get("enabled", True):
                continue
            detail = self._int_get(f"/v1/sites/{site.id}/networks/{n['id']}")
            v4 = (detail or {}).get("ipv4Configuration") or {}
            host, plen = v4.get("hostIpAddress"), v4.get("prefixLength")
            if not host or plen is None:
                continue  # e.g. unmanaged/third-party-gateway networks
            cidr = str(ipaddress.ip_interface(f"{host}/{plen}").network)
            nets.append(Network(name=n["name"], cidr=cidr, vlan=n.get("vlanId"),
                                gateway_ip=host, id=n["id"], purpose=n.get("management", "")))
        return nets

    def _classic_networks(self, site: Site) -> list[Network]:
        nets = []
        for n in self._classic_data(f"/api/s/{site.ref}/rest/networkconf"):
            if n.get("purpose") not in ("corporate", "guest", "vlan-only") or not n.get("ip_subnet"):
                continue
            if n.get("enabled") is False:
                continue
            iface = ipaddress.ip_interface(n["ip_subnet"])
            nets.append(Network(name=n.get("name", "?"), cidr=str(iface.network),
                                vlan=n.get("vlan") if n.get("vlan_enabled") else None,
                                gateway_ip=str(iface.ip), id=n.get("_id", ""),
                                purpose=n.get("purpose", "")))
        return nets

    # ------------------------------------------------------------------ WireGuard server
    def _classic_wg_servers(self, site: Site) -> list[dict[str, Any]]:
        return [n for n in self._classic_data(f"/api/s/{site.ref}/rest/networkconf")
                if n.get("purpose") == "remote-user-vpn"
                and n.get("vpn_type") in ("wireguard-server", "wireguard")]

    def get_wireguard_server(self, site: Site, name: str | None = None) -> WireGuardServer:
        integration_names: list[str] | None = None
        try:
            servers = self._paged(f"/v1/sites/{site.id}/vpn/servers")
            integration_names = [s.get("name", "") for s in servers
                                 if "WIREGUARD" in str(s.get("type", "")).upper()]
        except (NotSupported, HttpError):
            pass

        classic = self._classic_wg_servers(site)
        if name:
            classic = [c for c in classic if c.get("name") == name or c.get("_id") == name]
        if not classic:
            hint = ""
            if integration_names:
                hint = (f" (the integration API lists {', '.join(integration_names)}, but it has "
                        "no applied configuration)")
            raise DriverError(
                "No WireGuard VPN server exists on this site" + hint + ". In UniFi go to "
                "Settings > VPN > VPN Server > Create New, choose WireGuard, and click "
                "'Create'/'Apply Changes' BEFORE adding clients. Generating a client without "
                "creating the server gives a tunnel with no handshake.")
        if len(classic) > 1:
            raise DriverError("several WireGuard servers exist on this site; choose one with "
                              "--server: " + ", ".join(c.get("name", c.get("_id", "?")) for c in classic))
        c = classic[0]
        subnet_raw = _first(c, "ip_subnet", "wireguard_ip_subnet")
        server_addr = subnet = None
        if subnet_raw:
            iface = ipaddress.ip_interface(subnet_raw)
            server_addr, subnet = str(iface.ip), str(iface.network)
        pub = _first(c, "wireguard_public_key", "x_wireguard_public_key", "public_key")
        priv = c.get("x_wireguard_private_key")
        if not pub and priv and keys.is_valid_key(priv):
            pub = keys.public_from_private(priv)
        port = _first(c, "local_port", "wireguard_local_port", "port")
        enabled = c.get("enabled", True)
        return WireGuardServer(
            id=c.get("_id", ""), name=c.get("name", "WireGuard"), enabled=bool(enabled),
            port=int(port) if port else None, subnet=subnet, server_address=server_addr,
            public_key=pub, created=bool(c.get("_id")) and bool(pub),
        )

    # ------------------------------------------------------------------ WAN / DDNS
    def get_wan_info(self, site: Site) -> WanInfo:
        info = WanInfo()
        try:
            for sub in self._classic_data(f"/api/s/{site.ref}/stat/health"):
                if sub.get("subsystem") == "wan":
                    info.wan_ip = sub.get("wan_ip") or (sub.get("uptime_stats") or {}).get("wan_ip")
        except (HttpError, DriverError):
            pass
        try:
            for d in self._classic_data(f"/api/s/{site.ref}/rest/dynamicdns"):
                if d.get("host_name"):
                    info.ddns_hostname = d["host_name"]
                    break
        except (HttpError, DriverError):
            pass
        return info

    # ------------------------------------------------------------------ peers
    def _users_path(self, site: Site, server: WireGuardServer) -> str:
        return f"/v2/api/site/{site.ref}/wireguard/{server.id}/users"

    def list_clients(self, site: Site, server: WireGuardServer) -> list[ClientRecord]:
        try:
            resp = self._classic_call("GET", self._users_path(site, server))
        except HttpError as e:
            raise DriverError(f"could not list WireGuard clients ({e}); this UniFi Network "
                              "version may use a different API — use 'catflap import'") from e
        items = resp.get("data", resp) if isinstance(resp, dict) else resp
        out = []
        for u in items or []:
            out.append(ClientRecord(id=u.get("_id") or u.get("id", ""), name=u.get("name", ""),
                                    address=u.get("interface_ip", ""),
                                    public_key=u.get("public_key", "")))
        return out

    def create_client(self, site: Site, server: WireGuardServer, device_name: str,
                      public_key: str, address: str) -> ClientRecord:
        ip = str(ipaddress.ip_interface(address).ip)
        body = [{"name": device_name, "interface_ip": ip, "public_key": public_key}]
        last: Exception | None = None
        for suffix in ("/batch_add", "/batch"):
            try:
                resp = self._classic_call("POST", self._users_path(site, server) + suffix, body)
                break
            except HttpError as e:
                last = e
                if e.status in (404, 405):
                    continue
                raise DriverError(f"UniFi refused to create the client: {e}") from e
        else:
            raise DriverError(f"this UniFi Network version has no known endpoint to create "
                              f"WireGuard clients ({last}); create it in the UI and use "
                              "'catflap import'")
        items = resp.get("data", resp) if isinstance(resp, dict) else resp
        created = (items or [{}])[0] if isinstance(items, list) else {}
        cid = created.get("_id") or created.get("id")
        if not cid:  # some versions return nothing useful; look it up
            for c in self.list_clients(site, server):
                if c.public_key == public_key:
                    return c
            raise DriverError("UniFi accepted the client but did not return it; check the UI")
        return ClientRecord(id=cid, name=device_name, address=f"{ip}/32", public_key=public_key)

    def revoke_client(self, site: Site, server: WireGuardServer, client_id: str) -> None:
        try:
            self._classic_call("POST", self._users_path(site, server) + "/batch_delete",
                               [client_id])
        except HttpError as e:
            raise DriverError(f"could not revoke client {client_id}: {e}; delete it in the "
                              "UniFi UI (Settings > VPN > VPN Server > clients)") from e

    # ------------------------------------------------------------------ .conf quirks
    def parse_config(self, path: str | Path) -> PeerConfig:
        """UniFi .conf files: full tunnel, DNS=gateway, raw WAN IP endpoint and
        no keepalive. Parsing is plain; :mod:`catflap.safety` fixes the rest."""
        cfg = super().parse_config(path)
        cfg.addresses = [a if "/" in a else f"{a}/32" for a in cfg.addresses]
        return cfg
