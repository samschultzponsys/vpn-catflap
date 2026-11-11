import pytest

from catflap import wgconf


def test_parse_unifi_conf(unifi_conf):
    c = wgconf.parse_file(unifi_conf)
    assert c.addresses == ["192.168.5.2/32"]
    assert c.dns == ["192.168.5.1"]
    assert c.allowed_ips == ["0.0.0.0/0", "::/0"]
    assert (c.endpoint_host, c.endpoint_port) == ("203.0.113.10", 51820)
    assert c.keepalive is None


def test_roundtrip(unifi_conf):
    c = wgconf.parse_file(unifi_conf)
    assert wgconf.parse_text(wgconf.render(c)) == c


def test_ipv6_endpoint_and_comments():
    c = wgconf.parse_text("[Interface]\nPrivateKey = a # x\nAddress = 10.0.0.2/32\n"
                          "[Peer]\nPublicKey = b\nAllowedIPs = 10.0.0.0/24\nEndpoint = [2001:db8::1]:51820\n"
                          "PersistentKeepalive = 25\n")
    assert c.endpoint_host == "2001:db8::1" and c.keepalive == 25
    assert wgconf.format_endpoint(c.endpoint_host, 51820) == "[2001:db8::1]:51820"


def test_multiple_peers_rejected():
    text = ("[Interface]\nPrivateKey = a\nAddress = 10.0.0.2/32\n" +
            "[Peer]\nPublicKey = b\nAllowedIPs = 10.0.0.0/24\nEndpoint = h:1\n" * 2)
    with pytest.raises(wgconf.ConfError, match="exactly one \\[Peer\\]"):
        wgconf.parse_text(text)


def test_bad_endpoint():
    with pytest.raises(wgconf.ConfError):
        wgconf.parse_endpoint("no-port")
