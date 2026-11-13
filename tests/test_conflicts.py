from catflap import conflicts
from catflap.platform.base import LocalAddress, LocalNetwork


def test_windows_programs():
    progs = [{"name": "Tailscale", "version": "1.80"}, {"name": "NordVPN"},
             {"name": "Cisco Secure Client - AnyConnect VPN", "version": "5.1"},
             {"name": "Notepad++"}, {"name": "Proton VPN"}]
    keys = [f.key for f in conflicts.match_programs(progs)]
    assert keys == ["tailscale", "nordvpn", "protonvpn", "cisco"]
    found = conflicts.detect(None, "win32", programs=progs)
    assert found[0].label == "Tailscale 1.80" and not found[0].can_uninstall


def test_wireguard_app_always_listed(tmp_path):
    progs = [{"name": "WireGuard", "version": "1.1.1", "uninstall": "u"}]

    class B:
        engine = tmp_path / "wireguard.exe"

        def downloaded_msi(self):
            return None
    f = conflicts.detect(B(), "win32", programs=progs)[0]           # still listed before the engine
    assert f.key == "wireguard-app" and f.needs_engine and "still uses it" in f.detail
    B.engine.write_text("")
    f = conflicts.detect(B(), "win32", programs=progs)[0]
    assert f.key == "wireguard-app" and not f.needs_engine


def test_to_warn_respects_versions():
    settings = {}
    pia = conflicts.match_programs([{"name": "Private Internet Access", "version": "3.6"}])
    conflicts.silence(settings, pia)
    assert conflicts.to_warn(pia, settings) == []
    newer = conflicts.match_programs([{"name": "Private Internet Access", "version": "3.7"}])
    assert [f.key for f in conflicts.to_warn(newer, settings)] == ["pia"]


def test_linux_interfaces_skip_catflap_and_plain_nics():
    class B:
        def local_network(self):
            return LocalNetwork(addresses=[LocalAddress("192.168.1.5/24", "wlp0s20f3"),
                                           LocalAddress("10.250.1.2/32", "cf-home"),
                                           LocalAddress("100.101.1.1/32", "tailscale0"),
                                           LocalAddress("10.5.0.2/32", "wg0-mullvad"),
                                           LocalAddress("10.0.0.1/24", "ztabcdef12")])
    found = conflicts.detect(B(), "linux")
    assert [f.key for f in found] == ["tailscale", "zerotier", "mullvad"]
    assert found[0].detail == "interface tailscale0 is up"


def test_detect_never_raises():
    class B:
        def local_network(self):
            raise RuntimeError("no NM")
    assert conflicts.detect(B(), "linux") == []
