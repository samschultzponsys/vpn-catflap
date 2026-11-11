"""Address, subnet and interface-name helpers (no I/O)."""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable

IFNAME_MAX = 15            # Linux IFNAMSIZ - 1
IFNAME_PREFIX = "cf-"

CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
RFC1918 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
DEFAULT_ROUTES = {ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0")}

IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network


def sanitize_ifname(name: str, prefix: str = IFNAME_PREFIX) -> str:
    """Make a NetworkManager/kernel-safe interface name (problem #2).

    NetworkManager derives the interface name from the .conf filename; it must
    be <= 15 chars of ``[a-zA-Z0-9_-]`` or the import fails (silently in the
    KDE GUI). We lowercase, replace everything else with ``-``, collapse runs,
    add a recognizable prefix and truncate.
    """
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug) or "site"
    out = (prefix + slug)[:IFNAME_MAX].rstrip("-_")
    return out


def slug(text: str, max_len: int = 40) -> str:
    """CLI-safe name: lowercase letters, digits, '-' and '_' ("Mum's House" -> "mum-s-house")."""
    out = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-_")
    return re.sub(r"-{2,}", "-", out)[:max_len].strip("-_")


def unique_ifname(name: str, taken: Iterable[str]) -> str:
    taken = set(taken)
    base = sanitize_ifname(name)
    if base not in taken:
        return base
    for i in range(2, 100):
        suffix = f"-{i}"
        cand = base[: IFNAME_MAX - len(suffix)].rstrip("-_") + suffix
        if cand not in taken:
            return cand
    raise ValueError(f"cannot find a free interface name for {name!r}")


def net(cidr: str) -> IPNet:
    return ipaddress.ip_network(cidr, strict=False)


def normalize_cidr(cidr: str) -> str:
    return str(net(cidr))


def is_default_route(cidr: str) -> bool:
    return net(cidr) in DEFAULT_ROUTES


def overlaps(a: str, b: str) -> bool:
    na, nb = net(a), net(b)
    return na.version == nb.version and na.overlaps(nb)


def find_overlaps(mine: Iterable[str], others: Iterable[str]) -> list[tuple[str, str]]:
    others = list(others)
    return [(m, o) for m in mine for o in others if overlaps(m, o)]


def first_host(cidr: str) -> str:
    n = net(cidr)
    if n.num_addresses == 1:
        return str(n.network_address)
    return str(next(n.hosts()))


def is_cgnat(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in CGNAT_NET
    except ValueError:
        return False


def is_private(ip: str) -> bool:
    """RFC 1918 only (Python's is_private also covers documentation ranges)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in RFC1918)


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def free_host(subnet: str, used: Iterable[str]) -> str:
    """Pick the first free host address in ``subnet``.

    The first host is skipped (the gateway normally uses it).
    """
    n = net(subnet)
    used_ips = {ipaddress.ip_interface(u).ip for u in used}
    hosts = list(n.hosts())
    for h in hosts[1:]:
        if h not in used_ips:
            return str(h)
    raise ValueError(f"no free addresses left in VPN subnet {subnet}")


def suggest_vpn_subnet(index: int) -> str:
    """The recommended per-site VPN subnet plan: 10.250.<index>.0/28 (problem #8)."""
    if not 0 <= index <= 255:
        raise ValueError("site index must be 0-255")
    return f"10.250.{index}.0/28"
