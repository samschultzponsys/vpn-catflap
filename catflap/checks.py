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

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message,
                "problem": self.problem}


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
        same_wan = False
        if on_lan and my_public_ip and endpoint_ips:
            pub = my_public_ip()
            same_wan = pub is not None and pub in endpoint_ips
        if same_wan:
            out.append(Finding(ERROR, "on-site",
                               f"You are on {profile.name}'s LAN already (your public IP is the "
                               "site's endpoint). No tunnel needed; connecting would duplicate "
                               "routes.", 12))
        else:
            for m, r in local_conflicts:
                out.append(Finding(ERROR, "local-overlap",
                                   f"Site network {m} overlaps local network {r.dest} on "
                                   f"{r.dev}. Traffic for it would go to the wrong place (common "
                                   "with 192.168.0.0/24 and 192.168.1.0/24 at hotels/clients). "
                                   "Remove that route from the profile or renumber the site.", 8))

    # -- overlaps with other catflap tunnels (#8) -------------------------------
    for name, (other, active) in others.items():
        if name == profile.name:
            continue
        for m, o in netutil.find_overlaps(mine, other.routes):
            out.append(Finding(ERROR if active else WARN, "tunnel-overlap",
                               f"Network {m} overlaps {o} of {'active ' if active else ''}tunnel "
                               f"'{name}'." + (" Disconnect it first." if active else ""), 8))
        if profile.vpn_subnet and other.vpn_subnet and netutil.overlaps(profile.vpn_subnet, other.vpn_subnet):
            out.append(Finding(WARN, "vpn-subnet-collision",
                               f"VPN subnet {profile.vpn_subnet} collides with '{name}' "
                               f"({other.vpn_subnet}). Use e.g. 10.250.<site>.0/28 per site.", 8))

    # -- already reachable (linked site / on-site) (#12) -----------------------
    if not local_conflicts and reachable:
        targets = [n.get("gateway_ip") for n in profile.networks
                   if n.get("gateway_ip") and n.get("cidr") in mine]
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
