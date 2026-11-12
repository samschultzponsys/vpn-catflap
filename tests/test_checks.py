from catflap import checks
from catflap.model import Profile
from catflap.platform.base import LocalAddress, LocalNetwork, LocalRoute


def prof(**kw):
    d = dict(name="home", ifname="cf-home", vendor="unifi", addresses=["10.250.1.2/32"],
             peer_public_key="x", endpoint_host="home.example.net", endpoint_port=51820,
             routes=["192.168.1.0/24"], ping_target="10.250.1.1", vpn_subnet="10.250.1.0/28",
             networks=[{"name": "LAN", "cidr": "192.168.1.0/24", "gateway_ip": "192.168.1.1"}])
    d.update(kw)
    return Profile(**d)


def codes(f):
    return {x.code for x in f}


def hotel():
    return LocalNetwork(routes=[LocalRoute("0.0.0.0/0", "wlp0", "192.168.1.1"),
                                LocalRoute("192.168.1.0/24", "wlp0")],
                        addresses=[LocalAddress("192.168.1.50/24", "wlp0")])


def resolver(ip="203.0.113.10"):
    return lambda host: [ip]


def test_clean_preflight_passes():
    f = checks.preflight(prof(), LocalNetwork(), {}, resolver=resolver())
    assert not checks.has_errors(f) and "preflight-ok" in codes(f)


def test_overlap_with_hotel_lan_is_worked_around():  # problem 8: no longer a refusal
    f = checks.preflight(prof(), hotel(), {}, resolver=resolver(), my_public_ip=lambda: "198.51.100.7")
    assert "local-overlap" in codes(f) and not checks.has_errors(f)
    msg = next(x for x in f if x.code == "local-overlap").message
    assert "192.168.1.1" in msg and "192.168.1.50" in msg


def test_conflict_safe_routes_skip_self_and_local_router():
    import ipaddress
    routes, excluded = checks.conflict_safe_routes(["192.168.1.0/24", "10.9.0.0/24"], hotel(), "cf-home")
    assert excluded == ["192.168.1.1", "192.168.1.50"]
    assert "10.9.0.0/24" in routes                       # untouched
    covered = [ipaddress.ip_network(r) for r in routes if r != "10.9.0.0/24"]
    for ip in ("192.168.1.1", "192.168.1.50"):
        assert not any(ipaddress.ip_address(ip) in n for n in covered)
    for ip in ("192.168.1.2", "192.168.1.49", "192.168.1.51", "192.168.1.254"):
        assert any(ipaddress.ip_address(ip) in n for n in covered)
    assert all(n.prefixlen > 24 for n in covered)        # more specific than the local /24


def test_on_site_by_router_when_public_ip_unknown():
    p = prof(networks=[{"name": "LAN", "cidr": "192.168.1.0/24", "gateway_ip": "192.168.1.1"}])
    f = checks.preflight(p, hotel(), {}, resolver=resolver(), my_public_ip=lambda: None)
    assert "on-site" in codes(f)


def test_on_site_by_wan_ip():
    f = checks.preflight(prof(wan_ip="198.51.100.9"), hotel(), {}, resolver=resolver(),
                         my_public_ip=lambda: "198.51.100.9")
    assert "on-site" in codes(f)


def test_on_site_detected():  # problem 12
    f = checks.preflight(prof(), hotel(), {}, resolver=resolver(), my_public_ip=lambda: "203.0.113.10")
    assert "on-site" in codes(f)


def test_already_reachable_linked_site():  # problem 12
    f = checks.preflight(prof(), LocalNetwork(), {}, resolver=resolver(), reachable=lambda h: True)
    assert "already-reachable" in codes(f)


def test_overlap_with_other_tunnel():  # problem 8
    other = prof(name="office", ifname="cf-office", vpn_subnet="10.250.1.0/28")
    f = checks.preflight(prof(), LocalNetwork(), {"office": (other, True)}, resolver=resolver())
    assert "tunnel-overlap" in codes(f) and "vpn-subnet-collision" in codes(f)
    assert checks.has_errors(f)
    f2 = checks.preflight(prof(), LocalNetwork(), {"office": (other, False)}, resolver=resolver())
    assert not checks.has_errors(f2)


def test_own_tunnel_routes_ignored():
    ln = LocalNetwork(routes=[LocalRoute("192.168.1.0/24", "cf-home")])
    assert not checks.has_errors(checks.preflight(prof(), ln, {}, resolver=resolver()))


def test_cgnat_and_double_nat():  # problem 10
    assert "cgnat" in codes(checks.preflight(prof(wan_ip="100.70.1.2"), LocalNetwork(), {}, resolver=resolver()))
    assert "double-nat" in codes(checks.preflight(prof(wan_ip="192.168.0.2"), LocalNetwork(), {}, resolver=resolver()))
    assert "endpoint-mismatch" in codes(checks.preflight(prof(wan_ip="198.51.100.1"), LocalNetwork(), {}, resolver=resolver()))


def test_endpoint_raw_ip_and_unresolvable():  # problem 9
    assert "endpoint-raw-ip" in codes(checks.preflight(prof(endpoint_host="203.0.113.10"), LocalNetwork(), {}))

    def boom(h):
        raise OSError("Name or service not known")
    f = checks.preflight(prof(), LocalNetwork(), {}, resolver=boom)
    assert "endpoint-unresolvable" in codes(f) and checks.has_errors(f)


def test_health_no_handshake(backend):  # problem 1
    p = prof()
    backend.active.add("home")
    backend.rx = 0
    backend.ping_ok = False
    f = checks.health(p, backend, wait=0, sleep=lambda s: None)
    assert f[0].code == "no-handshake" and f[0].problem == 1


def test_health_ok(backend):
    from types import SimpleNamespace
    backend.active.add("home")
    backend.installed["home"] = SimpleNamespace(allowed_ips=["10.250.1.1/32"])
    f = checks.health(prof(), backend, wait=0, sleep=lambda s: None)
    assert {"handshake", "ping"} <= codes(f)
