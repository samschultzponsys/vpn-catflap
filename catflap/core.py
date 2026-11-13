"""Application service layer shared by the CLI and the tray app.

All workflows live here (provision, import, connect, revoke, export...). The
CLI and tray only collect input and display results.
"""

from __future__ import annotations

import datetime as _dt
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from . import bundle, checks, keys, netutil, safety
from .drivers import DriverError, GatewayConfig, NotSupported, get_driver_class
from .drivers.base import Driver
from .model import PALETTE, Network, PeerConfig, Profile, Site
from .platform.base import Backend, TunnelStatus, get_backend
from .secrets import SecretStore, gateway_key, profile_key
from .store import Store

GATEWAY_SECRETS = ("api_key", "password")
PROFILE_SECRETS = ("private_key", "preshared_key")


class CatflapError(RuntimeError):
    pass


@dataclass
class Outcome:
    ok: bool
    findings: list[checks.Finding] = field(default_factory=list)
    profile: Profile | None = None
    messages: list[str] = field(default_factory=list)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def default_device_name() -> str:
    return netutil.sanitize_ifname(socket.gethostname().split(".")[0], prefix="")[:32] or "device"


class Manager:
    def __init__(self, store: Store | None = None, secrets: SecretStore | None = None,
                 backend: Backend | None = None,
                 driver_factory: Callable[[GatewayConfig, dict[str, str | None]], Driver] | None = None):
        self.store = store or Store()
        self.secrets = secrets or SecretStore()
        self._backend = backend
        self._driver_factory = driver_factory
        self.resolver: Callable[[str], list[str]] = checks.resolve

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = get_backend()
        return self._backend

    # ================================================================ gateways
    def add_gateway(self, name: str, vendor: str, url: str = "", api_key: str | None = None,
                    username: str | None = None, password: str | None = None,
                    fingerprint: str | None = None, ca_file: str | None = None,
                    insecure: bool = False, options: dict[str, Any] | None = None) -> GatewayConfig:
        get_driver_class(vendor)  # validates vendor
        if not name or netutil.slug(name, 64) != name.lower():
            raise CatflapError("gateway names may only use letters, digits, '-' and '_'")
        gw = GatewayConfig(name=name, vendor=vendor, url=url.rstrip("/"), username=username,
                           fingerprint=fingerprint, ca_file=ca_file, insecure=insecure,
                           options=options or {})
        self.store.put_gateway(name, gw.to_dict())
        if api_key:
            self.secrets.set(gateway_key(name, "api_key"), api_key)
        if password:
            self.secrets.set(gateway_key(name, "password"), password)
        self.store.save()
        return gw

    def add_unifi_cloud_gateway(self, api_key: str, console_id: str, console_name: str = "") -> str:
        """Save a UniFi console reached through unifi.ui.com's cloud connector.

        Re-adding the same console updates its key instead of duplicating it.
        """
        from .drivers.unifi import cloud_url
        for n, g in self.store.gateways().items():
            if (g.get("options") or {}).get("console_id") == console_id:
                self.secrets.set(gateway_key(n, "api_key"), api_key)
                return n
        name = self.unique_gateway_name(f"unifi-{console_name or console_id}")
        self.add_gateway(name, "unifi", cloud_url(console_id), api_key=api_key,
                         options={"mode": "cloud", "console_id": console_id,
                                  "console_name": console_name})
        return name

    def remove_gateway(self, name: str) -> None:
        users = [p.name for p in self.store.profiles().values() if p.gateway == name]
        if users:
            raise CatflapError(f"gateway {name!r} is used by: {', '.join(users)}; remove those first")
        self.store.gateway(name)
        for s in GATEWAY_SECRETS:
            self.secrets.delete(gateway_key(name, s))
        self.store.remove_gateway(name)
        self.store.save()

    def set_gateway_secret(self, name: str, what: str, value: str) -> None:
        self.store.gateway(name)
        self.secrets.set(gateway_key(name, what), value)

    def driver(self, gateway: str) -> Driver:
        gw = GatewayConfig.from_dict(gateway, self.store.gateway(gateway))
        sec = {s: self.secrets.get(gateway_key(gateway, s)) for s in GATEWAY_SECRETS}
        if self._driver_factory:
            return self._driver_factory(gw, sec)
        return get_driver_class(gw.vendor)(gw, sec)

    def list_sites(self, gateway: str) -> list[Site]:
        return self.driver(gateway).list_sites()

    def list_networks(self, gateway: str, site: str | None = None) -> tuple[Site, list[Network]]:
        d = self.driver(gateway)
        s = d.find_site(site)
        return s, d.list_networks(s)

    # ================================================================ helpers
    def unique_profile_name(self, label: str) -> str:
        """A CLI-safe profile name from a friendly label ("Ponsys Office" -> "ponsys-office")."""
        base = netutil.slug(label, 32) or "site"
        taken = set(self.store.profiles())
        name, i = base, 2
        while name in taken:
            name = f"{base}-{i}"
            i += 1
        return name

    def unique_gateway_name(self, label: str) -> str:
        base = netutil.slug(label, 32) or "gateway"
        taken = set(self.store.gateways())
        name, i = base, 2
        while name in taken:
            name = f"{base}-{i}"
            i += 1
        return name

    def _taken_ifnames(self, exclude: str | None = None) -> list[str]:
        return [p.ifname for p in self.store.profiles().values() if p.name != exclude]

    def _next_color(self) -> str:
        used = {p.color for p in self.store.profiles().values()}
        for c in PALETTE:
            if c not in used:
                return c
        return PALETTE[len(used) % len(PALETTE)]

    @staticmethod
    def select_networks(networks: list[Network], wanted: list[str]) -> list[Network]:
        """Match by name (case-insensitive), CIDR or VLAN id."""
        out: list[Network] = []
        for w in wanted:
            hit = [n for n in networks if w.lower() == n.name.lower() or w == n.cidr
                   or (n.vlan is not None and w == str(n.vlan))]
            if not hit:
                raise CatflapError(f"network {w!r} not found; available: "
                                   + ", ".join(f"{n.name} ({n.cidr})" for n in networks))
            out += [h for h in hit if h not in out]
        return out

    def _peer_config(self, p: Profile) -> PeerConfig:
        priv = self.secrets.get(profile_key(p.name, "private_key"))
        if not priv:
            raise CatflapError(f"private key for '{p.name}' is missing from the keyring "
                               f"({self.secrets.backend_name}); re-import or re-provision it")
        return PeerConfig(private_key=priv, addresses=p.addresses,
                          peer_public_key=p.peer_public_key, endpoint_host=p.endpoint_host,
                          endpoint_port=p.endpoint_port, allowed_ips=p.routes,
                          preshared_key=self.secrets.get(profile_key(p.name, "preshared_key")),
                          keepalive=p.keepalive, mtu=p.mtu)

    def _finalize(self, p: Profile, cfg: PeerConfig, install: bool) -> Profile:
        """Store secrets + profile and install the OS connection."""
        others = {n: o.vpn_subnet for n, o in self.store.profiles().items()}
        p.warnings += safety.check_vpn_subnet_collisions(p.name, p.vpn_subnet, others)
        self.secrets.set(profile_key(p.name, "private_key"), cfg.private_key)
        if cfg.preshared_key:
            self.secrets.set(profile_key(p.name, "preshared_key"), cfg.preshared_key)
        if install:
            p.backend_id = self.backend.install(p, cfg)
            p.installed_routes = list(cfg.allowed_ips)
        self.store.put_profile(p)
        self.store.save()
        return p

    def _check_new_name(self, name: str, replace: bool) -> Profile | None:
        if not name or netutil.slug(name, 64) != name.lower():
            raise CatflapError("profile names may only use letters, digits, '-' and '_'")
        existing = self.store.profiles().get(name)
        if existing and not replace:
            raise CatflapError(f"profile {name!r} already exists (use --replace)")
        return existing

    # ================================================================ provision
    def provision(self, gateway: str, name: str, networks: list[str], site: str | None = None,
                  device: str | None = None, endpoint: str | None = None,
                  server: str | None = None, dns_domain: str | None = None,
                  dns_servers: list[str] | None = None, replace: bool = False,
                  install: bool = True, color: str | None = None,
                  label: str | None = None) -> Outcome:
        existing = self._check_new_name(name, replace)
        d = self.driver(gateway)
        s = d.find_site(site)
        srv = d.get_wireguard_server(s, server) if server else d.get_wireguard_server(s)
        if not srv.enabled:
            raise CatflapError(f"WireGuard server '{srv.name}' is disabled on the gateway; enable it first")
        if not (srv.created and srv.public_key and srv.port):
            raise CatflapError(
                f"WireGuard server '{srv.name}' is not fully created (missing "
                + ", ".join(k for k, v in (("public key", srv.public_key), ("port", srv.port)) if not v)
                + "). Click 'Create'/'Apply Changes' for the server in the gateway UI first.")
        all_nets = d.list_networks(s)
        chosen = self.select_networks(all_nets, networks)
        if not chosen:
            raise CatflapError("choose at least one site network to route (--network)")

        wan = d.get_wan_info(s)
        messages: list[str] = []
        ep = endpoint or wan.ddns_hostname or wan.wan_ip
        if not ep:
            raise CatflapError("could not determine the site's public address; pass --endpoint <ddns-host>")
        if not endpoint and wan.ddns_hostname:
            messages.append(f"Using the gateway's DDNS hostname {wan.ddns_hostname} as endpoint.")
        if wan.wan_ip and netutil.is_cgnat(wan.wan_ip):
            messages.append(f"WARNING: gateway WAN IP {wan.wan_ip} is CGNAT; the tunnel will not "
                            "be reachable from outside (problem #10).")

        dev = device or default_device_name()
        client_name = f"catflap-{dev}-{name}"[:64]
        clients = d.list_clients(s, srv)
        dupes = [c for c in clients if c.name == client_name]
        if dupes and not replace:
            raise CatflapError(f"the gateway already has a client {client_name!r} for this device; "
                               "use --replace to revoke and recreate it")
        for c in dupes:
            d.revoke_client(s, srv, c.id)
            clients.remove(c)

        priv, pub = keys.generate_keypair()
        address = d.allocate_address(srv, [c.address for c in clients])
        rec = d.create_client(s, srv, client_name, pub, address)
        try:
            routes = [n.cidr for n in chosen]
            if srv.server_address:
                routes.append(f"{srv.server_address}/32")
            raw = PeerConfig(private_key=priv, addresses=[rec.address or address],
                             peer_public_key=srv.public_key, endpoint_host=ep,
                             endpoint_port=srv.port, allowed_ips=routes)
            res = safety.make_safe(raw, routes)
            if existing:
                already = {c.id for c in dupes} | {rec.id}
                self._teardown(existing, revoke=existing.gateway == gateway
                               and existing.client_id not in already)
            p = Profile(
                name=name, ifname=netutil.unique_ifname(name, self._taken_ifnames(name)),
                vendor=d.vendor, addresses=res.config.addresses,
                peer_public_key=srv.public_key, endpoint_host=ep, endpoint_port=srv.port,
                routes=res.config.allowed_ips, gateway=gateway, site_id=s.id, site_name=s.name,
                client_id=rec.id, device=dev, vpn_subnet=srv.subnet,
                ping_target=srv.server_address or chosen[0].gateway_ip,
                networks=[n.__dict__ for n in chosen], wan_ip=wan.wan_ip,
                warnings=res.warnings, created=_now(), color=color or self._next_color(),
                label=label or "",
            )
            self._apply_dns(p, dns_domain, dns_servers)
            p = self._finalize(p, res.config, install)
        except Exception:
            try:
                d.revoke_client(s, srv, rec.id)   # don't leave an orphaned peer behind
            except Exception:
                pass
            raise
        return Outcome(True, profile=p, messages=messages + res.warnings)

    def _apply_dns(self, p: Profile, dns_domain: str | None, dns_servers: list[str] | None) -> None:
        if dns_domain:
            p.dns_domain = safety.validate_dns_domain(dns_domain)
            servers = dns_servers or ([n["gateway_ip"] for n in p.networks if n.get("gateway_ip")][:1])
            if not servers:
                raise CatflapError("--dns-domain needs --dns-server <ip>")
            for sv in servers:
                if not any(netutil.overlaps(f"{sv}/32", r) for r in p.routes):
                    raise CatflapError(f"DNS server {sv} is not inside a routed network; "
                                       "it would be unreachable through the tunnel")
            p.dns_servers = servers
        elif dns_servers:
            raise CatflapError("--dns-server requires --dns-domain (catflap never routes all DNS "
                               "through a tunnel)")

    # ================================================================ import .conf
    def import_conf(self, path: str | Path, name: str, vendor: str = "generic",
                    routes: list[str] | None = None, networks: list[str] | None = None,
                    gateway: str | None = None, site: str | None = None,
                    endpoint: str | None = None, dns_domain: str | None = None,
                    dns_servers: list[str] | None = None, replace: bool = False,
                    install: bool = True, color: str | None = None,
                    label: str | None = None) -> Outcome:
        if gateway:
            drv = self.driver(gateway)
            vendor = drv.vendor
        else:
            drv = get_driver_class(vendor)(GatewayConfig(name="", vendor=vendor), {})
        cfg = drv.parse_config(path)
        return self.import_peer(cfg, name, vendor=vendor, routes=routes, networks=networks,
                                gateway=gateway, site=site, endpoint=endpoint,
                                dns_domain=dns_domain, dns_servers=dns_servers, replace=replace,
                                install=install, color=color, label=label, _driver=drv)

    def manual_peer(self, private_key: str, server_public_key: str, endpoint: str, port: int,
                    address: str) -> PeerConfig:
        """Build a peer from details typed in by the user (routers without config files)."""
        ip = address.strip()
        if "/" not in ip:
            ip += "/32"
        try:
            netutil.net(ip)
        except ValueError:
            raise CatflapError(f"'{address}' isn't a valid IP address for this computer") from None
        if not keys.is_valid_key(server_public_key.strip()):
            raise CatflapError("the router's public key doesn't look right: it should be 44 "
                               "characters ending in '='")
        if not endpoint.strip():
            raise CatflapError("enter the site's internet address or DDNS name")
        return PeerConfig(private_key=private_key, addresses=[ip],
                          peer_public_key=server_public_key.strip(),
                          endpoint_host=endpoint.strip(), endpoint_port=int(port), allowed_ips=[])

    def import_peer(self, cfg: PeerConfig, name: str, vendor: str = "generic",
                    routes: list[str] | None = None, networks: list[str] | None = None,
                    gateway: str | None = None, site: str | None = None,
                    endpoint: str | None = None, dns_domain: str | None = None,
                    dns_servers: list[str] | None = None, replace: bool = False,
                    install: bool = True, color: str | None = None,
                    label: str | None = None, _driver: Driver | None = None) -> Outcome:
        """Create a site from a WireGuard peer config (parsed from a file or typed in)."""
        existing = self._check_new_name(name, replace)
        drv = _driver or (self.driver(gateway) if gateway else
                          get_driver_class(vendor)(GatewayConfig(name="", vendor=vendor), {}))
        if not keys.is_valid_key(cfg.private_key) or not keys.is_valid_key(cfg.peer_public_key):
            raise CatflapError("the WireGuard config has an invalid PrivateKey or peer PublicKey")

        # One client per device (#11): the same key must not be imported twice.
        my_pub = keys.public_from_private(cfg.private_key)
        for other in self.store.profiles().values():
            if other.name == name:
                continue
            opriv = self.secrets.get(profile_key(other.name, "private_key"))
            if opriv and keys.public_from_private(opriv) == my_pub:
                raise CatflapError(f"this client key is already used by profile '{other.name}'. "
                                   "Each device and site needs its own client; create a new one "
                                   "on the gateway.")

        chosen_nets: list[Network] = []
        s: Site | None = None
        if networks:
            if not gateway:
                raise CatflapError("--network needs --gateway (to look the networks up); "
                                   "use --route <cidr> otherwise")
            s = drv.find_site(site)
            chosen_nets = self.select_networks(drv.list_networks(s), networks)
        want = [n.cidr for n in chosen_nets] + list(routes or [])
        res = safety.make_safe(cfg, want or None)
        if endpoint:
            res.config.endpoint_host = endpoint

        vpn_subnet = None
        ping_target = chosen_nets[0].gateway_ip if chosen_nets else netutil.first_host(res.config.allowed_ips[0])
        if existing:
            self._teardown(existing, revoke=False)
        p = Profile(
            name=name, ifname=netutil.unique_ifname(name, self._taken_ifnames(name)), vendor=vendor,
            addresses=res.config.addresses, peer_public_key=res.config.peer_public_key,
            endpoint_host=res.config.endpoint_host, endpoint_port=res.config.endpoint_port,
            routes=res.config.allowed_ips, gateway=gateway, site_id=s.id if s else None,
            site_name=s.name if s else None, device=default_device_name(), vpn_subnet=vpn_subnet,
            ping_target=ping_target, networks=[n.__dict__ for n in chosen_nets],
            has_psk=bool(res.config.preshared_key), mtu=res.config.mtu,
            warnings=res.warnings, created=_now(), color=color or self._next_color(),
            label=label or "",
        )
        self._apply_dns(p, dns_domain, dns_servers)
        p = self._finalize(p, res.config, install)
        msgs = [f"Removed/fixed: {r}" for r in res.removed] + res.warnings
        return Outcome(True, profile=p, messages=msgs)

    # ================================================================ edit / apply
    def update_profile(self, name: str, endpoint: str | None = None,
                       routes: list[str] | None = None, color: str | None = None,
                       dns_domain: str | None = None, dns_servers: list[str] | None = None,
                       clear_dns: bool = False, ping_target: str | None = None,
                       label: str | None = None) -> Profile:
        p = self.store.profile(name)
        cfg = self._peer_config(p)
        if endpoint:
            p.endpoint_host = endpoint
        if routes is not None:
            res = safety.make_safe(cfg, routes)
            p.routes = res.config.allowed_ips
        if color:
            p.color = color
        if label is not None:
            p.label = label
        if ping_target:
            p.ping_target = ping_target
        if clear_dns:
            p.dns_domain, p.dns_servers = None, []
        elif dns_domain or dns_servers:
            self._apply_dns(p, dns_domain or p.dns_domain, dns_servers)
        needs_reinstall = bool(endpoint or routes is not None or dns_domain or dns_servers or clear_dns)
        if needs_reinstall:
            return self.apply(p)
        self.store.put_profile(p)
        self.store.save()
        return p

    def apply(self, p: Profile | str) -> Profile:
        """(Re)create the OS connection from the stored profile. Idempotent."""
        if isinstance(p, str):
            p = self.store.profile(p)
        cfg = self._peer_config(p)
        cfg.allowed_ips = p.routes
        cfg.endpoint_host = p.endpoint_host
        res = safety.make_safe(cfg, p.routes)
        p.backend_id = self.backend.install(p, res.config)
        p.installed_routes = res.config.allowed_ips
        self.store.put_profile(p)
        self.store.save()
        return p

    # ================================================================ connect
    def others_state(self) -> dict[str, tuple[Profile, bool]]:
        out = {}
        for n, p in self.store.profiles().items():
            try:
                active = self.backend.status(p).active
            except Exception:
                active = False
            out[n] = (p, active)
        return out

    def preflight(self, name: str, online_checks: bool = True) -> list[checks.Finding]:
        p = self.store.profile(name)
        ip_url = self.store.settings().get("public_ip_url", "https://api.ipify.org")
        my_ip = (lambda: checks.public_ip(ip_url)) if online_checks and ip_url else None
        reach = (lambda h: self.backend.ping(h, 1, 1)) if online_checks else None
        return checks.preflight(p, self.backend.local_network(), self.others_state(),
                                resolver=self.resolver, my_public_ip=my_ip, reachable=reach)

    def connect(self, name: str, force: bool = False, keep_on_failure: bool = False,
                switch: bool = False) -> Outcome:
        """Connect a site. With ``switch``, sites that can't be up at the same time
        (they use the same addresses) are disconnected first."""
        p = self.store.profile(name)
        if self.backend.status(p).active:
            return Outcome(True, [checks.Finding(checks.INFO, "already-up", f"'{name}' is already connected.")], p)
        findings = self.preflight(name)
        clashes = [f for f in findings if f.code == "tunnel-overlap" and f.level == checks.ERROR]
        if switch and clashes and all(f.code == "tunnel-overlap" for f in findings if f.level == checks.ERROR):
            for other in sorted({f.other for f in clashes if f.other}):
                self.disconnect(other)
            findings = self.preflight(name)
        if checks.has_errors(findings) and not force:
            return Outcome(False, findings, p)
        # Same addresses here as at the site? Route around the local router (#8).
        routes, excluded = checks.conflict_safe_routes(p.routes, self.backend.local_network(), p.ifname)
        # Single addresses another connected site already routes (e.g. both sites' VPN
        # server at 192.168.3.1) stay with that tunnel.
        shared = {r for r in routes if netutil.net(r).prefixlen == netutil.net(r).max_prefixlen
                   and any(r in (o.installed_routes or o.routes)
                           for n, (o, act) in self.others_state().items() if act and n != name)}
        if shared:
            routes = [r for r in routes if r not in shared]
            excluded = sorted(set(excluded) | {r.split("/")[0] for r in shared})
        if routes != (p.installed_routes or p.routes):
            p = self._install_routes(p, routes)
        self.backend.up(p)
        self.backend.wait_active(p, 10)
        target = p.ping_target if p.ping_target not in excluded else None
        h = checks.health(replace(p, ping_target=target), self.backend,
                          wait=float(self.store.settings().get("health_wait", 8)))
        findings = [f for f in findings if f.code != "preflight-ok"] + h
        ok = not checks.has_errors(h)
        if not ok and not keep_on_failure:
            self.backend.down(p)
        return Outcome(ok, findings, p)

    def _install_routes(self, p: Profile, routes: list[str]) -> Profile:
        """(Re)create the OS connection with these routes, keeping the profile's own."""
        cfg = self._peer_config(p)
        res = safety.make_safe(cfg, routes)
        p.backend_id = self.backend.install(p, res.config)
        p.installed_routes = res.config.allowed_ips
        self.store.put_profile(p)
        self.store.save()
        return p

    def disconnect(self, name: str) -> None:
        self.backend.down(self.store.profile(name))

    def status(self, name: str | None = None) -> dict[str, TunnelStatus]:
        names = [name] if name else list(self.store.profiles())
        return {n: self.backend.status(self.store.profile(n)) for n in names}

    # ================================================================ revoke / remove
    def _teardown(self, p: Profile, revoke: bool) -> list[str]:
        msgs = []
        try:
            self.backend.down(p)
            self.backend.remove(p)
        except Exception as e:  # keep going: the user wants it gone
            msgs.append(f"could not remove the local connection: {e}")
        if revoke and p.gateway and p.client_id:
            d = self.driver(p.gateway)
            s = d.find_site(p.site_id)
            srv = d.get_wireguard_server(s)
            d.revoke_client(s, srv, p.client_id)
            msgs.append(f"Revoked client {p.client_id} on gateway '{p.gateway}'.")
        return msgs

    def remove(self, name: str, revoke: bool = False) -> list[str]:
        p = self.store.profile(name)
        if revoke and not (p.gateway and p.client_id):
            raise CatflapError(f"'{name}' was imported from a .conf; catflap can't revoke it on the "
                               "gateway. Delete the client in the gateway UI, then run "
                               f"'catflap remove {name}'.")
        try:
            msgs = self._teardown(p, revoke)
        except (DriverError, NotSupported) as e:
            raise CatflapError(f"revoking on the gateway failed: {e}. Nothing was removed.") from e
        for s in PROFILE_SECRETS:
            self.secrets.delete(profile_key(name, s))
        self.store.remove_profile(name)
        self.store.save()
        return msgs + [f"Removed profile '{name}'."]

    # ================================================================ export / import
    def export(self, passphrase: str | None, gateways_only: bool = False) -> str:
        data = self.store._data
        payload: dict[str, Any] = {
            "gateways": data["gateways"],
            "profiles": {} if gateways_only else data["profiles"],
            "settings": data.get("settings", {}),
            "secrets": {},
            "exported": _now(),
        }
        for g in data["gateways"]:
            for s in GATEWAY_SECRETS:
                v = self.secrets.get(gateway_key(g, s))
                if v:
                    payload["secrets"][gateway_key(g, s)] = v
        for n in payload["profiles"]:
            for s in PROFILE_SECRETS:
                v = self.secrets.get(profile_key(n, s))
                if v:
                    payload["secrets"][profile_key(n, s)] = v
        from .version import __version__
        payload["app_version"] = __version__
        return bundle.seal(payload, passphrase)

    # Fields that make two copies of a gateway / site "the same thing".
    _GW_KEYS = ("vendor", "url", "options", "username")
    _P_KEYS = ("peer_public_key", "endpoint_host", "endpoint_port", "routes", "addresses")

    def _same_gateway(self, name: str, cfg: dict, secrets: dict) -> bool:
        mine = self.store.gateways().get(name) or {}
        return (all(mine.get(k) == cfg.get(k) for k in self._GW_KEYS)
                and self.secrets.get(gateway_key(name, "api_key")) == secrets.get(gateway_key(name, "api_key")))

    def _same_profile(self, name: str, pd: dict, secrets: dict) -> bool:
        mine = self.store.profiles().get(name)
        if not mine:
            return False
        md = mine.to_dict()
        return (all(md.get(k) == pd.get(k) for k in self._P_KEYS)
                and self.secrets.get(profile_key(name, "private_key")) == secrets.get(profile_key(name, "private_key")))

    def preview_bundle(self, text: str, passphrase: str | None) -> dict[str, list[str]]:
        """What importing would do: new / identical / conflicting gateways and sites."""
        payload = bundle.open_bundle(text, passphrase)
        sec = payload.get("secrets", {})
        out: dict[str, list[str]] = {k: [] for k in ("new_gateways", "same_gateways", "conflict_gateways",
                                                     "new_sites", "same_sites", "conflict_sites")}
        for g, cfg in payload.get("gateways", {}).items():
            key = ("new_gateways" if g not in self.store.gateways() else
                   "same_gateways" if self._same_gateway(g, cfg, sec) else "conflict_gateways")
            out[key].append(g)
        for n, pd in payload.get("profiles", {}).items():
            key = ("new_sites" if n not in self.store.profiles() else
                   "same_sites" if self._same_profile(n, pd, sec) else "conflict_sites")
            out[key].append(pd.get("label") or n)
        return out

    def import_bundle(self, text: str, passphrase: str | None, overwrite: bool = False,
                      install: bool = True, on_conflict: str = "keep") -> list[str]:
        """Merge an export into this computer.

        New gateways and sites are added; identical ones are left alone. For ones
        with the same name but different settings, ``on_conflict`` decides:
        "keep" (keep this computer's), "replace" (use the imported one) or "both"
        (add the imported one under a new name). ``overwrite`` means "replace".
        """
        if overwrite:
            on_conflict = "replace"
        if on_conflict not in ("keep", "replace", "both"):
            raise CatflapError("on_conflict must be keep, replace or both")
        payload = bundle.open_bundle(text, passphrase)
        sec = payload.get("secrets", {})
        msgs: list[str] = []
        gw_renamed: dict[str, str] = {}

        for g, cfg in payload.get("gateways", {}).items():
            target = g
            if g in self.store.gateways():
                if self._same_gateway(g, cfg, sec):
                    msgs.append(f"Gateway '{g}' is already here.")
                    continue
                if on_conflict == "keep":
                    msgs.append(f"Kept this computer's gateway '{g}'.")
                    continue
                if on_conflict == "both":
                    target = self.unique_gateway_name(f"{g}-imported")
                    gw_renamed[g] = target
            self.store.put_gateway(target, cfg)
            for k in GATEWAY_SECRETS:
                v = sec.get(gateway_key(g, k))
                if v:
                    self.secrets.set(gateway_key(target, k), v)
            msgs.append(f"Imported gateway '{target}'." if target == g else
                        f"Imported gateway '{g}' as '{target}'.")

        for n, pd in payload.get("profiles", {}).items():
            p = Profile.from_dict(pd)
            target = n
            if n in self.store.profiles():
                if self._same_profile(n, pd, sec):
                    msgs.append(f"Site '{p.display}' is already here.")
                    continue
                if on_conflict == "keep":
                    msgs.append(f"Kept this computer's site '{p.display}'.")
                    continue
                if on_conflict == "both":
                    p.label = f"{p.display} (imported)"
                    target = self.unique_profile_name(p.label)
                elif on_conflict == "replace":
                    self._teardown(self.store.profile(n), revoke=False)
            p.name = target
            p.gateway = gw_renamed.get(p.gateway, p.gateway) if p.gateway else p.gateway
            p.backend_id = None          # belongs to the other machine
            p.installed_routes = []
            if p.ifname in self._taken_ifnames(target):
                p.ifname = netutil.unique_ifname(target, self._taken_ifnames(target))
            for k in PROFILE_SECRETS:
                v = sec.get(profile_key(n, k))
                if v:
                    self.secrets.set(profile_key(target, k), v)
            self.store.put_profile(p)
            self.store.save()
            if install:
                try:
                    self.apply(p)
                except Exception as e:
                    msgs.append(f"Site '{p.display}' imported, but its connection couldn't be "
                                f"created: {e}")
                    continue
            msgs.append(f"Imported site '{p.display}'.")
        if payload.get("profiles"):
            msgs.append("Note: imported sites use the other computer's keys. Don't connect the same "
                        "site on both computers at once; for a second computer, export only the "
                        "gateways and add the sites again there (one client per device).")
        self.store.settings().update({k: v for k, v in payload.get("settings", {}).items()
                                      if k not in self.store.settings()})
        self.store.save()
        return msgs
