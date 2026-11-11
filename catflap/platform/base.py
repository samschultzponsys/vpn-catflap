"""Platform backend interface: how a normalized, already-safe tunnel becomes a
real OS connection, and how the local network is inspected.

Linux: NetworkManager (nmcli). Windows: the official WireGuard for Windows
tunnel service. No custom VPN daemon, no wg-quick.
"""

from __future__ import annotations

import abc
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..model import PeerConfig, Profile


class BackendError(RuntimeError):
    pass


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], CmdResult]


def run(cmd: Sequence[str], input_text: str | None = None, timeout: float = 30) -> CmdResult:
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, input=input_text,
                           timeout=timeout)
    except FileNotFoundError:
        return CmdResult(127, "", f"{cmd[0]}: command not found")
    except subprocess.TimeoutExpired:
        return CmdResult(124, "", f"{cmd[0]}: timed out")
    return CmdResult(p.returncode, p.stdout, p.stderr)


@dataclass
class LocalRoute:
    dest: str           # CIDR
    dev: str
    gateway: str | None = None


@dataclass
class LocalAddress:
    cidr: str           # interface address with prefix, e.g. "192.168.1.23/24"
    dev: str


@dataclass
class TunnelStatus:
    active: bool
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    last_handshake: int | None = None   # unix time, None if unknown (no privilege)
    detail: str = ""


@dataclass
class LocalNetwork:
    routes: list[LocalRoute] = field(default_factory=list)
    addresses: list[LocalAddress] = field(default_factory=list)


class Backend(abc.ABC):
    name: str = "base"

    def __init__(self, runner: Runner = run):
        self.run = runner

    @abc.abstractmethod
    def install(self, profile: Profile, cfg: PeerConfig) -> str:
        """Create or replace the OS connection. Returns its backend id."""

    @abc.abstractmethod
    def remove(self, profile: Profile) -> None: ...

    @abc.abstractmethod
    def up(self, profile: Profile) -> None: ...

    @abc.abstractmethod
    def down(self, profile: Profile) -> None: ...

    @abc.abstractmethod
    def status(self, profile: Profile) -> TunnelStatus: ...

    @abc.abstractmethod
    def local_network(self) -> LocalNetwork: ...

    def wait_active(self, profile: Profile, timeout: float = 10) -> bool:
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.status(profile).active:
                return True
            time.sleep(0.5)
        return False

    def ping(self, host: str, count: int = 1, timeout_s: int = 2) -> bool:
        import shutil
        import sys
        if not shutil.which("ping"):   # e.g. inside the Flatpak sandbox
            return any(icmp_ping(host, timeout_s) for _ in range(count))
        if sys.platform == "win32":
            cmd = ["ping", "-n", str(count), "-w", str(timeout_s * 1000), host]
        else:
            cmd = ["ping", "-c", str(count), "-W", str(timeout_s), host]
        return self.run(cmd).returncode == 0


def icmp_ping(host: str, timeout_s: float = 2) -> bool:
    """Unprivileged ICMP echo (Linux SOCK_DGRAM ICMP; needs net.ipv4.ping_group_range,
    which Fedora and Debian allow for all users)."""
    import os
    import select
    import socket
    import struct
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except OSError:
        return False
    with s:
        seq = os.getpid() & 0xFFFF
        try:
            s.sendto(struct.pack("!BBHHH", 8, 0, 0, 0, seq) + b"catflap", (host, 0))
            r, _, _ = select.select([s], [], [], timeout_s)
            if not r:
                return False
            data = s.recv(1024)
        except OSError:
            return False
        return len(data) >= 8 and data[0] == 0   # echo reply


def get_backend(runner: Runner = run) -> Backend:
    import sys
    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend(runner)
    import os
    import shutil
    choice = os.environ.get("CATFLAP_BACKEND")
    if choice == "nmcli" or (choice is None and shutil.which("nmcli")):
        from .networkmanager import NetworkManagerBackend
        return NetworkManagerBackend(runner)
    # No nmcli (Flatpak sandbox, minimal installs): talk to NetworkManager over D-Bus.
    from .nm_dbus import NetworkManagerDBusBackend
    return NetworkManagerDBusBackend(runner)
