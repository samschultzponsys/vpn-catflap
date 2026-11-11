import pytest

from catflap import keys

from catflap.drivers.base import DriverError, GatewayConfig
from catflap.drivers.unifi import UniFiDriver
from catflap.http import HttpError
from catflap.model import Site

SERVER_PRIV = "YCs/Q2uUBdJ7SnVo7I3yH2lR0g9xA1ZJ/4l9mE0x7Xg="


class FakeClient:
    """Routes (method, path) to canned responses; shared across driver clients."""

    def __init__(self, routes, log, headers=None):
        self.routes, self.log, self.headers, self.cookies = routes, log, headers or {}, {}

    def request(self, method, path, body=None, params=None, extra_headers=None):
        self.log.append((method, path, body, dict(self.headers)))
        r = self.routes.get((method, path))
        if callable(r):
            r = r(self)
        if isinstance(r, HttpError):
            raise r
        if r is None:
            raise HttpError(404, "not found")
        return 200, {"x-csrf-token": "CSRF"}, r

    def get(self, path, **kw):
        return self.request("GET", path, **kw)[2]


def make(routes, username=None, password=None, api_key="KEY"):
    log = []
    gw = GatewayConfig(name="g", vendor="unifi", url="https://gw", username=username)
    d = UniFiDriver(gw, {"api_key": api_key, "password": password},
                    client_factory=lambda gw, h: FakeClient(routes, log, h))
    return d, log


INT = "/proxy/network/integration/v1/sites"
SITE = Site(id="uuid-1", name="Default", ref="default")
WG = {"_id": "wg1", "name": "Teleport-ish", "purpose": "remote-user-vpn",
      "vpn_type": "wireguard-server", "enabled": True, "local_port": 51820,
      "ip_subnet": "10.250.1.1/28", "x_wireguard_private_key": SERVER_PRIV}


def base_routes():
    return {
        ("GET", INT): {"offset": 0, "limit": 200, "count": 1, "totalCount": 1,
                       "data": [{"id": "uuid-1", "internalReference": "default", "name": "Default"}]},
        ("GET", INT + "/uuid-1/networks"): {"totalCount": 2, "data": [
            {"id": "n1", "name": "LAN", "enabled": True, "vlanId": 1, "management": "GATEWAY"},
            {"id": "n2", "name": "IoT", "enabled": True, "vlanId": 20, "management": "GATEWAY"}]},
        ("GET", INT + "/uuid-1/networks/n1"): {"ipv4Configuration": {"hostIpAddress": "192.168.1.1", "prefixLength": 24}},
        ("GET", INT + "/uuid-1/networks/n2"): {"ipv4Configuration": {"hostIpAddress": "192.168.20.1", "prefixLength": 24}},
        ("GET", INT + "/uuid-1/vpn/servers"): {"totalCount": 1, "data": [{"id": "x", "name": "Teleport-ish", "type": "WIREGUARD"}]},
        ("GET", "/proxy/network/api/s/default/rest/networkconf"): {"data": [WG]},
    }


def test_sites_and_networks_via_integration_api():
    d, log = make(base_routes())
    assert [s.name for s in d.list_sites()] == ["Default"]
    nets = d.list_networks(SITE)
    assert [(n.name, n.cidr, n.vlan) for n in nets] == [("LAN", "192.168.1.0/24", 1), ("IoT", "192.168.20.0/24", 20)]
    assert all(h.get("X-API-KEY") == "KEY" for _, _, _, h in log)


def test_wireguard_server_from_classic_api_derives_pubkey():
    d, _ = make(base_routes())
    s = d.get_wireguard_server(SITE)
    assert (s.port, s.subnet, s.server_address) == (51820, "10.250.1.0/28", "10.250.1.1")
    assert s.public_key == keys.public_from_private(SERVER_PRIV)
    assert s.created


def test_server_never_created_gives_clear_error():  # problem 1
    r = base_routes()
    r[("GET", "/proxy/network/api/s/default/rest/networkconf")] = {"data": []}
    d, _ = make(r)
    with pytest.raises(DriverError, match="Create"):
        d.get_wireguard_server(SITE)


def test_create_client_falls_back_to_batch_endpoint():
    r = base_routes()
    users = "/proxy/network/v2/api/site/default/wireguard/wg1/users"
    r[("POST", users + "/batch_add")] = HttpError(404, "nope")
    r[("POST", users + "/batch")] = [{"_id": "c9", "name": "catflap-laptop-home"}]
    d, log = make(r)
    srv = d.get_wireguard_server(SITE)
    rec = d.create_client(SITE, srv, "catflap-laptop-home", "PUB", "10.250.1.2/32")
    assert rec.id == "c9" and rec.address == "10.250.1.2/32"
    body = [b for m, p, b, _ in log if p.endswith("/batch")][0]
    assert body == [{"name": "catflap-laptop-home", "interface_ip": "10.250.1.2", "public_key": "PUB"}]


def test_classic_login_fallback_on_401():
    r = base_routes()
    calls = {"n": 0}

    def netconf(client):
        calls["n"] += 1
        if "X-API-KEY" in client.headers:
            return HttpError(401, "unauthorized")
        return {"data": [WG]}
    r[("GET", "/proxy/network/api/s/default/rest/networkconf")] = netconf
    r[("POST", "/api/auth/login")] = {}
    d, log = make(r, username="localadmin", password="pw")
    assert d.get_wireguard_server(SITE).port == 51820
    assert any(p == "/api/auth/login" for _, p, _, _ in log)
    assert d._classic.headers.get("X-CSRF-Token") == "CSRF"


def test_classic_401_without_credentials_explains():
    r = base_routes()
    r[("GET", "/proxy/network/api/s/default/rest/networkconf")] = HttpError(401, "unauthorized")
    d, _ = make(r)
    with pytest.raises(DriverError, match="LOCAL"):
        d.get_wireguard_server(SITE)


def test_allocate_address_skips_server_and_used():
    d, _ = make(base_routes())
    srv = d.get_wireguard_server(SITE)
    assert d.allocate_address(srv, ["10.250.1.2/32"]) == "10.250.1.3/32"


def test_cloud_mode_uses_connector_and_key(monkeypatch):
    from catflap.drivers import unifi
    log = []
    gw = GatewayConfig(name="g", vendor="unifi", url=unifi.cloud_url("CID"),
                       options={"mode": "cloud", "console_id": "CID"})
    d = UniFiDriver(gw, {"api_key": "CLOUDKEY"},
                    client_factory=lambda gw, h: FakeClient(base_routes(), log, h))
    assert gw.url == "https://api.ui.com/v1/connector/consoles/CID"
    assert d.get_wireguard_server(SITE).port == 51820
    assert all(h.get("X-API-KEY") == "CLOUDKEY" for *_, h in log)


def test_cloud_401_does_not_ask_for_local_login():
    r = base_routes()
    r[("GET", "/proxy/network/api/s/default/rest/networkconf")] = HttpError(403, "insufficient permissions")
    gw = GatewayConfig(name="g", vendor="unifi", url="https://api.ui.com/v1/connector/consoles/X",
                       options={"mode": "cloud"})
    d = UniFiDriver(gw, {"api_key": "K"}, client_factory=lambda gw, h: FakeClient(r, [], h))
    with pytest.raises(DriverError, match="cloud connector"):
        d.get_wireguard_server(SITE)


def test_list_cloud_consoles():
    from catflap.drivers import unifi
    routes = {("GET", "/v1/hosts"): {"data": [
        {"id": "AA:11", "type": "console", "reportedState": {"name": "Home UDM", "state": "connected"}},
        {"id": "BB:22", "type": "console", "reportedState": {"hostname": "office-ucg"}},
        {"id": "CC", "type": "protect-camera"}]}}
    c = FakeClient(routes, [], {"X-API-KEY": "K"})
    got = unifi.list_cloud_consoles("K", client=c)
    assert [(h["id"], h["name"]) for h in got] == [("AA:11", "Home UDM"), ("BB:22", "office-ucg")]


def test_list_cloud_consoles_bad_key():
    from catflap.drivers import unifi
    c = FakeClient({("GET", "/v1/hosts"): HttpError(401, "unauthorized")}, [], {})
    with pytest.raises(DriverError, match="unifi.ui.com"):
        unifi.list_cloud_consoles("bad", client=c)
