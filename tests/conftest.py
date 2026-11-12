from __future__ import annotations

from pathlib import Path

import pytest

from catflap.core import Manager
from catflap.drivers.base import Capabilities, Driver, GatewayConfig
from catflap.model import ClientRecord, Network, Site, WanInfo, WireGuardServer
from catflap.platform.base import Backend, LocalNetwork, TunnelStatus
from catflap.secrets import SecretStore
from catflap.store import Store

DATA = Path(__file__).parent / "data"
SERVER_PUB = "hSDwCYkwp1R0i33ctD73Wg2/Og0mOBr066SpjqqbTmo="


class FakeBackend(Backend):
    name = "fake"

    def __init__(self):
        super().__init__(runner=lambda cmd: None)
        self.installed: dict[str, object] = {}
        self.active: set[str] = set()
        self.local = LocalNetwork()
        self.rx = 1234
        self.ping_ok = True
        self.calls: list[tuple] = []

    def install(self, profile, cfg):
        self.calls.append(("install", profile.name))
        self.installed[profile.name] = cfg
        return f"uuid-{profile.name}"

    def remove(self, profile):
        self.calls.append(("remove", profile.name))
        self.installed.pop(profile.name, None)

    def up(self, profile):
        self.calls.append(("up", profile.name))
        self.active.add(profile.name)

    def down(self, profile):
        self.calls.append(("down", profile.name))
        self.active.discard(profile.name)

    def status(self, profile):
        a = profile.name in self.active
        return TunnelStatus(active=a, rx_bytes=self.rx if a else None, tx_bytes=500 if a else None)

    def local_network(self):
        return self.local

    def wait_active(self, profile, timeout=10):
        return profile.name in self.active

    def ping(self, host, count=1, timeout_s=2):
        # Site addresses only answer through an active tunnel that routes them.
        import ipaddress
        if not self.ping_ok:
            return False
        ip = ipaddress.ip_address(host)
        return any(ip in ipaddress.ip_network(r, strict=False)
                   for n in self.active for r in getattr(self.installed.get(n), "allowed_ips", []))


class FakeDriver(Driver):
    vendor = "unifi"
    display_name = "FakeUniFi"
    capabilities = Capabilities(list_sites=True, list_networks=True, read_server=True,
                                create_client=True, revoke_client=True, wan_info=True)
    state: dict = {}

    def __init__(self, gw, secrets):
        super().__init__(gw, secrets)
        st = FakeDriver.state
        st.setdefault("clients", [])
        st.setdefault("revoked", [])
        st.setdefault("next_id", 1)
        self.st = st

    def list_sites(self):
        return [Site(id="s1", name="Home", ref="default")]

    def list_networks(self, site):
        return [Network("LAN", "192.168.1.0/24", None, "192.168.1.1", "n1"),
                Network("IoT", "192.168.20.0/24", 20, "192.168.20.1", "n2")]

    def get_wireguard_server(self, site, name=None):
        return WireGuardServer(id="wg1", name="WG", enabled=True, port=51820,
                               subnet="10.250.1.0/28", server_address="10.250.1.1",
                               public_key=SERVER_PUB)

    def get_wan_info(self, site):
        return WanInfo(wan_ip="203.0.113.10", ddns_hostname="home.example.net")

    def list_clients(self, site, server):
        return list(self.st["clients"])

    def create_client(self, site, server, device_name, public_key, address):
        rec = ClientRecord(id=f"c{self.st['next_id']}", name=device_name,
                           address=address, public_key=public_key)
        self.st["next_id"] += 1
        self.st["clients"].append(rec)
        return rec

    def revoke_client(self, site, server, client_id):
        self.st["revoked"].append(client_id)
        self.st["clients"] = [c for c in self.st["clients"] if c.id != client_id]


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def manager(tmp_path, backend, monkeypatch):
    monkeypatch.setenv("CATFLAP_SECRETS", "file")
    FakeDriver.state = {}
    store = Store(tmp_path)
    secrets = SecretStore(backend="file", root=tmp_path)
    m = Manager(store, secrets, backend, driver_factory=lambda gw, sec: FakeDriver(gw, sec))
    m.resolver = lambda host: ["203.0.113.10"]
    store.settings().update({"public_ip_url": "", "health_wait": 0})
    m.add_gateway("home-gw", "unifi", "https://192.0.2.1", api_key="KEY")
    return m


@pytest.fixture
def unifi_conf():
    return DATA / "unifi-client.conf"
