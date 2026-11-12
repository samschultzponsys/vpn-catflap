"""Pre-flight checks (before connecting) and health checks (after connecting).

Every finding carries the number of the problem it guards against (see
docs/TEST-CHECKLIST.md) and a message a human can act on.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

from . import netutil
from .model import Profile
from .platform.base import Backend, LocalNetwork

ERROR, WARN, INFO, OK = "error", "warning", "info", "ok"


@dataclass
class Finding:
    level: str
    code: str
    message: str
    problem: int | None = None
    other: str | None = None         # the other site involved, for tunnel clashes

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message,
                "problem": self.problem, "other": self.other}


Resolver = Callable[[str], list[str]]


def resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    return sorted({i[4][0] for i in infos})


def public_ip(url: str = "https://api.ipify.org", timeout: float = 5) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (fixed https URL)
            ip = r.read().decode().strip()
        ipaddress.ip_address(ip)
        return ip
    except Exception:
        return None


def conflict_safe_routes(routes: list[str], local: LocalNetwork,
                         ifname: str) -> tuple[list[str], list[str]]:
    """Split site routes around the addresses that must stay local.

    When the network you're on uses the same addresses as the site (both
    10.0.30.0/24, say), routing the whole site range into the tunnel would cut
    you off from your own router, and with it the internet. Instead, route the
    site range minus this computer's address(es) and the local router(s) as
    more-specific routes: everything at the site stays reachable except those
    few addresses. Returns (routes, excluded addresses).
    """
    avoid: set[ipaddress.IPv4Address] = set()
    for a in local.addresses:
        if a.dev != ifname:
            try:
                avoid.add(ipaddress.ip_interface(a.cidr).ip)
            except ValueError:
                pass
    for r in local.routes:
        if r.dev != ifname and r.gateway:
            try:
                avoid.add(ipaddress.ip_address(r.gateway))
            except ValueError:
                pass
    out: list[str] = []
    excluded: set[str] = set()
    for route in routes:
        n = netutil.net(route)
        hits = sorted(ip for ip in avoid if ip.version == n.version and ip in n)
        if not hits:
            out.append(str(n))
            continue
        pieces = [n]
        for ip in hits:
            excluded.add(str(ip))
            nxt = []
            host = ipaddress.ip_network(f"{ip}/{n.max_prefixlen}")
            for piece in pieces:
                nxt.extend(piece.address_exclude(host) if ip in piece else [piece])
            pieces = nxt
        out.extend(str(x) for x in sorted(pieces))
    return out, sorted(excluded, key=ipaddress.ip_address)


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == ERROR for f in findings)


def preflight(profile: Profile, local: LocalNetwork, others: dict[str, tuple[Profile, bool]],
              resolver: Resolver = resolve, my_public_ip: Callable[[], str | None] | None = None,
              reachable: Callable[[str], bool] | None = None) -> list[Finding]:
    """``others`` maps profile name -> (profile, is_active)."""
    out: list[Finding] = []

    # -- endpoint (#9) ---------------------------------------------------------
    endpoint_ips: list[str] = []
    if netutil.is_ip(profile.endpoint_host):
        endpoint_ips = [profile.endpoint_host]
        out.append(Finding(WARN, "endpoint-raw-ip",
                           f"Endpoint is a raw IP ({profile.endpoint_host}). If the site's WAN IP "
                           "is dynamic, set a DDNS hostname: 'catflap set "
                           f"{profile.name} --endpoint <host>'.", 9))
    else:
        try:
            endpoint_ips = resolver(profile.endpoint_host)
        except OSError as e:
            out.append(Finding(ERROR, "endpoint-unresolvable",
                               f"Cannot resolve endpoint {profile.endpoint_host}: {e}. Check DDNS "
                               "and your internet connection.", 9))

    # -- CGNAT (#10) -----------------------------------------------------------
    if profile.wan_ip:
        if netutil.is_cgnat(profile.wan_ip):
            out.append(Finding(ERROR, "cgnat",
                               f"The gateway's WAN IP {profile.wan_ip} is in 100.64.0.0/10: the site "
                               "is behind carrier-grade NAT. Plain WireGuard cannot reach it; see "
                               "README 'CGNAT sites'.", 10))
        elif netutil.is_private(profile.wan_ip):
            out.append(Finding(WARN, "double-nat",
                               f"The gateway's WAN IP {profile.wan_ip} is private: there is another "
                               f"router in front. UDP {profile.endpoint_port} must be forwarded to "
                               "the gateway on that router.", 10))
        elif endpoint_ips and profile.wan_ip not in endpoint_ips:
            out.append(Finding(WARN, "endpoint-mismatch",
                               f"Endpoint resolves to {', '.join(endpoint_ips)} but the gateway "
                               f"reported WAN IP {profile.wan_ip}: DDNS may be stale, or the "
                               "WAN IP changed.", 9))

    # -- overlaps with the local LAN / on-site detection (#8, #12) -------------
    mine = profile.routes
    local_conflicts = []
    for r in local.routes:
        if r.dev == profile.ifname or netutil.is_default_route(r.dest):
            continue
        for m in mine:
            if netutil.overlaps(m, r.dest):
                local_conflicts.append((m, r))
    on_lan = [a for a in local.addresses if a.dev != profile.ifname
              and any(netutil.overlaps(a.cidr.split("/")[0] + "/32", m) for m in mine)]

    if local_conflicts:
        site = profile.display
        on_site = False
        if on_lan and my_public_ip:
            pub = my_public_ip()
            if pub is not None:
                on_site = pub in endpoint_ips or pub == profile.wan_ip
            else:
                # Can't check the public IP: fall back to "your router is the site's router".
                site_gws = {n.get("gateway_ip") for n in profile.networks if n.get("gateway_ip")}
                local_gws = {r.gateway for r in local.routes
                             if netutil.is_default_route(r.dest) and r.gateway}
                on_site = bool(site_gws & local_gws)
        if on_site:
            out.append(Finding(ERROR, "on-site",
                               f"You're at {site} right now (you're on its network), so you don't "
                               "need the VPN: everything there is already reachable.", 12))
        else:
            safe, excluded = conflict_safe_routes(mine, local, profile.ifname)
            nets = ", ".join(sorted({r.dest for _, r in local_conflicts}))
            devs = ", ".join(sorted({r.dev for _, r in local_conflicts}))
            keep = ", ".join(excluded) if excluded else "nothing"
            out.append(Finding(WARN, "local-overlap",
                               f"The network you're on now ({nets} on {devs}) uses the same "
                               f"addresses as {site}. catflap works around it: everything at "
                               f"{site} goes through the VPN except {keep} (this computer and your "
                               "local router), so your internet keeps working. Devices on the "
                               "network you're on now can't be reached while connected.", 8))

    # -- overlaps with other catflap tunnels (#8) -------------------------------
    for name, (other, active) in others.items():
        if name == profile.name:
            continue
        other_routes = other.installed_routes or other.routes
        clashes = netutil.find_overlaps(mine, other_routes)
        # Both sides routing the same single address (typically the VPN server's own
        # address, e.g. UniFi's default 192.168.3.1 at two sites) isn't a real clash:
        # catflap leaves it to the tunnel that's already up.
        hosts = [(m, o) for m, o in clashes if netutil.net(m).prefixlen == netutil.net(m).max_prefixlen]
        real = [c for c in clashes if c not in hosts]
        if hosts and active:
            out.append(Finding(INFO, "shared-host-route",
                               f"{', '.join(sorted({m for m, _ in hosts}))} is also used by "
                               f"'{other.display}'; catflap leaves it to that tunnel.", 8, other=name))
        if real:
            nets = ", ".join(sorted({m for m, _ in real}))
            if active:
                out.append(Finding(ERROR, "tunnel-overlap",
                                   f"{profile.display} and {other.display} use the same addresses "
                                   f"({nets}), so only one of them can be connected at a time.",
                                   8, other=name))
            else:
                out.append(Finding(WARN, "tunnel-overlap",
                                   f"{profile.display} and {other.display} use the same addresses "
                                   f"({nets}); they can't be connected at the same time.",
                                   8, other=name))
        if profile.vpn_subnet and other.vpn_subnet and netutil.overlaps(profile.vpn_subnet, other.vpn_subnet):
            out.append(Finding(WARN, "vpn-subnet-collision",
                               f"VPN subnet {profile.vpn_subnet} collides with '{name}' "
                               f"({other.vpn_subnet}). Use e.g. 10.250.<site>.0/28 per site.", 8))

    # -- already reachable (linked site / on-site) (#12) -----------------------
    if not local_conflicts and reachable:
        # Reachable through another connected catflap tunnel doesn't mean on-site.
        via_tunnels = [r for n2, (o, act) in others.items() if act and n2 != profile.name
                       for r in (o.installed_routes or o.routes)]
        targets = [n.get("gateway_ip") for n in profile.networks
                   if n.get("gateway_ip") and n.get("cidr") in mine
                   and not any(netutil.overlaps(n["gateway_ip"] + "/32", r) for r in via_tunnels)]
        for t in targets[:1]:
            if reachable(t):
                out.append(Finding(ERROR, "already-reachable",
                                   f"{t} ({profile.name}'s gateway) is reachable without the "
                                   "tunnel: you are on-site or on a site linked by site-to-site "
                                   "VPN (e.g. Site Magic). Not connecting avoids duplicate "
                                   "routes; use --force to override.", 12))

    if not any(f.level == ERROR for f in out):
        out.append(Finding(OK, "preflight-ok", "Pre-flight checks passed."))
    return out


def health(profile: Profile, backend: Backend, wait: float = 8,
           sleep: Callable[[float], None] = time.sleep) -> list[Finding]:
    """Verify the tunnel actually carries traffic (#1, #9, #10, #11)."""
    st = backend.status(profile)
    if not st.active:
        return [Finding(ERROR, "not-active", "The connection did not activate.")]
    target = profile.ping_target
    pinged = False
    if target:
        deadline = time.monotonic() + wait
        while True:
            pinged = backend.ping(target, count=1, timeout_s=2)
            if pinged or time.monotonic() >= deadline:
                break
            sleep(1)
    st = backend.status(profile)
    rx = st.rx_bytes or 0
    handshake = st.last_handshake is not None or rx > 0

    if not handshake:
        return [Finding(ERROR, "no-handshake",
                        f"No WireGuard handshake ({st.tx_bytes or 0} bytes sent, 0 received). "
                        "Usual causes: (1) the VPN server was never created/applied on the "
                        "gateway; (10) the site is behind CGNAT or UDP "
                        f"{profile.endpoint_port} isn't forwarded; (9) the endpoint "
                        f"{profile.endpoint_host} points at an old IP; (11) this client was "
                        "revoked or its key is used by another device. The tunnel was "
                        "disconnected; your normal internet is unaffected (split tunnel).", 1)]
    out = [Finding(OK, "handshake", f"Handshake OK ({rx} bytes received).")]
    if target and not pinged:
        out.append(Finding(WARN, "ping-failed",
                           f"Handshake OK but {target} does not answer ping. The gateway may "
                           "block ICMP, or its firewall doesn't allow the VPN subnet to that "
                           "network.", 12))
    elif target:
        out.append(Finding(OK, "ping", f"{target} answers through the tunnel."))
    return out
