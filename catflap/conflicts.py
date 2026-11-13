"""Other VPN software that can clash with CatFlap, and how to spot it.

Windows: programs in the Apps list (registry). Linux: active network interfaces
that belong to other VPNs (works inside the Flatpak too, via NetworkManager).
The tray warns once at start with what each one can do and how to live with it;
WiFiman Desktop and the WireGuard app get their own uninstall offers.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from .netutil import IFNAME_PREFIX

FULL_TUNNEL = ("While it's connected it can send all traffic and DNS through itself, and a "
               "kill switch or \"block LAN\" setting can block CatFlap's tunnels. Disconnect it, or "
               "turn on its split tunneling / allow LAN, while you use CatFlap.")
CORPORATE = ("Work VPNs often enforce their own routes and DNS and can block other VPNs while "
             "connected. Use CatFlap while it's disconnected, or ask IT about split tunneling.")
MESH = ("It can route whole subnets (subnet routes / exit nodes / managed routes). If it routes "
        "the same networks as a CatFlap site, only one of them wins. CatFlap warns about overlaps "
        "when you connect; turn those routes off while you use CatFlap if they clash.")


@dataclass(frozen=True)
class Conflict:
    key: str                      # stable id for "don't warn again"
    name: str                     # shown to the user
    why: str
    programs: tuple[str, ...] = ()     # Windows Apps-list name prefixes (lowercase)
    interfaces: tuple[str, ...] = ()   # Linux interface name patterns


KNOWN: tuple[Conflict, ...] = (
    Conflict("tailscale", "Tailscale", MESH, ("tailscale",), ("tailscale*",)),
    Conflict("zerotier", "ZeroTier", MESH, ("zerotier",), ("zt*",)),
    Conflict("netbird", "NetBird", MESH, ("netbird",), ("wt0", "netbird*")),
    Conflict("nordvpn", "NordVPN", FULL_TUNNEL, ("nordvpn",), ("nordlynx", "nordtun")),
    Conflict("expressvpn", "ExpressVPN", FULL_TUNNEL, ("expressvpn",), ("expressvpn*",)),
    Conflict("surfshark", "Surfshark", FULL_TUNNEL, ("surfshark",), ("surfshark*",)),
    Conflict("protonvpn", "Proton VPN", FULL_TUNNEL, ("proton vpn", "protonvpn"), ("proton*", "pvpn*")),
    Conflict("mullvad", "Mullvad VPN", FULL_TUNNEL, ("mullvad",), ("wg0-mullvad", "wg-mullvad*")),
    Conflict("pia", "Private Internet Access", FULL_TUNNEL, ("private internet access",), ("wgpia*",)),
    Conflict("windscribe", "Windscribe", FULL_TUNNEL, ("windscribe",), ("windscribe*", "utun420")),
    Conflict("cyberghost", "CyberGhost", FULL_TUNNEL, ("cyberghost",), ("cyberghost*",)),
    Conflict("ipvanish", "IPVanish", FULL_TUNNEL, ("ipvanish",), ()),
    Conflict("warp", "Cloudflare WARP", FULL_TUNNEL, ("cloudflare warp",), ("cloudflarewarp", "CloudflareWARP")),
    Conflict("cisco", "Cisco Secure Client / AnyConnect", CORPORATE,
             ("cisco secure client", "cisco anyconnect"), ("cscotun*",)),
    Conflict("globalprotect", "GlobalProtect", CORPORATE, ("globalprotect",), ("gpd*",)),
    Conflict("forticlient", "FortiClient", CORPORATE, ("forticlient",), ("fctvpn*",)),
    Conflict("zscaler", "Zscaler", CORPORATE, ("zscaler",), ("zcctun*",)),
    Conflict("ivanti", "Ivanti / Pulse Secure", CORPORATE, ("ivanti secure access", "pulse secure"), ()),
    Conflict("checkpoint", "Check Point VPN", CORPORATE, ("check point endpoint",), ()),
    Conflict("openvpn", "OpenVPN", "If one of its profiles routes the same networks as a CatFlap "
             "site (or everything), only one of them wins. CatFlap warns about overlaps when you "
             "connect.", ("openvpn",), ()),
)


def match_programs(programs: list[dict]) -> list[tuple[Conflict, dict]]:
    """Known conflicts among Windows' installed programs (one entry per conflict)."""
    out = []
    for c in KNOWN:
        for info in programs:
            if any(info["name"].lower().startswith(p) for p in c.programs):
                out.append((c, info))
                break
    return out


def match_interfaces(devs: list[str]) -> list[tuple[Conflict, str]]:
    """Known conflicts among active network interfaces (Linux)."""
    out = []
    for c in KNOWN:
        for d in devs:
            if not d.startswith(IFNAME_PREFIX) and any(fnmatch.fnmatchcase(d, pat) for pat in c.interfaces):
                out.append((c, d))
                break
    return out


def detect(backend, platform: str, programs=None) -> list[tuple[Conflict, str]]:
    """(conflict, detail) pairs for this computer; detail is a version or interface."""
    if platform == "win32":
        if programs is None:
            from .platform.windows import installed_programs
            programs = installed_programs()
        return [(c, f"{info['name']} {info.get('version') or ''}".strip())
                for c, info in match_programs(programs)]
    try:
        devs = sorted({a.dev for a in backend.local_network().addresses if a.dev})
    except Exception:  # noqa: BLE001 - best effort
        return []
    return [(c, f"interface {d} is up") for c, d in match_interfaces(devs)]
