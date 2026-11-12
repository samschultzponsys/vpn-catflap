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
    off = make_icon([]).pixmap(32, 32).toImage()
    on = make_icon(["#ff0000"]).pixmap(32, 32).toImage()
    assert off != on
    centre = on.pixelColor(16, 22)
    assert centre.red() > 200 and centre.green() < 60      # cat filled with the site color


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
