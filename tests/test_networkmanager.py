import os
import json

import pytest

from catflap import wgconf
from catflap.model import Profile
from catflap.platform.base import BackendError, CmdResult
from catflap.platform.networkmanager import NetworkManagerBackend


class FakeRunner:
    def __init__(self, settings=None):
        self.cmds = []
        self.files = {}
        self.settings = settings or {
            "connection.autoconnect": "no", "ipv4.never-default": "yes", "ipv4.dns": "",
            "ipv4.dns-search": "", "ipv6.method": "disabled", "ipv6.dns-search": "",
            "connection.interface-name": "cf-home"}

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        if cmd[:4] == ["nmcli", "connection", "import", "type"]:
            path = cmd[-1]
            self.files[path] = open(path).read()
            return CmdResult(0, "Connection 'cf-home' (0f6b0a8e-1111-2222-3333-444455556666) successfully added.\n", "")
        if "-f" in cmd and "show" in cmd and "ipv6.method" in cmd[cmd.index("-f") + 1]:
            return CmdResult(0, "\n".join(f"{k}:{v}" for k, v in self.settings.items()), "")
        if cmd[:2] == ["ip", "-j"]:
            if "route" in cmd:
                return CmdResult(0, json.dumps([{"dst": "default", "gateway": "192.168.1.1", "dev": "wlp0"},
                                                {"dst": "192.168.1.0/24", "dev": "wlp0"}]), "")
            return CmdResult(0, json.dumps([{"ifname": "wlp0", "addr_info": [
                {"family": "inet", "local": "192.168.1.50", "prefixlen": 24}]}]), "")
        return CmdResult(0, "", "")


def profile():
    return Profile(name="home", ifname="cf-home", vendor="unifi", addresses=["10.250.1.2/32"],
                   peer_public_key="P", endpoint_host="h", endpoint_port=51820,
                   routes=["192.168.1.0/24"])


def cfg():
    return wgconf.parse_text("[Interface]\nPrivateKey = K\nAddress = 10.250.1.2/32\n[Peer]\n"
                             "PublicKey = P\nAllowedIPs = 192.168.1.0/24\nEndpoint = h:51820\n"
                             "PersistentKeepalive = 25\n")


def test_install_imports_with_sanitized_filename_and_safe_settings():
    r = FakeRunner()
    uuid = NetworkManagerBackend(r).install(profile(), cfg())
    assert uuid == "0f6b0a8e-1111-2222-3333-444455556666"
    (path, text), = r.files.items()
    assert os.path.basename(path) == "cf-home.conf"   # problem 2
    assert "DNS" not in text and "0.0.0.0/0" not in text
    modify = next(c for c in r.cmds if c[1:3] == ["connection", "modify"])
    pairs = dict(zip(modify[5::2], modify[6::2]))
    assert pairs["connection.autoconnect"] == "no"    # 7
    assert pairs["ipv4.never-default"] == "yes"       # 3
    assert pairs["ipv6.method"] == "disabled"         # 5
    assert pairs["ipv4.dns-search"] == ""             # 4


def test_split_dns_uses_routing_domain_only():
    p = profile()
    p.dns_domain, p.dns_servers = "office.lan", ["192.168.1.1"]
    s = dict(NetworkManagerBackend(FakeRunner()).settings_for(p))
    assert s["ipv4.dns-search"] == "~office.lan"
    assert s["ipv4.dns"] == "192.168.1.1"


def test_verify_catches_ipv6_tilde_and_rolls_back():  # problem 4/5: "IPv6 kept coming back"
    r = FakeRunner()
    r.settings.update({"ipv6.method": "auto", "ipv6.dns-search": "~"})
    with pytest.raises(BackendError, match="IPv6"):
        NetworkManagerBackend(r).install(profile(), cfg())
    assert any(c[1:3] == ["connection", "delete"] for c in r.cmds)


def test_local_network_parsing():
    ln = NetworkManagerBackend(FakeRunner()).local_network()
    assert [x.dest for x in ln.routes] == ["0.0.0.0/0", "192.168.1.0/24"]
    assert ln.addresses[0].cidr == "192.168.1.50/24"


def test_polkit_hint():
    def runner(cmd):
        return CmdResult(1, "", "Error: Not authorized to control networking.")
    with pytest.raises(BackendError, match="sudo"):
        NetworkManagerBackend(runner).up(Profile(**{**profile().__dict__, "backend_id": "u"}))
