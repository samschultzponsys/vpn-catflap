"""Other VPN software that can clash with CatFlap, and how to spot it.

Windows: programs in the Apps list (registry). Linux: active network interfaces
that belong to other VPNs (works inside the Flatpak too, via NetworkManager).
The tray scans on first start and after every CatFlap update, and from
Settings → Check for other VPN software… (which always lists everything). One
window lists what was found, with checkboxes to uninstall (Windows) or not be
warned again. "Don't warn again" remembers the version: a newer version of that
software is warned about again.
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
    exact: bool = False                # programs must match the whole name


@dataclass
class Found:
    conflict: Conflict
    name: str                     # as installed, e.g. "Private Internet Access"
    version: str = ""
    detail: str = ""              # e.g. "interface tailscale0 is up"
    info: dict | None = None      # Windows uninstall info (None: can't uninstall from here)

    @property
    def key(self) -> str:
        return self.conflict.key

    @property
    def can_uninstall(self) -> bool:
        return bool(self.info and (self.info.get("uninstall") or self.info.get("quiet_uninstall")))

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}".strip()


WIFIMAN = Conflict("wifiman", "WiFiman Desktop",
                   "Its Teleport VPN changes routes and DNS too, which can interfere with CatFlap's "
                   "connections. CatFlap replaces it. If you keep it, don't connect Teleport and "
                   "CatFlap at the same time.", ("wifiman",))
WIREGUARD_APP = Conflict("wireguard-app", "WireGuard (app)",
                         "CatFlap has WireGuard's engine built in, so the WireGuard app (its window "
                         "and tray icon) isn't needed. If you keep it, don't import CatFlap's sites "
                         "into it or run its tunnels for the same networks while CatFlap is "
                         "connected. \"Hide tray icon\" keeps the app but stops it starting with "
                         "Windows.", ("wireguard",), exact=True)

KNOWN: tuple[Conflict, ...] = (
    WIFIMAN,
    WIREGUARD_APP,
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


def match_programs(programs: list[dict], known=KNOWN) -> list[Found]:
    """Known conflicts among Windows' installed programs (one entry per conflict)."""
    out = []
    for c in known:
        for info in programs:
            n = info["name"].strip().lower()
            if any(n == pat if c.exact else n.startswith(pat) for pat in c.programs):
                out.append(Found(c, info["name"].strip(), str(info.get("version") or ""), info=info))
                break
    return out


def match_interfaces(devs: list[str], known=KNOWN) -> list[Found]:
    """Known conflicts among active network interfaces (Linux)."""
    out = []
    for c in known:
        for d in devs:
            if not d.startswith(IFNAME_PREFIX) and any(fnmatch.fnmatchcase(d, pat) for pat in c.interfaces):
                out.append(Found(c, c.name, detail=f"interface {d} is up"))
                break
    return out


def detect(backend, platform: str, programs=None) -> list[Found]:
    """Other VPN software on this computer."""
    if platform == "win32":
        known = KNOWN
        engine = getattr(backend, "engine", None)
        if not (engine is not None and (engine.exists() or backend.downloaded_msi())):
            # CatFlap uses the WireGuard app itself when its own engine isn't set up
            known = tuple(c for c in KNOWN if c is not WIREGUARD_APP)
        try:
            if programs is None:
                from .platform.windows import installed_programs
                programs = installed_programs()
        except Exception:  # noqa: BLE001 - best effort
            return []
        return match_programs(programs, known)
    try:
        devs = sorted({a.dev for a in backend.local_network().addresses if a.dev})
    except Exception:  # noqa: BLE001 - best effort
        return []
    return match_interfaces(devs)


# -- "don't warn again", remembered per software and version
def ignored(settings: dict) -> dict[str, str]:
    """{key: version} the user silenced. Migrates 1.5's settings (no versions): those
    are adopted at the next scan as whatever version is installed then."""
    raw = settings.get("conflicts_ignored")
    if isinstance(raw, list):
        raw = {k: "*" for k in raw}
    raw = dict(raw or {})
    for old, key in (("wifiman_prompt", "wifiman"), ("wireguard_app_prompt", "wireguard-app")):
        if settings.pop(old, None) == "never":
            raw.setdefault(key, "*")
    settings["conflicts_ignored"] = raw
    return raw


def to_warn(found: list[Found], settings: dict) -> list[Found]:
    """What to show after an automatic scan: everything except software the user
    silenced at this same version."""
    ign = ignored(settings)
    out = []
    for f in found:
        if f.key in ign and ign[f.key] == "*":
            ign[f.key] = f.version          # adopt the version silenced before versions were kept
        if f.key in ign and ign[f.key] == f.version:
            continue
        out.append(f)
    return out


def silence(settings: dict, found: list[Found]) -> None:
    ign = ignored(settings)
    for f in found:
        ign[f.key] = f.version


def unsilence(settings: dict, found: list[Found]) -> None:
    ign = ignored(settings)
    for f in found:
        ign.pop(f.key, None)
