import json

import pytest

from catflap import keys
from catflap.bundle import BundleError
from catflap.core import CatflapError, Manager
from catflap.secrets import SecretStore, profile_key
from catflap.store import Store

from conftest import FakeBackend, FakeDriver


def test_provision_end_to_end(manager, backend):
    res = manager.provision("home-gw", "home", ["LAN", "20"], device="laptop1")
    p = res.profile
    assert p.routes == ["192.168.1.0/24", "192.168.20.0/24", "10.250.1.1/32"]
    assert p.endpoint_host == "home.example.net"          # DDNS preferred (#9)
    assert p.addresses == ["10.250.1.2/32"]
    assert p.client_id == "c1" and p.device == "laptop1"   # #11
    assert p.ifname == "cf-home" and p.backend_id == "uuid-home"
    cfg = backend.installed["home"]
    assert cfg.dns == [] and cfg.keepalive == 25 and "0.0.0.0/0" not in cfg.allowed_ips
    # the private key never left the device; the gateway only saw the public key
    priv = manager.secrets.get(profile_key("home", "private_key"))
    assert FakeDriver.state["clients"][0].public_key == keys.public_from_private(priv)
    assert "private_key" not in json.dumps(manager.store._data["profiles"])


def test_provision_same_device_twice_requires_replace(manager):
    manager.provision("home-gw", "home", ["LAN"], device="laptop1")
    with pytest.raises(CatflapError, match="already exists"):
        manager.provision("home-gw", "home", ["LAN"], device="laptop1")
    res = manager.provision("home-gw", "home", ["LAN"], device="laptop1", replace=True)
    assert FakeDriver.state["revoked"] == ["c1"]
    assert res.profile.client_id == "c2"


def test_provision_unknown_network(manager):
    with pytest.raises(CatflapError, match="not found"):
        manager.provision("home-gw", "home", ["Nope"])


def test_failed_install_revokes_orphan(manager, backend):
    def boom(profile, cfg):
        raise RuntimeError("nm exploded")
    backend.install = boom
    with pytest.raises(RuntimeError):
        manager.provision("home-gw", "home", ["LAN"], device="d")
    assert FakeDriver.state["revoked"] == ["c1"]


def test_import_conf(manager, backend, unifi_conf):
    res = manager.import_conf(unifi_conf, "office", vendor="unifi", routes=["10.10.0.0/16"],
                              endpoint="office.example.net")
    p = res.profile
    assert p.routes == ["10.10.0.0/16"] and p.endpoint_host == "office.example.net"
    assert backend.installed["office"].dns == []
    assert any("DNS" in m for m in res.messages)


def test_import_same_key_twice_refused(manager, unifi_conf):  # #11
    manager.import_conf(unifi_conf, "office", routes=["10.10.0.0/16"])
    with pytest.raises(CatflapError, match="own client"):
        manager.import_conf(unifi_conf, "office2", routes=["10.11.0.0/16"])


def test_split_dns_validation(manager, unifi_conf):
    with pytest.raises(CatflapError, match="requires --dns-domain"):
        manager.import_conf(unifi_conf, "o", routes=["10.10.0.0/16"], dns_servers=["10.10.0.1"])
    with pytest.raises(CatflapError, match="not inside a routed network"):
        manager.import_conf(unifi_conf, "o", routes=["10.10.0.0/16"], dns_domain="corp.lan",
                            dns_servers=["8.8.8.8"])
    p = manager.import_conf(unifi_conf, "o", routes=["10.10.0.0/16"], dns_domain="corp.lan",
                            dns_servers=["10.10.0.1"]).profile
    assert p.dns_domain == "corp.lan"


def test_connect_and_failed_health_disconnects(manager, backend):
    manager.provision("home-gw", "home", ["LAN"], device="d")
    ok = manager.connect("home")
    assert ok.ok and "home" in backend.active
    manager.disconnect("home")
    backend.rx = 0
    backend.ping_ok = False
    bad = manager.connect("home")
    assert not bad.ok and "home" not in backend.active   # auto-down on no handshake
    assert any(f.code == "no-handshake" for f in bad.findings)


def test_connect_blocked_by_preflight(manager, backend):
    from catflap.platform.base import LocalAddress, LocalNetwork, LocalRoute
    manager.provision("home-gw", "home", ["LAN"], device="d")
    backend.local = LocalNetwork(routes=[LocalRoute("192.168.1.0/24", "wlp0")],
                                 addresses=[LocalAddress("192.168.1.9/24", "wlp0")])
    manager.store.settings()["public_ip_url"] = ""
    res = manager.connect("home")
    assert not res.ok and ("up", "home") not in backend.calls


def test_remove_with_revoke(manager, backend):
    manager.provision("home-gw", "home", ["LAN"], device="d")
    msgs = manager.remove("home", revoke=True)
    assert FakeDriver.state["revoked"] == ["c1"]
    assert "home" not in manager.store.profiles()
    assert manager.secrets.get(profile_key("home", "private_key")) is None
    assert any("Revoked" in m for m in msgs)


def test_export_import_roundtrip(manager, tmp_path, monkeypatch):
    manager.provision("home-gw", "home", ["LAN"], device="d", color="#123456")
    text = manager.export("correct horse")
    assert "KEY" not in text and "home.example.net" not in text   # encrypted
    with pytest.raises(BundleError, match="passphrase"):
        Manager(Store(tmp_path / "x"), SecretStore("file", tmp_path / "x"), FakeBackend()).import_bundle(text, "wrong pass")

    other_root = tmp_path / "pc2"
    b2 = FakeBackend()
    m2 = Manager(Store(other_root), SecretStore("file", other_root), b2,
                 driver_factory=lambda gw, sec: FakeDriver(gw, sec))
    msgs = m2.import_bundle(text, "correct horse")
    assert "home" in m2.store.profiles() and "home-gw" in m2.store.gateways()
    assert m2.secrets.get("gateway/home-gw/api_key") == "KEY"
    assert m2.store.profile("home").color == "#123456"
    assert "home" in b2.installed                         # connection recreated on the new PC
    assert any("one client per device" in m for m in msgs)


def test_export_gateways_only(manager):
    manager.provision("home-gw", "home", ["LAN"], device="d")
    from catflap.bundle import open_bundle
    payload = open_bundle(manager.export(None, gateways_only=True), None)
    assert payload["profiles"] == {} and "gateway/home-gw/api_key" in payload["secrets"]
    assert not any(k.startswith("profile/") for k in payload["secrets"])


def test_multiple_gateways_independent(manager):
    manager.add_gateway("office-gw", "unifi", "https://192.0.2.2", api_key="KEY2")
    assert manager.secrets.get("gateway/office-gw/api_key") == "KEY2"
    assert manager.secrets.get("gateway/home-gw/api_key") == "KEY"
    a = manager.provision("home-gw", "home", ["LAN"], device="d").profile
    b = manager.provision("office-gw", "office", ["IoT"], device="d").profile
    assert a.color != b.color and a.ifname != b.ifname


def test_remove_gateway_in_use(manager):
    manager.provision("home-gw", "home", ["LAN"], device="d")
    with pytest.raises(CatflapError, match="used by"):
        manager.remove_gateway("home-gw")


def test_store_reloads_external_changes_only(tmp_path):
    a, b = Store(tmp_path), Store(tmp_path)
    a.settings()["x"] = 1
    a.save()
    b.reload()
    assert b.settings()["x"] == 1
    b.settings()["y"] = 2          # unsaved in-memory change survives a no-op reload
    b.reload()
    assert b.settings()["y"] == 2
