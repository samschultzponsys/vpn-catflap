"""catflap tray app (Qt / PySide6).

KDE Plasma on Wayland shows QSystemTrayIcon through StatusNotifierItem;
Windows uses the notification area. The app:

* lives in the tray; the window minimizes to the tray, and closing it asks
  whether to keep running in the tray or quit (with "remember my choice");
* the tray icon is grey/outlined when nothing is connected, and filled with
  each connected site's own colour (split into segments if several are up),
  with a red badge when the last attempt failed;
* groups sites by gateway, click to connect/disconnect, shows notifications;
* checks for updates once a day and shows how to update for the way catflap
  was installed.

All real work happens in :class:`catflap.core.Manager` on a worker thread.
"""

from __future__ import annotations

import sys
import time
import traceback
from typing import Any, Callable

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
except ImportError:  # pragma: no cover - depends on the platform packages
    QtCore = QtGui = QtWidgets = None  # type: ignore

from . import autostart, update
from .core import Manager
from .drivers import DRIVERS
from .model import Profile
from .store import Store
from .version import __version__

GREY = "#8A8F98"
RED = "#D92D20"


def require_qt() -> None:
    if QtWidgets is None:
        raise SystemExit("The tray app needs PySide6 (Fedora: dnf install python3-pyside6; "
                         "Debian: apt install python3-pyside6.qtwidgets; pip: PySide6-Essentials)")


# ============================================================ icon
def _cat_path(s: float) -> "QtGui.QPainterPath":
    """Cat head (same shape as the app icon), in 64x64 units scaled by ``s``."""
    pts = [(19, 54), (19, 37), (21, 31), (22, 22), (28, 28), (32, 27), (36, 28), (42, 22),
           (43, 31), (45, 37), (45, 54)]
    path = QtGui.QPainterPath()
    path.moveTo(pts[0][0] * s, pts[0][1] * s)
    for x, y in pts[1:]:
        path.lineTo(x * s, y * s)
    path.closeSubpath()
    return path


def make_icon(colors: list[str], error: bool = False, size: int = 64) -> "QtGui.QIcon":
    """Tray icon: the app icon's cat in its cat flap.

    Disconnected: grey frame, cat drawn as an outline only. Connected: the cat
    is filled with the connection colour (split into stripes when several sites
    are up). Error: red badge in the corner.
    """
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    s = size / 64.0
    door = QtCore.QRectF(8 * s, 9 * s, 48 * s, 47 * s)
    # Frame in the connection colour so it reads on light and dark panels.
    frame = QtGui.QColor(GREY if not colors else colors[0] if len(colors) == 1 else "#64748B")
    clip = QtGui.QPainterPath()
    clip.addRoundedRect(door, 10 * s, 10 * s)
    cat = _cat_path(s)
    if colors:
        p.save()
        p.setClipPath(clip.intersected(cat))
        bounds = cat.boundingRect()
        n = len(colors)
        for i, c in enumerate(colors):
            seg = QtCore.QRectF(bounds.x() + bounds.width() * i / n, bounds.y(),
                                bounds.width() / n + 1, bounds.height())
            p.fillRect(seg, QtGui.QColor(c))
        p.restore()
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor("#0F172A"))
        for ex in (26, 38):     # eyes
            p.drawEllipse(QtCore.QPointF(ex * s, 41 * s), 2.6 * s, 3.4 * s)
    else:
        p.save()
        p.setClipPath(clip)
        p.setPen(QtGui.QPen(QtGui.QColor(GREY), 3.2 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(cat)
        p.restore()
    p.setPen(QtGui.QPen(frame, 4.5 * s))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(door, 10 * s, 10 * s)
    p.setPen(QtGui.QPen(frame, 3.5 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QtCore.QPointF(14 * s, 17 * s), QtCore.QPointF(50 * s, 17 * s))
    if error:
        p.setPen(QtGui.QPen(QtGui.QColor("white"), 2 * s))
        p.setBrush(QtGui.QColor(RED))
        p.drawEllipse(QtCore.QRectF(41 * s, 41 * s, 21 * s, 21 * s))
    p.end()
    return QtGui.QIcon(pm)


def dot_icon(color: str, filled: bool) -> "QtGui.QIcon":
    pm = QtGui.QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtGui.QPen(QtGui.QColor(color), 2))
    p.setBrush(QtGui.QColor(color) if filled else Qt.NoBrush)
    p.drawEllipse(2, 2, 12, 12)
    p.end()
    return QtGui.QIcon(pm)


# ============================================================ background work
if QtCore is not None:
    class _Signals(QtCore.QObject):
        done = QtCore.Signal(object, object)

    class _Job(QtCore.QRunnable):
        def __init__(self, fn: Callable[[], Any], cb: Callable[[Any, Any], None]):
            super().__init__()
            # Python owns the job: Qt must not delete it (and its signal object)
            # while the result is still queued for the UI thread.
            self.setAutoDelete(False)
            self.fn, self.cb, self.sig = fn, cb, _Signals()
            self.sig.done.connect(self._finish, QtCore.Qt.QueuedConnection)

        def run(self) -> None:
            try:
                result, err = self.fn(), None
            except Exception as e:  # noqa: BLE001 - reported to the UI
                traceback.print_exc()
                result, err = None, e
            self.sig.done.emit(result, err)

        def _finish(self, result: Any, err: Any) -> None:
            try:
                self.cb(result, err)
            finally:
                _JOBS.discard(self)

    _JOBS: set = set()


def run_bg(fn: Callable[[], Any], cb: Callable[[Any, Any], None]) -> None:
    job = _Job(fn, cb)
    _JOBS.add(job)
    QtCore.QThreadPool.globalInstance().start(job)


# ============================================================ dialogs
class AddGatewayDialog(QtWidgets.QDialog if QtWidgets else object):
    def __init__(self, parent, manager: Manager):
        super().__init__(parent)
        self.m = manager
        self.setWindowTitle("Add gateway")
        f = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit()
        self.vendor = QtWidgets.QComboBox()
        for k, d in DRIVERS.items():
            self.vendor.addItem(d.display_name, k)
        self.url = QtWidgets.QLineEdit()
        self.url.setPlaceholderText("https://192.168.1.1")
        self.key = QtWidgets.QLineEdit()
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.user = QtWidgets.QLineEdit()
        self.user.setPlaceholderText("optional local admin (UniFi classic API)")
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
        for label, w in (("Name", self.name), ("Vendor", self.vendor), ("URL", self.url),
                         ("API key", self.key), ("Username", self.user), ("Password", self.pw)):
            f.addRow(label, w)
        f.addRow(QtWidgets.QLabel("Credentials are stored only in this PC's keyring."))
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def save(self) -> None:
        from .http import fetch_fingerprint
        fp = None
        url = self.url.text().strip()
        try:
            if url.startswith("https://"):
                fp = fetch_fingerprint(url)
                if QtWidgets.QMessageBox.question(
                        self, "Trust certificate?",
                        f"The gateway's certificate fingerprint is:\n\n{fp}\n\nCompare it with the "
                        "one your browser shows for the gateway. Trust it?") != QtWidgets.QMessageBox.Yes:
                    return
            self.m.add_gateway(self.name.text().strip(), self.vendor.currentData(), url,
                               api_key=self.key.text().strip() or None,
                               username=self.user.text().strip() or None,
                               password=self.pw.text() or None, fingerprint=fp)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "catflap", str(e))
            return
        self.accept()


class AddSiteDialog(QtWidgets.QDialog if QtWidgets else object):
    """Provision from a gateway (pick networks) or import a downloaded .conf."""

    def __init__(self, parent, manager: Manager):
        super().__init__(parent)
        self.m = manager
        self.setWindowTitle("Add site")
        self.resize(520, 480)
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        lay.addWidget(tabs)

        # -- from gateway
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self.gw = QtWidgets.QComboBox()
        self.gw.addItems([n for n, g in manager.store.gateways().items() if g["vendor"] != "generic"])
        self.site = QtWidgets.QComboBox()
        self.nets = QtWidgets.QListWidget()
        self.name = QtWidgets.QLineEdit()
        self.endpoint = QtWidgets.QLineEdit()
        self.endpoint.setPlaceholderText("DDNS hostname (optional; default: gateway's DDNS/WAN IP)")
        load = QtWidgets.QPushButton("Load sites/networks")
        load.clicked.connect(self.load_sites)
        self.site.currentIndexChanged.connect(self.load_networks)
        for label, x in (("Gateway", self.gw), ("", load), ("Site", self.site),
                         ("Route these networks", self.nets), ("Profile name", self.name),
                         ("Endpoint", self.endpoint)):
            f.addRow(label, x)
        tabs.addTab(w, "From gateway")

        # -- from file
        w2 = QtWidgets.QWidget()
        f2 = QtWidgets.QFormLayout(w2)
        self.file = QtWidgets.QLineEdit()
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(lambda: self.file.setText(
            QtWidgets.QFileDialog.getOpenFileName(self, "WireGuard config", "", "WireGuard (*.conf)")[0]))
        self.fvendor = QtWidgets.QComboBox()
        for k, d in DRIVERS.items():
            self.fvendor.addItem(d.display_name, k)
        self.fname = QtWidgets.QLineEdit()
        self.routes = QtWidgets.QLineEdit()
        self.routes.setPlaceholderText("e.g. 192.168.10.0/24, 192.168.20.0/24")
        self.fendpoint = QtWidgets.QLineEdit()
        for label, x in (("File", self.file), ("", browse), ("Vendor", self.fvendor),
                         ("Profile name", self.fname), ("Route these networks", self.routes),
                         ("Endpoint (DDNS)", self.fendpoint)):
            f2.addRow(label, x)
        tabs.addTab(w2, "From .conf file")
        self.tabs = tabs

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._sites: list = []

    def load_sites(self) -> None:
        gw = self.gw.currentText()
        if not gw:
            return
        run_bg(lambda: self.m.list_sites(gw), self._got_sites)

    def _got_sites(self, sites, err) -> None:
        if err:
            QtWidgets.QMessageBox.warning(self, "catflap", str(err))
            return
        self._sites = sites
        self.site.clear()
        self.site.addItems([s.name for s in sites])

    def load_networks(self) -> None:
        if not self._sites or self.site.currentIndex() < 0:
            return
        gw, sid = self.gw.currentText(), self._sites[self.site.currentIndex()].id
        run_bg(lambda: self.m.list_networks(gw, sid)[1], self._got_nets)

    def _got_nets(self, nets, err) -> None:
        if err:
            QtWidgets.QMessageBox.warning(self, "catflap", str(err))
            return
        self.nets.clear()
        for n in nets:
            it = QtWidgets.QListWidgetItem(f"{n.name}  {n.cidr}" + (f"  VLAN {n.vlan}" if n.vlan else ""))
            it.setData(Qt.UserRole, n.cidr)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.nets.addItem(it)

    def save(self) -> None:
        if self.tabs.currentIndex() == 0:
            cidrs = [self.nets.item(i).data(Qt.UserRole) for i in range(self.nets.count())
                     if self.nets.item(i).checkState() == Qt.Checked]
            site = self._sites[self.site.currentIndex()].id if self._sites else None
            args = (self.gw.currentText(), self.name.text().strip(), cidrs)
            fn = lambda: self.m.provision(*args, site=site, endpoint=self.endpoint.text().strip() or None)  # noqa: E731
        else:
            routes = [r.strip() for r in self.routes.text().split(",") if r.strip()]
            fn = lambda: self.m.import_conf(self.file.text(), self.fname.text().strip(),  # noqa: E731
                                            vendor=self.fvendor.currentData(), routes=routes or None,
                                            endpoint=self.fendpoint.text().strip() or None)
        self.setEnabled(False)
        run_bg(fn, self._saved)

    def _saved(self, res, err) -> None:
        self.setEnabled(True)
        if err:
            QtWidgets.QMessageBox.warning(self, "catflap", str(err))
            return
        msgs = "\n".join(f"• {x}" for x in res.messages)
        QtWidgets.QMessageBox.information(self, "catflap", f"Site '{res.profile.name}' added.\n\n{msgs}")
        self.accept()


# ============================================================ main window
class MainWindow(QtWidgets.QMainWindow if QtWidgets else object):
    COLS = ["", "Site", "Gateway", "Status", "Routes"]

    def __init__(self, app: "TrayApp"):
        super().__init__()
        self.app = app
        self.setWindowTitle(f"catflap {__version__}")
        self.resize(760, 420)
        central = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(central)
        self.table = QtWidgets.QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _: self.toggle_selected())
        lay.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("Connect / Disconnect", self.toggle_selected), ("Colour…", self.pick_color),
                         ("Add site…", self.add_site), ("Add gateway…", self.add_gateway),
                         ("Remove site", self.remove_selected)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.setCentralWidget(central)

        mb = self.menuBar()
        fm = mb.addMenu("&File")
        fm.addAction("Export everything…", self.export)
        fm.addAction("Import export file…", self.import_bundle)
        fm.addSeparator()
        fm.addAction("Hide to tray", self.hide)
        fm.addAction("Quit", self.app.quit)
        sm = mb.addMenu("&Settings")
        self.a_autostart = sm.addAction("Start at login")
        self.a_autostart.setCheckable(True)
        self.a_autostart.setChecked(autostart.is_enabled())
        self.a_autostart.toggled.connect(self.set_autostart)
        self.a_updates = sm.addAction("Check for updates automatically")
        self.a_updates.setCheckable(True)
        self.a_updates.setChecked(self.app.settings.get("update_check", True))
        self.a_updates.toggled.connect(lambda v: self.app.set_setting("update_check", v))
        sm.addAction("Reset close-button choice", lambda: self.app.set_setting("close_behavior", "ask"))
        hm = mb.addMenu("&Help")
        hm.addAction("Check for updates…", lambda: self.app.check_updates(interactive=True))
        hm.addAction("About catflap", self.about)

    # -- table
    def refresh(self, profiles: dict[str, Profile], status: dict[str, Any], errors: dict[str, str]) -> None:
        sel = self.selected()
        self.table.setRowCount(0)
        for name, p in sorted(profiles.items(), key=lambda kv: (kv[1].gateway or "", kv[0])):
            st = status.get(name)
            active = bool(st and st.active)
            r = self.table.rowCount()
            self.table.insertRow(r)
            sw = QtWidgets.QTableWidgetItem()
            sw.setIcon(dot_icon(p.color or GREY, active))
            self.table.setItem(r, 0, sw)
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(name))
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{p.gateway or 'imported'} ({p.vendor})"))
            self.table.setItem(r, 3, QtWidgets.QTableWidgetItem(self.app.status_text(p, st, errors.get(name))))
            self.table.setItem(r, 4, QtWidgets.QTableWidgetItem(", ".join(p.routes)))
            if name == sel:
                self.table.selectRow(r)
        self.table.resizeColumnsToContents()

    def selected(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.table.item(rows[0].row(), 1).text() if rows else None

    def toggle_selected(self) -> None:
        n = self.selected()
        if n:
            self.app.toggle(n)

    def pick_color(self) -> None:
        n = self.selected()
        if not n:
            return
        p = self.app.m.store.profile(n)
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(p.color or GREY), self, f"Colour for {n}")
        if c.isValid():
            self.app.m.update_profile(n, color=c.name())
            self.app.poll()

    def remove_selected(self) -> None:
        n = self.selected()
        if not n:
            return
        p = self.app.m.store.profile(n)
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Remove site")
        box.setText(f"Remove '{n}' from this PC?")
        revoke = None
        if p.gateway and p.client_id:
            revoke = box.addButton("Remove and revoke on gateway", QtWidgets.QMessageBox.DestructiveRole)
        local = box.addButton("Remove from this PC only", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (revoke, local):
            return
        do_revoke = box.clickedButton() is revoke
        run_bg(lambda: self.app.m.remove(n, revoke=do_revoke), lambda r, e: self._done(r, e))

    def _done(self, res, err) -> None:
        if err:
            QtWidgets.QMessageBox.warning(self, "catflap", str(err))
        self.app.poll()

    def add_site(self) -> None:
        if AddSiteDialog(self, self.app.m).exec():
            self.app.poll()

    def add_gateway(self) -> None:
        AddGatewayDialog(self, self.app.m).exec()

    def set_autostart(self, v: bool) -> None:
        try:
            autostart.set_enabled(v)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "catflap", str(e))

    def _ask_pass(self, title: str, confirm: bool) -> str | None:
        pw, ok = QtWidgets.QInputDialog.getText(self, title, "Passphrase (min. 8 characters):",
                                                QtWidgets.QLineEdit.Password)
        if not ok:
            return None
        if confirm:
            again, ok = QtWidgets.QInputDialog.getText(self, title, "Repeat passphrase:",
                                                       QtWidgets.QLineEdit.Password)
            if not ok or again != pw:
                QtWidgets.QMessageBox.warning(self, "catflap", "Passphrases do not match.")
                return None
        return pw

    def export(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export catflap", "catflap-export.json")
        if not path:
            return
        pw = self._ask_pass("Encrypt export", confirm=True)
        if pw is None:
            return
        try:
            import os
            text = self.app.m.export(pw)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text)
            QtWidgets.QMessageBox.information(self, "catflap", f"Exported (encrypted) to {path}.")
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "catflap", str(e))

    def import_bundle(self) -> None:
        from .bundle import is_encrypted
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import catflap export", "", "catflap export (*.json)")
        if not path:
            return
        text = open(path, encoding="utf-8").read()
        pw = self._ask_pass("Decrypt export", confirm=False) if is_encrypted(text) else None
        run_bg(lambda: self.app.m.import_bundle(text, pw),
               lambda r, e: (QtWidgets.QMessageBox.warning(self, "catflap", str(e)) if e else
                             QtWidgets.QMessageBox.information(self, "catflap", "\n".join(r)),
                             self.app.poll()))

    def about(self) -> None:
        info = update.detect_install()
        QtWidgets.QMessageBox.about(
            self, "About catflap",
            f"<b>catflap {__version__}</b><br>Safe WireGuard remote access to your sites.<br><br>"
            f"Installed via: <b>{info.method}</b> {info.detail}<br>"
            f"Licensed under the Apache License 2.0; see NOTICE.<br>"
            f"<a href='https://github.com/{update.REPO}'>github.com/{update.REPO}</a>")

    def closeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        if self.app.quitting:
            ev.accept()
            return
        behavior = self.app.settings.get("close_behavior", "ask")
        if behavior == "ask":
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Close catflap?")
            box.setText("Keep catflap running in the system tray, or quit completely?\n\n"
                        "Connected tunnels stay up either way.")
            tray_btn = box.addButton("Keep running in tray", QtWidgets.QMessageBox.AcceptRole)
            quit_btn = box.addButton("Quit catflap", QtWidgets.QMessageBox.DestructiveRole)
            box.addButton(QtWidgets.QMessageBox.Cancel)
            remember = QtWidgets.QCheckBox("Remember my choice")
            box.setCheckBox(remember)
            box.setDefaultButton(tray_btn)
            box.exec()
            if box.clickedButton() is tray_btn:
                behavior = "tray"
            elif box.clickedButton() is quit_btn:
                behavior = "quit"
            else:
                ev.ignore()
                return
            if remember.isChecked():
                self.app.set_setting("close_behavior", behavior)
        if behavior == "quit":
            ev.accept()
            self.app.quit()
        else:
            ev.ignore()
            self.hide()

    def changeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        # Minimizing sends the window to the tray instead of the task bar.
        if ev.type() == QtCore.QEvent.WindowStateChange and self.isMinimized():
            QtCore.QTimer.singleShot(0, self.hide)
        super().changeEvent(ev)


# ============================================================ tray app
class TrayApp(QtCore.QObject if QtCore else object):
    POLL_MS = 5000

    def __init__(self, qapp: "QtWidgets.QApplication", manager: Manager | None = None):
        super().__init__()
        self.qapp = qapp
        self.m = manager or Manager()
        self.settings = self.m.store.settings()
        self.quitting = False
        self.profiles: dict[str, Profile] = {}
        self.status: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self.busy: set[str] = set()
        self.update_status: update.UpdateStatus | None = None

        self.tray = QtWidgets.QSystemTrayIcon(make_icon([]), self)
        self.tray.setToolTip("catflap: not connected")
        self.menu = QtWidgets.QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.window = MainWindow(self)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(self.POLL_MS)
        self.poll()
        if self.settings.get("update_check", True):
            QtCore.QTimer.singleShot(3000, lambda: self.check_updates(interactive=False))

    # -- settings
    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        self.m.store.save()

    # -- status
    @staticmethod
    def status_text(p: Profile, st: Any, err: str | None) -> str:
        if st is not None and st.active:
            if st.last_handshake:
                return f"connected (handshake {int(time.time() - st.last_handshake)}s ago)"
            return "connected"
        if err:
            return f"error: {err.splitlines()[0][:80]}"
        return "disconnected"

    def poll(self) -> None:
        root = self.m.store.root

        def work():
            store = Store(root)  # fresh read: the CLI may have changed things
            profiles = store.profiles()
            st = {}
            for n, p in profiles.items():
                try:
                    st[n] = self.m.backend.status(p)
                except Exception:  # noqa: BLE001
                    st[n] = None
            return store, profiles, st
        run_bg(work, self._polled)

    def _polled(self, res, err) -> None:
        if err or res is None:
            return
        store, self.profiles, self.status = res
        self.m.store = store
        self.settings = store.settings()
        self._render()

    def _render(self) -> None:
        active = [n for n, s in self.status.items() if s and s.active]
        colors = [self.profiles[n].color or "#2E9E44" for n in sorted(active)]
        self.tray.setIcon(make_icon(colors, error=bool(self.errors) and not active))
        self.tray.setToolTip("catflap: " + (", ".join(sorted(active)) + " connected" if active
                                             else "not connected"))
        self._build_menu()
        if self.window.isVisible():
            self.window.refresh(self.profiles, self.status, self.errors)

    def _build_menu(self) -> None:
        m = self.menu
        m.clear()
        groups: dict[str, list[Profile]] = {}
        for p in self.profiles.values():
            groups.setdefault(f"{p.gateway or 'Imported'} · {DRIVERS[p.vendor].display_name if p.vendor in DRIVERS else p.vendor}", []).append(p)
        if not groups:
            m.addAction("No sites yet — open catflap to add one").setEnabled(False)
        for g in sorted(groups):
            m.addSection(g)
            for p in sorted(groups[g], key=lambda x: x.name):
                st = self.status.get(p.name)
                active = bool(st and st.active)
                label = f"{p.name} — " + ("working…" if p.name in self.busy else
                                          self.status_text(p, st, self.errors.get(p.name)))
                a = m.addAction(dot_icon(p.color or GREY, active), label)
                a.setEnabled(p.name not in self.busy)
                a.triggered.connect(lambda _=False, n=p.name: self.toggle(n))
        m.addSeparator()
        if self.update_status and self.update_status.available:
            m.addAction(f"Update to {self.update_status.latest.version} available…",
                        lambda: self.check_updates(interactive=True))
        m.addAction("Open catflap…", self.show_window)
        m.addAction("Quit", self.quit)

    # -- actions
    def _activated(self, reason) -> None:
        if reason in (QtWidgets.QSystemTrayIcon.Trigger, QtWidgets.QSystemTrayIcon.DoubleClick):
            if self.window.isVisible():
                self.window.hide()
            else:
                self.show_window()

    def show_window(self) -> None:
        self.window.refresh(self.profiles, self.status, self.errors)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def toggle(self, name: str) -> None:
        if name in self.busy:
            return
        st = self.status.get(name)
        self.busy.add(name)
        self._render()
        if st and st.active:
            run_bg(lambda: self.m.disconnect(name), lambda r, e: self._toggled(name, None, e, False))
        else:
            run_bg(lambda: self.m.connect(name), lambda r, e: self._toggled(name, r, e, True))

    def _toggled(self, name: str, res, err, connecting: bool) -> None:
        self.busy.discard(name)
        if err:
            self.errors[name] = str(err)
            self.tray.showMessage(f"catflap: {name}", str(err), QtWidgets.QSystemTrayIcon.Critical)
        elif connecting and res is not None and not res.ok:
            bad = [f.message for f in res.findings if f.level == "error"]
            self.errors[name] = bad[0] if bad else "failed"
            self.tray.showMessage(f"catflap: {name} not connected", "\n".join(bad)[:400],
                                  QtWidgets.QSystemTrayIcon.Warning)
        else:
            self.errors.pop(name, None)
            self.tray.showMessage("catflap", f"{name} {'connected' if connecting else 'disconnected'}",
                                  QtWidgets.QSystemTrayIcon.Information, 3000)
        self.poll()

    def check_updates(self, interactive: bool) -> None:
        run_bg(lambda: update.check(self.settings, force=interactive),
               lambda r, e: self._update_checked(r, e, interactive))

    def _update_checked(self, st, err, interactive: bool) -> None:
        self.m.store.save()
        self.update_status = st
        self._build_menu()
        if err or st is None:
            return
        if not st.available:
            if interactive:
                QtWidgets.QMessageBox.information(self.window, "catflap",
                                                  st.error or f"catflap {st.current} is up to date.")
            return
        if not interactive:
            self.tray.showMessage("catflap update", f"Version {st.latest.version} is available.",
                                  QtWidgets.QSystemTrayIcon.Information)
            return
        steps = "\n".join(st.instructions)
        text = (f"catflap {st.latest.version} is available (you have {st.current}).\n"
                f"Installed via: {st.install.method}\n\nTo update manually:\n{steps}")
        if st.install.automatic:
            if QtWidgets.QMessageBox.question(self.window, "Update catflap",
                                              text + "\n\nUpdate automatically now?") == QtWidgets.QMessageBox.Yes:
                run_bg(lambda: update.apply_update(st), self._updated)
        else:
            QtWidgets.QMessageBox.information(self.window, "Update catflap", text)

    def _updated(self, res, err) -> None:
        msg = str(err) if err else res[1]
        QtWidgets.QMessageBox.information(self.window, "catflap", msg)

    def quit(self) -> None:
        self.quitting = True
        self.tray.hide()
        self.qapp.quit()


def main(argv: list[str] | None = None) -> int:
    require_qt()
    argv = sys.argv[1:] if argv is None else argv
    QtWidgets.QApplication.setApplicationName("catflap")
    QtWidgets.QApplication.setDesktopFileName("catflap")
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        print("catflap: no system tray available; showing the window only", file=sys.stderr)
    qapp.setWindowIcon(make_icon(["#22C55E"]))
    app = TrayApp(qapp)
    # Quit cleanly on logout/SIGTERM/Ctrl+C: Python only handles signals while
    # it runs, so wake the interpreter regularly.
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    wake = QtCore.QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(500)
    app.tray.show()
    if "--minimized" not in argv:
        app.show_window()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
