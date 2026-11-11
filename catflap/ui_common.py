"""Shared Qt helpers for the catflap GUI: icons, background work, plain-language errors."""

from __future__ import annotations

import socket
import ssl
import traceback
from typing import Any, Callable

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
except ImportError:  # pragma: no cover - depends on the platform packages
    QtCore = QtGui = QtWidgets = Qt = None  # type: ignore

GREY = "#8A8F98"
RED = "#D92D20"
GREEN = "#16A34A"
PALETTE = ["#22C55E", "#3B82F6", "#F97316", "#A855F7", "#14B8A6", "#EAB308", "#EC4899", "#64748B"]


def require_qt() -> None:
    if QtWidgets is None:
        raise SystemExit("The catflap window needs PySide6 (Fedora: dnf install python3-pyside6; "
                         "Debian: apt install python3-pyside6.qtwidgets; pip: PySide6-Essentials)")


# ============================================================ icons
# The tray icon follows the app icon (catflap/resources/catflap.svg): the first icon's
# cat sitting in a tunnel arch. Coordinates are in a 64x64 box.
_CAT = [(19, 56), (19, 41.5), ("q", 19, 37.5, 21, 35.5), (22, 29), (28, 34),
        ("q", 32, 32.75, 36, 34), (42, 29), (43, 35.5), ("q", 45, 37.5, 45, 41.5), (45, 56)]


def _cat_path(s: float) -> "QtGui.QPainterPath":
    path = QtGui.QPainterPath()
    path.moveTo(_CAT[0][0] * s, _CAT[0][1] * s)
    for pt in _CAT[1:]:
        if pt[0] == "q":
            path.quadTo(pt[1] * s, pt[2] * s, pt[3] * s, pt[4] * s)
        else:
            path.lineTo(pt[0] * s, pt[1] * s)
    path.closeSubpath()
    return path


def _arch_path(s: float) -> "QtGui.QPainterPath":
    """Tunnel arch: up the left side, half circle over the top, down the right."""
    path = QtGui.QPainterPath()
    path.moveTo(11 * s, 58 * s)
    path.lineTo(11 * s, 31 * s)
    path.arcTo(QtCore.QRectF(11 * s, 10 * s, 42 * s, 42 * s), 180, -180)
    path.lineTo(53 * s, 58 * s)
    return path


def make_pixmap(colors: list[str], error: bool = False, size: int = 64) -> "QtGui.QPixmap":
    """The tray icon: the cat in its tunnel.

    Disconnected: grey arch, cat drawn as an outline only. Connected: arch and
    cat in the connection colour (cat striped when several sites are up).
    Error: red badge in the corner.
    """
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    s = size / 64.0
    # Arch in the connection colour so it reads on light and dark panels.
    arch = QtGui.QColor(GREY if not colors else colors[0] if len(colors) == 1 else "#64748B")
    cat = _cat_path(s)
    if colors:
        p.save()
        p.setClipPath(cat)
        bounds = cat.boundingRect()
        n = len(colors)
        for i, c in enumerate(colors):
            seg = QtCore.QRectF(bounds.x() + bounds.width() * i / n, bounds.y(),
                                bounds.width() / n + 1, bounds.height())
            p.fillRect(seg, QtGui.QColor(c))
        p.restore()
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor("#0F172A"))
        for ex in (27, 37):     # eyes
            p.drawEllipse(QtCore.QPointF(ex * s, 43 * s), 2.3 * s, 2.9 * s)
    else:
        p.setPen(QtGui.QPen(QtGui.QColor(GREY), 3 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(cat)
    p.setPen(QtGui.QPen(arch, 5 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(_arch_path(s))
    if error:
        p.setPen(QtGui.QPen(QtGui.QColor("white"), 2 * s))
        p.setBrush(QtGui.QColor(RED))
        p.drawEllipse(QtCore.QRectF(41 * s, 41 * s, 21 * s, 21 * s))
    p.end()
    return pm


ICON_FILE = __import__("pathlib").Path(__file__).with_name("resources") / "catflap.svg"


def app_icon() -> "QtGui.QIcon":
    """The app icon, loaded from the SVG shipped inside the package.

    Set directly on every window so KDE's title bar shows the current icon even
    when the desktop's icon cache still holds an old one. Falls back to the icon
    theme, then to the drawn tray-style icon.
    """
    import os
    icon = QtGui.QIcon(str(ICON_FILE))
    if not icon.isNull() and not icon.pixmap(64, 64).isNull():
        return icon
    for name in (os.environ.get("FLATPAK_ID", ""), "catflap"):
        if name and QtGui.QIcon.hasThemeIcon(name):
            return QtGui.QIcon.fromTheme(name)
    return QtGui.QIcon(make_pixmap(["#3B82F6"], size=128))


def make_icon(colors: list[str], error: bool = False, size: int = 64) -> "QtGui.QIcon":
    return QtGui.QIcon(make_pixmap(colors, error, size))


def dot_pixmap(color: str, filled: bool, size: int = 16) -> "QtGui.QPixmap":
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    w = max(2, size // 8)
    p.setPen(QtGui.QPen(QtGui.QColor(color), w))
    p.setBrush(QtGui.QColor(color) if filled else Qt.NoBrush)
    p.drawEllipse(QtCore.QRectF(w, w, size - 2 * w, size - 2 * w))
    p.end()
    return pm


def dot_icon(color: str, filled: bool) -> "QtGui.QIcon":
    return QtGui.QIcon(dot_pixmap(color, filled))


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
    """Run ``fn`` on a worker thread; call ``cb(result, error)`` on the UI thread."""
    job = _Job(fn, cb)
    _JOBS.add(job)
    QtCore.QThreadPool.globalInstance().start(job)


def run_blocking(parent, text: str, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` in the background behind a "please wait" dialog; return its
    result or raise its exception. The UI keeps repainting meanwhile."""
    dlg = QtWidgets.QProgressDialog(text, None, 0, 0, parent)
    dlg.setWindowTitle("catflap")
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(300)
    dlg.setCancelButton(None)
    loop = QtCore.QEventLoop()
    box: dict[str, Any] = {}

    def done(result, err):
        box["result"], box["err"] = result, err
        loop.quit()
    run_bg(fn, done)
    if "err" not in box:
        loop.exec()
    dlg.close()
    dlg.deleteLater()
    if box.get("err") is not None:
        raise box["err"]
    return box.get("result")


# ============================================================ plain language
def friendly_error(e: BaseException, address: str = "") -> str:
    """Turn an exception into something a non-technical user can act on."""
    where = f" at {address}" if address else ""
    if isinstance(e, ssl.SSLError):
        return (f"The router{where} answered, but its security certificate couldn't be "
                "checked. Make sure the address starts with https:// and is your router.")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return (f"The router{where} didn't answer in time. Check the address, and that this "
                "computer is on the same network as the router.")
    if isinstance(e, ConnectionRefusedError):
        return f"Nothing answered{where}. Check the address of your router."
    if isinstance(e, socket.gaierror):
        return f"That address{where} couldn't be found. Check for typos."
    if isinstance(e, OSError) and not str(e).strip():
        return f"Couldn't reach the router{where}."
    msg = str(e).strip() or e.__class__.__name__
    return msg[0].upper() + msg[1:]


def status_text(st: Any, err: str | None, busy: bool = False) -> tuple[str, str]:
    """(text, colour) for a site's state, in plain words."""
    if busy:
        return "Working…", GREY
    if st is not None and getattr(st, "active", False):
        return "Connected", GREEN
    if err:
        first = err.splitlines()[0]
        return f"Couldn't connect: {first[:90]}", RED
    return "Not connected", GREY
