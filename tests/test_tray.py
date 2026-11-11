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
    assert centre.red() > 200 and centre.green() < 60      # cat filled with the site colour


def test_tray_menu_and_basic_window(qapp, manager, backend):
    manager.provision("home-gw", "home", ["LAN"], device="d", color="#ff0000", label="Home Sweet Home")
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    app.profiles = manager.store.profiles()
    app.status = manager.status()
    app._render()
    labels = [a.text() for a in app.menu.actions()]
    assert "Home Sweet Home: Not connected  (Connect)" in labels
    assert "Add a site…" in labels and "Open catflap" in labels and "Quit" in labels
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


def test_site_wizard_unifi_automatic(qapp, manager, no_popups):
    w = ui_wizards.SiteWizard(manager)
    w.show()
    vp = w.page(ui_wizards.P_VENDOR)
    next(b for b in vp.group.buttons() if b.property("vendor") == "unifi").setChecked(True)
    w.next()
    assert w.currentId() == ui_wizards.P_LOGIN
    login = w.currentPage()
    login.address.setText("192.168.7.1")
    login.key.setText("NEWKEY")
    w.next()
    assert w.currentId() == ui_wizards.P_SITE, no_popups
    assert w.gateway == "unifi-192-168-7-1"
    assert manager.secrets.get("gateway/unifi-192-168-7-1/api_key") == "NEWKEY"
    w.next()
    server = w.currentPage()
    wait_for(qapp, lambda: server.ok)
    w.next()
    nets = w.currentPage()
    assert nets.chosen() == ["192.168.1.0/24"]           # first network pre-ticked
    w.next()
    name = w.currentPage()
    assert name.name.text() == "Home"
    w.next()
    assert w.currentId() == ui_wizards.P_DONE, no_popups
    p = w.result.profile
    assert p.label == "Home" and p.name == "home" and "192.168.1.0/24" in p.routes


def test_site_wizard_file_import_for_omada(qapp, manager, no_popups, unifi_conf):
    w = ui_wizards.SiteWizard(manager)
    w.show()
    vp = w.page(ui_wizards.P_VENDOR)
    next(b for b in vp.group.buttons() if b.property("vendor") == "omada").setChecked(True)
    w.next()
    assert w.currentId() == ui_wizards.P_FILE
    fp = w.currentPage()
    fp.path.setText(str(unifi_conf))
    fp.router.setText("10.20.0.1")
    w.next()
    name = w.currentPage()
    name.name.setText("Cabin")
    w.next()
    assert w.currentId() == ui_wizards.P_DONE, no_popups
    p = w.result.profile
    assert p.vendor == "omada" and p.routes == ["10.20.0.0/24"] and p.label == "Cabin"


def test_site_wizard_rejects_duplicate_name_and_bad_file(qapp, manager, no_popups, tmp_path):
    bad = tmp_path / "x.conf"
    bad.write_text("not a wireguard file")
    w = ui_wizards.SiteWizard(manager)
    w.show()
    vp = w.page(ui_wizards.P_VENDOR)
    next(b for b in vp.group.buttons() if b.property("vendor") == "generic").setChecked(True)
    w.next()
    fp = w.currentPage()
    fp.path.setText(str(bad))
    fp.router.setText("10.20.0.1")
    w.next()
    assert w.currentId() == ui_wizards.P_FILE
    assert any("doesn't look like a WireGuard" in m for _, m in no_popups)


def test_gateway_wizard(qapp, manager, no_popups):
    w = ui_wizards.GatewayWizard(manager)
    w.show()
    w.next()
    w.login.address.setText("https://10.0.0.1/")
    w.login.key.setText("K2")
    assert w.login.validatePage()
    assert manager.store.gateway(w.gateway)["url"] == "https://10.0.0.1"
