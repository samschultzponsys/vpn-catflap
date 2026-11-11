import pytest

from catflap import safety, wgconf


def test_unifi_conf_made_safe(unifi_conf):
    cfg = wgconf.parse_file(unifi_conf)
    res = safety.make_safe(cfg, ["192.168.1.0/24", "192.168.20.0/24"])
    c = res.config
    assert c.allowed_ips == ["192.168.1.0/24", "192.168.20.0/24"]   # #3 split tunnel
    assert c.dns == []                                              # #4
    assert c.keepalive == 25                                        # #6
    assert all(":" not in a for a in c.addresses)                   # #5
    assert any("DNS" in r for r in res.removed)


def test_full_tunnel_only_falls_back_to_vpn_subnet(unifi_conf):
    res = safety.make_safe(wgconf.parse_file(unifi_conf))
    assert res.config.allowed_ips == ["192.168.5.0/24"]
    assert "0.0.0.0/0" not in res.config.allowed_ips
    assert res.warnings


@pytest.mark.parametrize("route", ["0.0.0.0/0", "::/0", "2001:db8::/32"])
def test_default_and_v6_routes_dropped(unifi_conf, route):
    res = safety.make_safe(wgconf.parse_file(unifi_conf), [route, "10.1.0.0/16"])
    assert res.config.allowed_ips == ["10.1.0.0/16"]


def test_too_broad_route_rejected(unifi_conf):
    with pytest.raises(safety.SafetyError):
        safety.make_safe(wgconf.parse_file(unifi_conf), ["0.0.0.0/1"])


@pytest.mark.parametrize("bad", ["~", "~.", "", "bad domain", "-x.lan"])
def test_dns_domain_never_catch_all(bad):
    with pytest.raises(safety.SafetyError):
        safety.validate_dns_domain(bad)


def test_dns_domain_ok():
    assert safety.validate_dns_domain("~Office.LAN.") == "office.lan"


def test_vpn_subnet_collision():
    w = safety.check_vpn_subnet_collisions("a", "192.168.5.0/24", {"b": "192.168.5.0/24", "c": "10.250.2.0/28"})
    assert len(w) == 1 and "'b'" in w[0]
