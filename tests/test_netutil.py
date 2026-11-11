import pytest

from catflap import netutil


@pytest.mark.parametrize("name", ["Ponsys Office (Main)!", "home", "a" * 40, "Ünïcødé síte", "--x--"])
def test_ifname_is_valid(name):
    n = netutil.sanitize_ifname(name)
    assert 1 <= len(n) <= 15
    assert all(c.isalnum() or c in "-_" for c in n)
    assert n.isascii()
    assert n.startswith("cf-")


def test_ifname_examples():
    assert netutil.sanitize_ifname("Ponsys Office") == "cf-ponsys-offic"
    assert netutil.sanitize_ifname("home") == "cf-home"


def test_unique_ifname():
    taken = ["cf-ponsys-offic"]
    n = netutil.unique_ifname("Ponsys Office", taken)
    assert n != taken[0] and len(n) <= 15


def test_overlap():
    assert netutil.overlaps("192.168.1.0/24", "192.168.1.128/25")
    assert not netutil.overlaps("192.168.1.0/24", "192.168.2.0/24")
    assert not netutil.overlaps("192.168.1.0/24", "::/0")


def test_free_host_skips_gateway_and_used():
    assert netutil.free_host("10.250.1.0/28", ["10.250.1.2/32"]) == "10.250.1.3"
    with pytest.raises(ValueError):
        netutil.free_host("10.250.1.0/30", ["10.250.1.2/32"])


def test_cgnat_and_private():
    assert netutil.is_cgnat("100.72.1.1")
    assert not netutil.is_cgnat("203.0.113.1")
    assert netutil.is_private("192.168.1.1") and not netutil.is_private("100.72.1.1")


def test_suggest_subnet():
    assert netutil.suggest_vpn_subnet(3) == "10.250.3.0/28"


def test_slug_allows_long_names():
    assert netutil.slug("Ponsys Office (Main Building)") == "ponsys-office-main-building"
    assert netutil.slug("Mum's House") == "mum-s-house"
