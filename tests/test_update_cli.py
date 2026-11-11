import json

from catflap import cli, update
from catflap.update import InstallInfo, ReleaseInfo

REL = ReleaseInfo(version="9.9", url="https://github.com/x/releases/v9.9",
                  assets={"catflap-9.9-1.noarch.rpm": "https://dl/catflap-9.9-1.noarch.rpm",
                          "catflap-9.9-py3-none-any.whl": "https://dl/catflap-9.9-py3-none-any.whl"})


def test_check_detects_newer_and_gives_method_instructions():
    st = update.check({}, fetch=lambda: REL, install=InstallInfo("flatpak", "io.x.CatFlap"))
    assert st.available
    assert st.instructions[-1] == "flatpak install --user -y ~/Downloads/catflap-9.9.flatpak"


def test_instructions_per_method():
    assert "dnf install https://dl/catflap-9.9-1.noarch.rpm" in update.instructions(InstallInfo("rpm"), REL)[0]
    assert "pipx install --force https://dl/catflap-9.9-py3-none-any.whl" == update.instructions(InstallInfo("pipx"), REL)[0]
    assert update.instructions(InstallInfo("source"), REL)[-1] == "git checkout v9.9"


def test_check_cached_once_a_day():
    settings = {}
    calls = []
    update.check(settings, fetch=lambda: calls.append(1) or REL, install=InstallInfo("pip"))
    update.check(settings, force=False, fetch=lambda: calls.append(1) or REL, install=InstallInfo("pip"))
    assert len(calls) == 1


def test_apply_update_rpm_uses_pkexec():
    st = update.check({}, fetch=lambda: REL, install=InstallInfo("rpm", automatic=True))
    ran = []
    ok, _ = update.apply_update(st, run=lambda c: ran.append(c) or 0)
    assert ok and ran[0][:3] == ["pkexec", "dnf", "install"]


def test_check_failure_is_reported():
    def boom():
        raise OSError("offline")
    st = update.check({}, fetch=boom, install=InstallInfo("pip"))
    assert st.error and not st.available


def test_cli_import_list_json(manager, unifi_conf, capsys):
    rc = cli.main(["--json", "import", str(unifi_conf), "lab", "--route", "10.20.0.0/16"], manager=manager)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["profile"]["routes"] == ["10.20.0.0/16"]
    rc = cli.main(["--json", "list"], manager=manager)
    assert json.loads(capsys.readouterr().out)["profiles"]["lab"]["active"] is False


def test_cli_error_exit_code(manager, capsys):
    assert cli.main(["show", "missing"], manager=manager) == 1
    assert "no site profile" in capsys.readouterr().err


def test_cli_up_failure_exit_code(manager, backend, capsys):
    manager.provision("home-gw", "home", ["LAN"], device="d")
    manager.store.settings()["public_ip_url"] = ""
    backend.rx = 0
    backend.ping_ok = False
    assert cli.main(["up", "home"], manager=manager) == 3


def test_flatpak_update_downloads_bundle_and_explains(monkeypatch, tmp_path):
    rel = ReleaseInfo(version="9.9", url="u", assets={"catflap-9.9.flatpak": "https://dl/catflap-9.9.flatpak"})
    st = update.check({}, fetch=lambda: rel, install=InstallInfo("flatpak", "io.x.CatFlap", True))
    # full copy-pasteable commands before anything is downloaded
    assert update.flatpak_commands(st) == [
        "curl -L -o ~/Downloads/catflap-9.9.flatpak https://dl/catflap-9.9.flatpak",
        "flatpak install --user -y ~/Downloads/catflap-9.9.flatpak"]
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(update, "_download", lambda url, d: d / "catflap-9.9.flatpak")
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/flatpak-spawn")
    ran = []
    # host commands not permitted -> downloaded, one command left, then restart
    res = update.apply_update(st, run=lambda c: ran.append(c) or 1)
    assert not res.done and res.restart and res.downloaded == tmp_path / "catflap-9.9.flatpak"
    assert res.commands == [f"flatpak install --user -y {tmp_path / 'catflap-9.9.flatpak'}"]
    # permitted -> installs on the host
    ran.clear()
    res = update.apply_update(st, run=lambda c: ran.append(c) or 0)
    assert res.done and res.restart and ran[-1][:3] == ["flatpak-spawn", "--host", "flatpak"]


def test_restart_command_runs_latest_version(monkeypatch):
    assert update.restart_command(InstallInfo("flatpak")) == ["flatpak-spawn", "--latest-version", "catflap-tray"]
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    assert update.restart_command(InstallInfo("pip"))[-2:] == ["catflap", "tray"]
    monkeypatch.setattr(update.shutil, "which", lambda n: "/usr/bin/catflap-tray")
    assert update.restart_command(InstallInfo("rpm")) == ["/usr/bin/catflap-tray"]


def test_cli_gateway_add_cloud(manager, monkeypatch, capsys):
    from catflap.drivers import unifi
    monkeypatch.setattr(unifi, "list_cloud_consoles",
                        lambda key: [{"id": "AA:11", "name": "Home UDM"}, {"id": "BB", "name": "Office"}])
    monkeypatch.setenv("CATFLAP_API_KEY", "K")
    assert cli.main(["gateway", "add", "home", "--vendor", "unifi", "--cloud"], manager=manager) == 1
    assert "--console" in capsys.readouterr().err
    assert cli.main(["gateway", "add", "home2", "--vendor", "unifi", "--cloud", "--console", "home udm"],
                    manager=manager) == 0
    g = manager.store.gateway("home2")
    assert g["url"].endswith("/consoles/AA:11") and manager.secrets.get("gateway/home2/api_key") == "K"
