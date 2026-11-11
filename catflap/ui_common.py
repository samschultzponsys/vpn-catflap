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


def make_pixmap(colors: list[str], error: bool = False, size: int = 64) -> "QtGui.QPixmap":
    """The cat in its cat flap.

    Disconnected: grey frame, cat drawn as an outline only. Connected: cat and
    frame in the connection colour (cat striped when several sites are up).
    Error: red badge in the corner.
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
    return pm


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
