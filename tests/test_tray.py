"""Headless smoke test of the tray app (skipped when PySide6 isn't installed)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from catflap import tray  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_icon_states(qapp):
    off = tray.make_icon([]).pixmap(32, 32).toImage()
    on = tray.make_icon(["#ff0000"]).pixmap(32, 32).toImage()
    assert off != on
    centre = on.pixelColor(16, 22)
    assert centre.red() > 200 and centre.green() < 60      # flap filled with the site colour


def test_tray_menu_and_window(qapp, manager, backend):
    manager.provision("home-gw", "home", ["LAN"], device="d", color="#ff0000")
    app = tray.TrayApp(qapp, manager)
    app.timer.stop()
    app.profiles = manager.store.profiles()
    app.status = manager.status()
    app._render()
    labels = [a.text() for a in app.menu.actions()]
    assert any(t.startswith("home — disconnected") for t in labels)
    assert "Open catflap…" in labels and "Quit" in labels
    backend.active.add("home")
    app.status = manager.status()
    app._render()
    assert "home connected" in app.tray.toolTip()
    app.show_window()
    assert app.window.table.rowCount() == 1
    app.settings["close_behavior"] = "tray"
    app.window.close()
    assert not app.window.isVisible() and not app.quitting   # closed to the tray
