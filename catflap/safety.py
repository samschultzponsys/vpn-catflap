"""Safe-defaults policy. Every tunnel goes through here before it reaches a backend.

Enforced (each maps to a numbered problem in docs/TEST-CHECKLIST.md):
  #3  split tunnel: 0.0.0.0/0 and ::/0 are never routed
  #4  no DNS from the .conf and never a "~" catch-all search domain
  #5  IPv6 dropped (addresses and routes)
  #6  PersistentKeepalive = 25
  #2  interface name sanitized
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from . import netutil
from .model import PeerConfig

KEEPALIVE = 25
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


class SafetyError(ValueError):
    pass


@dataclass
class SafeResult:
    config: PeerConfig
    warnings: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def validate_dns_domain(domain: str) -> str:
    d = domain.strip().lower().lstrip("~").rstrip(".")
    if not d or not _DOMAIN_RE.match(d):
        raise SafetyError(
            f"invalid DNS domain {domain!r}: give a specific internal domain such as "
            "'office.lan' (a catch-all '~' is never allowed)"
        )
    return d


def make_safe(cfg: PeerConfig, routes: list[str] | None = None) -> SafeResult:
    """Return a copy of ``cfg`` with catflap's safe defaults applied.

    ``routes`` are the networks the user chose to send through the tunnel. If
    omitted, the .conf's own AllowedIPs are used minus default routes and IPv6;
    if that leaves nothing, the tunnel's own subnet is routed so the gateway is
    at least reachable.
    """
    warnings: list[str] = []
    removed: list[str] = []

    addresses_v4 = [a for a in cfg.addresses if netutil.net(a).version == 4]
    dropped_v6 = [a for a in cfg.addresses if netutil.net(a).version == 6]
    if dropped_v6:
        removed.append(f"IPv6 tunnel address(es) {', '.join(dropped_v6)}")
    if not addresses_v4:
        raise SafetyError("the config has no IPv4 tunnel address")

    candidate = routes if routes is not None else cfg.allowed_ips
    safe_routes: list[str] = []
    for r in candidate:
        try:
            n = netutil.net(r)
        except ValueError:
            raise SafetyError(f"invalid route {r!r}") from None
        if netutil.is_default_route(r):
            removed.append(f"full-tunnel route {n}")
            continue
        if n.version == 6:
            removed.append(f"IPv6 route {n}")
            continue
        if n.prefixlen < 8:
            raise SafetyError(f"route {n} is too broad for a split tunnel")
        if str(n) not in safe_routes:
            safe_routes.append(str(n))

    if not safe_routes:
        # Route the VPN subnet itself so the gateway can be reached/pinged.
        own = str(netutil.net(addresses_v4[0]).supernet(new_prefix=24)) \
            if netutil.net(addresses_v4[0]).prefixlen == 32 else str(netutil.net(addresses_v4[0]))
        safe_routes = [own]
        warnings.append(
            "No site networks were selected and the config only had full-tunnel routes; "
            f"routing only {own}. Re-run with --route to add site networks."
        )

    if cfg.dns:
        removed.append(f"DNS servers {', '.join(cfg.dns)} (would hijack all DNS)")
    if cfg.keepalive != KEEPALIVE:
        removed.append(f"keepalive {cfg.keepalive or 'off'} -> {KEEPALIVE}")

    safe = replace(
        cfg,
        addresses=addresses_v4,
        allowed_ips=safe_routes,
        dns=[],
        keepalive=KEEPALIVE,
    )
    return SafeResult(config=safe, warnings=warnings, removed=removed)


def check_vpn_subnet_collisions(name: str, vpn_subnet: str | None,
                                others: dict[str, str | None]) -> list[str]:
    """Warn when two sites use the same/overlapping VPN client subnet (problem #8)."""
    if not vpn_subnet:
        return []
    out = []
    for other, sub in others.items():
        if other != name and sub and netutil.overlaps(vpn_subnet, sub):
            out.append(
                f"VPN subnet {vpn_subnet} of '{name}' overlaps {sub} of '{other}'. Give each "
                "site its own VPN subnet, e.g. 10.250.<site-number>.0/28."
            )
    return out
