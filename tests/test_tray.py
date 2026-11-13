"""Headless tests of the window, tray and wizards (skipped without PySide6)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from catflap import tray, ui_wizards  # noqa: E402
from catflap.ui_common import make_icon  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def no_popups(monkeypatch):
    shown = []
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, kind,
                            lambda *a, k=kind, **kw: shown.append((k, a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(ui_wizards, "fetch_fingerprint", lambda url: "AA:BB")
    return shown


def wait_for(qapp, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        qapp.processEvents()
        if time.monotonic() > end:
            raise AssertionError("timed out")
        time.sleep(0.01)


def test_icon_states(qapp):
    off = make_icon([]).pixmap(64, 64).toImage()
    on = make_icon(["#ff0000"]).pixmap(64, 64).toImage()          # default style: site color
    assert off != on
    tile = on.pixelColor(4, 32)
    assert tile.red() > 150 and tile.blue() < 100
    dot = on.pixelColor(51, 51)                                     # green status dot
    assert dot.green() > 150 and dot.red() < 100
    assert off.pixelColor(4, 32).alpha() == 0                      # disconnected: outline only
    err = make_icon([], error=True).pixmap(64, 64).toImage().pixelColor(51, 51)
    assert err.red() > 180 and err.green() < 100


def test_icon_styles_for_several_sites(qapp):
    red, blue = "#ff0000", "#0000ff"
    grad = make_icon([red, blue]).pixmap(64, 64).toImage()          # blend across both
    top_left, bottom_right = grad.pixelColor(6, 6), grad.pixelColor(58, 40)
    assert top_left.red() > top_left.blue() and bottom_right.blue() > bottom_right.red()
    c0 = make_icon([red, blue], style="cycle", phase=0.0).pixmap(64, 64).toImage().pixelColor(4, 32)
    c1 = make_icon([red, blue], style="cycle", phase=1.0).pixmap(64, 64).toImage().pixelColor(4, 32)
    assert c0.red() > c0.blue() and c1.blue() > c1.red()           # cycles from red to blue
    b = make_icon([red, blue], style="blue").pixmap(64, 64).toImage().pixelColor(4, 32)
    assert b.blue() > 150 and b.red() < 100                        # always the blue app icon


def test_tray_menu_and_basic_window(qapp, manager, backend):
    manager.provision("home-gw", "home", ["LAN"], device="d", color="#ff0000", label="Home Sweet Home")
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    app.profiles = manager.store.profiles()
    app.status = manager.status()
    app._render()
    labels = [a.text() for a in app.menu.actions()]
    assert "Home Sweet Home: Not connected  (Connect)" in labels
    assert "Add a site…" in labels and "Open CatFlap" in labels and "Quit" in labels
    backend.active.add("home")
    app.status = manager.status()
    app._render()
    assert "Home Sweet Home connected" in app.tray.toolTip()
    app.show_window()
    basic = app.window.basic
    assert len(basic.cards) == 1 and basic.cards[0].button.text() == "Disconnect"
    app.window.set_mode("advanced")
    assert app.settings["ui_mode"] == "advanced"
    assert app.window.advanced.sites.rowCount() == 1
    assert app.window.advanced.gws.rowCount() == 1
    app.settings["close_behavior"] = "tray"
    app.window.close()
    assert not app.window.isVisible() and not app.quitting   # closed to the tray


def test_basic_view_empty_state(qapp, manager):
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    app.show_window()
    assert app.window.basic.stack.currentIndex() == 0          # "Add your first site"


def test_first_run_wizard_previews_and_choice(qapp):
    w = ui_wizards.FirstRunWizard()
    assert w.mode == "basic"
    w.group.button(1).setChecked(True)
    assert w.mode == "advanced"
    assert w.add_now.isChecked()


def pick(w, vendor):
    vp = w.page(ui_wizards.P_VENDOR)
    next(b for b in vp.group.buttons() if b.property("vendor") == vendor).setChecked(True)
    w.next()


CONSOLES = [{"id": "AA:11", "name": "Home UDM", "model": "UDM Pro", "online": True},
            {"id": "BB:22", "name": "Office UCG", "model": "", "online": True}]


def test_site_wizard_unifi_cloud(qapp, manager, no_popups, monkeypatch):
    from catflap.drivers import unifi
    monkeypatch.setattr(unifi, "list_cloud_consoles", lambda key: CONSOLES)
    w = ui_wizards.SiteWizard(manager)
    w.show()
    pick(w, "unifi")
    assert w.currentId() == ui_wizards.P_LOGIN
    access = w.currentPage()
    assert access.cloud.isChecked() and access.address.isHidden()   # no router address asked
    access.key.setText("CLOUDKEY")
    w.next()
    assert w.currentId() == ui_wizards.P_CONSOLE, no_popups
    cp = w.currentPage()
    assert cp.list.count() == 2
    cp.list.setCurrentRow(1)
    w.next()
    assert w.currentId() == ui_wizards.P_SITE, no_popups
    gw = manager.store.gateway(w.gateway)
    assert gw["url"] == "https://api.ui.com/v1/connector/consoles/BB:22"
    assert gw["options"]["mode"] == "cloud" and gw["options"]["console_name"] == "Office UCG"
    assert manager.secrets.get(f"gateway/{w.gateway}/api_key") == "CLOUDKEY"
    w.next()
    server = w.currentPage()
    wait_for(qapp, lambda: server.ok)
    w.next()
    assert w.currentPage().chosen() == ["192.168.1.0/24"]
    w.next()
    assert w.currentPage().name.text() == "Home"
    w.next()
    assert w.currentId() == ui_wizards.P_DONE, no_popups
    assert w.result.profile.gateway == w.gateway


def test_site_wizard_unifi_direct(qapp, manager, no_popups):
    w = ui_wizards.SiteWizard(manager)
    w.show()
    pick(w, "unifi")
    access = w.currentPage()
    access.direct.setChecked(True)
    assert not access.address.isHidden()
    access.address.setText("192.168.7.1")
    access.key.setText("LOCALKEY")
    w.next()
    assert w.currentId() == ui_wizards.P_SITE, no_popups
    assert manager.store.gateway(w.gateway)["url"] == "https://192.168.7.1"


def test_site_wizard_omada_typed_in(qapp, manager, no_popups):
    from catflap import keys
    _, srv_pub = keys.generate_keypair()
    w = ui_wizards.SiteWizard(manager)
    w.show()
    pick(w, "omada")
    assert w.currentId() == ui_wizards.P_METHOD
    assert w.currentPage().manual.isChecked()
    w.next()
    mp = w.currentPage()
    assert "Settings → VPN → WireGuard" in mp.help.toPlainText().replace("->", "→")
    mp.server_key.setText(srv_pub)
    mp.endpoint.setText("cabin.tplinkdns.com")
    mp.address.setText("10.250.3.2")
    w.next()
    assert w.currentId() == ui_wizards.P_SITE_NETS, no_popups
    nets = w.currentPage()
    nets.new.setText("192.168.50.0")
    nets._add()
    assert nets.chosen() == ["192.168.50.0/24"]
    w.next()
    w.currentPage().name.setText("Cabin")
    w.next()
    assert w.currentId() == ui_wizards.P_DONE, no_popups
    p = w.result.profile
    assert p.vendor == "omada" and p.routes == ["192.168.50.0/24"] and p.addresses == ["10.250.3.2/32"]
    assert p.endpoint_host == "cabin.tplinkdns.com"


def test_site_wizard_alta_file(qapp, manager, no_popups, unifi_conf):
    w = ui_wizards.SiteWizard(manager)
    w.show()
    pick(w, "alta")
    assert w.currentId() == ui_wizards.P_FILE
    fp = w.currentPage()
    assert "manage.alta.inc" in fp.help.toPlainText()
    fp.path.setText(str(unifi_conf))
    w.next()
    nets = w.currentPage()
    assert nets.chosen() == []               # the file only had a full tunnel; nothing pre-ticked
    assert not w.button(QtWidgets.QWizard.NextButton).isEnabled()
    nets.new.setText("10.20.0.0/24")
    nets._add()
    w.next()
    w.currentPage().name.setText("Lake house")
    w.next()
    assert w.currentId() == ui_wizards.P_DONE, no_popups
    assert w.result.profile.routes == ["10.20.0.0/24"]


def test_site_wizard_rejects_bad_file(qapp, manager, no_popups, tmp_path):
    bad = tmp_path / "x.conf"
    bad.write_text("not a wireguard file")
    w = ui_wizards.SiteWizard(manager)
    w.show()
    pick(w, "alta")
    w.currentPage().path.setText(str(bad))
    w.next()
    assert w.currentId() == ui_wizards.P_FILE
    assert any("doesn't look like a WireGuard" in m for _, m in no_popups)


def test_gateway_wizard_cloud_single_console(qapp, manager, no_popups, monkeypatch):
    from catflap.drivers import unifi
    monkeypatch.setattr(unifi, "list_cloud_consoles", lambda key: CONSOLES[:1])
    w = ui_wizards.GatewayWizard(manager)
    w.show()
    w.next()
    w.access_page.key.setText("K2")
    assert w.access_page.validatePage()
    assert manager.store.gateway(w.gateway)["options"]["console_id"] == "AA:11"
    # adding the same console again reuses it
    assert manager.add_unifi_cloud_gateway("K3", "AA:11", "Home UDM") == w.gateway


def test_update_dialog_flatpak_manual_then_restart(qapp, monkeypatch, tmp_path):
    from catflap import update, ui_update
    rel = update.ReleaseInfo(version="9.9", url="https://x/releases/v9.9", notes="* new stuff",
                             assets={"catflap-9.9.flatpak": "https://dl/catflap-9.9.flatpak"})
    st = update.check({}, fetch=lambda: rel, install=update.InstallInfo("flatpak", "io.x.CatFlap", True))
    settings = {}
    d = ui_update.UpdateDialog(st, settings=settings)
    assert "curl -L -o ~/Downloads/catflap-9.9.flatpak https://dl/catflap-9.9.flatpak" in d.cmds.toPlainText()
    assert d.go.text() == "Download and install"
    bundle = tmp_path / "catflap-9.9.flatpak"
    monkeypatch.setattr(update, "apply_update", lambda s: update.UpdateResult(
        False, "Downloaded", restart=True, downloaded=bundle,
        commands=[f"flatpak install --user -y {bundle}"]))
    d.do_update()
    assert d.cmds.toPlainText() == f"flatpak install --user -y {bundle}"
    assert not d.restart_btn.isHidden()
    d.do_restart()
    assert d.restart_requested and settings["pending_update"] == "9.9"


def test_after_restart_messages(monkeypatch):
    from catflap import ui_update
    monkeypatch.setattr(ui_update, "__version__", "1.3")
    s = {"last_run_version": "1.2"}
    assert ui_update.after_restart_message(s)[0] == "CatFlap updated"
    assert s["last_run_version"] == "1.3"
    assert ui_update.after_restart_message(s) is None
    s["pending_update"] = "1.4"
    assert ui_update.after_restart_message(s)[0] == "Update not installed yet"


def test_update_dialog_restarts_once_installed(qapp, monkeypatch, tmp_path):
    from catflap import update, ui_update
    rel = update.ReleaseInfo(version="9.9", url="u", assets={"catflap-9.9.flatpak": "https://dl/x.flatpak"})
    st = update.check({}, fetch=lambda: rel, install=update.InstallInfo("flatpak", "io.x.CatFlap", True))
    d = ui_update.UpdateDialog(st, settings={})
    d.COUNTDOWN = 0
    d.WATCH_MS = 10
    monkeypatch.setattr(update, "apply_update", lambda s: update.UpdateResult(
        False, "x", restart=True, downloaded=tmp_path / "x.flatpak", commands=["flatpak install …"]))
    versions = iter(["0.6", "9.9"])          # first check: not yet installed; then installed
    monkeypatch.setattr(update, "installed_version", lambda info: next(versions, "9.9"))
    d.do_update()
    wait_for(qapp, lambda: d.restart_requested, timeout=5)
    assert d.restart_requested


def test_update_dialog_auto_restart_countdown_can_be_cancelled(qapp, monkeypatch):
    from catflap import update, ui_update
    rel = update.ReleaseInfo(version="9.9", url="u", assets={})
    st = update.check({}, fetch=lambda: rel, install=update.InstallInfo("rpm", automatic=True))
    d = ui_update.UpdateDialog(st, settings={})
    monkeypatch.setattr(update, "apply_update", lambda s: update.UpdateResult(True, "installed", restart=True))
    d.do_update()
    assert "Restarting CatFlap in" in d.status.text()
    d.reject()                                 # "Don't restart now"
    qapp.processEvents()
    assert not d.restart_requested and not d._tick_timer.isActive()


def test_site_wizard_unifi_cloud_refused_falls_back_to_file(qapp, manager, no_popups, monkeypatch, unifi_conf):
    from catflap.drivers import unifi
    from catflap.drivers.unifi import ConnectorRefused
    from conftest import FakeDriver
    monkeypatch.setattr(unifi, "list_cloud_consoles", lambda key: CONSOLES[:1])
    def refuse(self, site, name=None):
        raise ConnectorRefused("only the official API (Ubiquiti said: HTTP 403: insufficient permissions)")
    monkeypatch.setattr(FakeDriver, "get_wireguard_server", refuse)
    monkeypatch.setattr(FakeDriver, "wireguard_servers_official",
                        lambda self, site: [{"name": "WG", "enabled": True}], raising=False)
    w = ui_wizards.SiteWizard(manager)
    w.show()
    pick(w, "unifi")
    w.currentPage().key.setText("K")
    w.next()                      # console page
    w.next()                      # site page
    w.next()                      # server page
    server = w.currentPage()
    wait_for(qapp, lambda: not server.file_btn.isHidden())
    assert "official API" in server.text.toPlainText()
    server.file_btn.click()
    assert w.currentId() == ui_wizards.P_FILE
    w.currentPage().path.setText(str(unifi_conf))
    w.next()
    assert w.currentId() == ui_wizards.P_NETWORKS          # networks still come from the API
    assert w.currentPage().chosen() == ["192.168.1.0/24"]
    w.next()
    w.currentPage().name.setText("HQ")
    w.next()
    assert w.currentId() == ui_wizards.P_DONE, no_popups
    p = w.result.profile
    assert p.vendor == "unifi" and p.routes == ["192.168.1.0/24"] and p.peer_public_key


def test_color_picker(qapp, monkeypatch):
    from catflap.model import PALETTE
    from catflap.ui_common import ColorPicker
    from PySide6 import QtGui
    cp = ColorPicker()
    assert len(cp.group.buttons()) == len(PALETTE) == 24
    cp.first_unused({PALETTE[0], PALETTE[1]})
    assert cp.color() == PALETTE[2]
    monkeypatch.setattr(QtWidgets.QColorDialog, "getColor", lambda *a, **k: QtGui.QColor("#123456"))
    cp._pick_custom()
    assert cp.color() == "#123456"
    cp.set_color(PALETTE[5])
    assert cp.color() == PALETTE[5]


def test_tray_menu_status_is_short():
    from catflap.platform.base import TunnelStatus
    from catflap.ui_common import INFO, short_status
    up = TunnelStatus(active=True)
    assert short_status(up, INFO + "Your current network uses the same addresses as X; …") == "Connected (workaround)"
    assert short_status(up, None) == "Connected"
    assert short_status(None, INFO + "You're at X right now …") == "You're here"
    assert short_status(None, "Site network … a very long error message …") == "Couldn't connect"
    assert short_status(None, None) == "Not connected"


def test_offer_wireguard_install(qapp, monkeypatch, no_popups):
    from catflap.platform.windows import WireGuardMissing
    from catflap.ui_common import offer_wireguard_install
    installed = []

    class B:
        def install_wireguard(self):
            installed.append(True)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    assert offer_wireguard_install(None, WireGuardMissing("missing"), B())
    assert installed == [True]
    assert not offer_wireguard_install(None, RuntimeError("other"), B())     # other errors: no offer


def _pia(version="3.6"):
    from catflap import conflicts
    return conflicts.match_programs([{"name": "Private Internet Access", "version": version,
                                      "uninstall": "pia-uninstall.exe", "per_user": False}])


def _capture(app, monkeypatch, choose=None):
    seen = []

    def fake_exec(dlg):
        seen.append([f.key for f, *_ in dlg.rows])
        if choose:
            choose(dlg)
        return 1
    monkeypatch.setattr(app, "run_dialog", fake_exec)
    return seen


def test_scans_after_each_update_and_respects_dont_warn_per_version(qapp, manager, monkeypatch):
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    seen = _capture(app, monkeypatch, lambda d: d.set_choice("pia", quiet=True))
    app.check_conflicting_apps(found=_pia())
    assert seen == [["pia"]] and app.settings["conflicts_ignored"] == {"pia": "3.6"}
    app.check_conflicting_apps()                          # same CatFlap version: no new scan
    assert len(seen) == 1
    app.settings["conflicts_scanned_version"] = "1.0"     # CatFlap was updated
    monkeypatch.setattr("catflap.conflicts.detect", lambda b, p: _pia())
    app.check_conflicting_apps()
    assert len(seen) == 1                                 # silenced at this version
    app.settings["conflicts_scanned_version"] = "1.0"
    monkeypatch.setattr("catflap.conflicts.detect", lambda b, p: _pia("3.7"))
    app.check_conflicting_apps()
    assert seen[-1] == ["pia"]                            # newer PIA: warned again


def test_manual_check_lists_everything_even_silenced(qapp, manager, monkeypatch):
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    app.settings["conflicts_ignored"] = {"pia": "3.6"}
    seen = _capture(app, monkeypatch)
    monkeypatch.setattr(tray, "run_blocking", lambda parent, text, fn: fn())
    monkeypatch.setattr("catflap.conflicts.detect", lambda b, p: _pia())
    app.check_conflicting_apps(manual=True)
    assert seen == [["pia"]]
    assert app.settings["conflicts_ignored"] == {"pia": "3.6"}   # box was pre-ticked, kept


def test_uninstall_checkbox_runs_the_uninstaller(qapp, manager, monkeypatch):
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    ran = []
    monkeypatch.setattr(tray, "run_blocking", lambda parent, text, fn: fn())
    monkeypatch.setattr("catflap.platform.windows.uninstall_program", lambda info, run: ran.append(info["name"]))
    _capture(app, monkeypatch, lambda d: d.set_choice("pia", uninstall=True))
    app.check_conflicting_apps(found=_pia())
    assert ran == ["Private Internet Access"]


def test_old_dont_ask_settings_are_kept(qapp, manager, monkeypatch):
    from catflap import conflicts
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    app.settings["wifiman_prompt"] = "never"
    app.settings["conflicts_ignored"] = ["pia"]
    seen = _capture(app, monkeypatch)
    wifiman = conflicts.match_programs([{"name": "WiFiman Desktop", "version": "1.2.8", "uninstall": "u"}])
    app.check_conflicting_apps(found=wifiman + _pia())
    assert not seen                                       # both silenced before versions were kept
    assert app.settings["conflicts_ignored"] == {"pia": "3.6", "wifiman": "1.2.8"}


def test_wireguard_app_row_offers_hiding_the_tray_icon(qapp):
    from catflap import conflicts
    from catflap.ui_conflicts import ConflictsDialog
    found = conflicts.match_programs([{"name": "WireGuard", "version": "1.1.1", "uninstall": "MsiExec /X{X}"}])
    assert found[0].conflict is conflicts.WIREGUARD_APP
    d = ConflictsDialog(found, {}, manual=True, windows=True)
    d.set_choice("wireguard-app", hide=True)
    assert [f.key for f in d.hide_tray()] == ["wireguard-app"] and not d.uninstall()
    d.set_choice("wireguard-app", uninstall=True)        # uninstall unticks hide
    assert not d.hide_tray() and [f.key for f in d.uninstall()] == ["wireguard-app"]
    assert d.ok.text() == "Uninstall 1 and close"
