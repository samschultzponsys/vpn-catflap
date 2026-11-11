import pytest

from catflap import wgconf
from catflap.model import Profile
from catflap.platform.base import BackendError
from catflap.platform import nm_dbus
from catflap.platform.nm_dbus import NetworkManagerDBusBackend, build_settings


class FakeNM:
    """In-memory NetworkManager speaking the subset of the D-Bus API catflap uses."""

    def __init__(self, mangle=None):
        self.conns = {}      # path -> settings
        self.active = {}     # active path -> uuid
        self.mangle = mangle
        self.calls = []
        self.devices = {"/dev/1": ("wlp0", "/ip4/1")}
        self.ip4 = {"/ip4/1": {
            "AddressData": [{"address": ("s", "192.168.1.50"), "prefix": ("u", 24)}],
            "RouteData": [{"dest": ("s", "0.0.0.0"), "prefix": ("u", 0), "next-hop": ("s", "192.168.1.1")},
                          {"dest": ("s", "192.168.1.0"), "prefix": ("u", 24)},
                          {"dest": ("s", "10.9.9.0"), "prefix": ("u", 24), "table": ("u", 51820)}]}}

    def _uuid(self, s):
        return s["connection"]["uuid"][1]

    def call(self, path, interface, method, signature="", args=()):
        self.calls.append(method)
        if method == "AddConnection":
            s = args[0]
            if self.mangle:
                self.mangle(s)
            p = f"/conn/{len(self.conns) + 1}"
            self.conns[p] = s
            return (p,)
        if method == "GetConnectionByUuid":
            for p, s in self.conns.items():
                if self._uuid(s) == args[0]:
                    return (p,)
            raise BackendError("UnknownConnection")
        if method == "ListConnections":
            return (list(self.conns),)
        if method == "GetSettings":
            return (self.conns[path],)
        if method == "Delete":
            self.conns.pop(path)
            return ()
        if method == "ActivateConnection":
            ap = f"/active/{len(self.active) + 1}"
            self.active[ap] = self._uuid(self.conns[args[0]])
            return (ap,)
        if method == "DeactivateConnection":
            self.active.pop(args[0])
            return ()
        if method == "GetDevices":
            return (list(self.devices),)
        raise AssertionError(method)

    def get(self, path, interface, prop):
        if prop == "ActiveConnections":
            return list(self.active)
        if prop == "Uuid":
            return self.active[path]
        if prop == "State":
            return 2
        if prop == "Interface":
            return self.devices[path][0]
        if prop == "Ip4Config":
            return self.devices[path][1]
        return self.ip4[path][prop]


def profile(**kw):
    d = dict(name="home", ifname="cf-home", vendor="unifi", addresses=["10.250.1.2/32"],
             peer_public_key="P", endpoint_host="home.example.net", endpoint_port=51820,
             routes=["192.168.1.0/24"])
    d.update(kw)
    return Profile(**d)


def cfg():
    return wgconf.parse_text("[Interface]\nPrivateKey = K\nAddress = 10.250.1.2/32\n[Peer]\n"
                             "PublicKey = P\nAllowedIPs = 192.168.1.0/24\nEndpoint = home.example.net:51820\n"
                             "PersistentKeepalive = 25\n")


def test_settings_are_safe():
    s = nm_dbus._plain(build_settings(profile(), cfg(), "u-1"))
    assert s["connection"]["autoconnect"] is False                 # 7
    assert s["connection"]["interface-name"] == "cf-home"          # 2
    assert s["ipv4"]["never-default"] is True                      # 3
    assert s["wireguard"]["ip4-auto-default-route"] == 0           # 3
    assert s["ipv4"]["dns"] == [] and s["ipv4"]["dns-search"] == []  # 4
    assert s["ipv6"]["method"] == "disabled"                       # 5
    peer = {k: v[1] for k, v in s["wireguard"]["peers"][0].items()}
    assert peer["persistent-keepalive"] == 25                      # 6
    assert peer["allowed-ips"] == ["192.168.1.0/24"]
    assert peer["endpoint"] == "home.example.net:51820"


def test_settings_serialise_with_jeepney():
    jeepney = pytest.importorskip("jeepney")
    p = profile(dns_domain="office.lan", dns_servers=["192.168.1.1"])
    msg = jeepney.new_method_call(
        jeepney.DBusAddress(nm_dbus.SETTINGS_PATH, bus_name=nm_dbus.NM, interface=nm_dbus.IF_SETTINGS),
        "AddConnection", "a{sa{sv}}", (build_settings(p, cfg(), "u-1"),))
    assert len(msg.serialise(serial=1)) > 200


def test_split_dns():
    s = nm_dbus._plain(build_settings(profile(dns_domain="office.lan", dns_servers=["192.168.1.1"]), cfg(), "u"))
    assert s["ipv4"]["dns-search"] == ["~office.lan"]
    assert s["ipv4"]["dns"] == [nm_dbus._ip4_u32("192.168.1.1")]


def test_lifecycle():
    nm = FakeNM()
    b = NetworkManagerDBusBackend(bus=nm)
    p = profile()
    p.backend_id = b.install(p, cfg())
    assert len(nm.conns) == 1
    assert not b.status(p).active
    b.up(p)
    assert b.status(p).active
    b.down(p)
    assert not b.status(p).active
    first = p.backend_id
    p.backend_id = b.install(p, cfg())     # re-apply replaces, doesn't duplicate
    assert len(nm.conns) == 1 and p.backend_id != first
    b.remove(p)
    assert nm.conns == {}


def test_replaces_stale_connection_with_same_interface():
    nm = FakeNM()
    b = NetworkManagerDBusBackend(bus=nm)
    b.install(profile(), cfg())
    b.install(profile(), cfg())             # no backend_id: found by interface name
    assert len(nm.conns) == 1


def test_verify_rejects_ipv6_tilde():  # problems 4/5
    def mangle(s):
        s["ipv6"] = {"method": ("s", "auto"), "dns-search": ("as", ["~"])}
    nm = FakeNM(mangle=mangle)
    with pytest.raises(BackendError, match="IPv6"):
        NetworkManagerDBusBackend(bus=nm).install(profile(), cfg())
    assert nm.conns == {}


def test_local_network_main_table_only():
    ln = NetworkManagerDBusBackend(bus=FakeNM()).local_network()
    assert [r.dest for r in ln.routes] == ["0.0.0.0/0", "192.168.1.0/24"]
    assert ln.addresses[0].cidr == "192.168.1.50/24"


def test_backend_selection(monkeypatch):
    from catflap.platform import base
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("CATFLAP_BACKEND", "dbus")
    assert base.get_backend().name == "networkmanager-dbus"
    monkeypatch.setenv("CATFLAP_BACKEND", "nmcli")
    assert base.get_backend().name == "networkmanager"
