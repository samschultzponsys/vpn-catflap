"""Shared Qt helpers for the CatFlap GUI: icons, background work, plain-language errors."""

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
from .model import PALETTE  # noqa: E402,F401 - re-exported for the UI


def require_qt() -> None:
    if QtWidgets is None:
        raise SystemExit("The catflap window needs PySide6 (Fedora: dnf install python3-pyside6; "
                         "Debian: apt install python3-pyside6.qtwidgets; pip: PySide6-Essentials)")


# ============================================================ icons
# The tray icon follows the app icon (catflap/resources/catflap.svg): the same
# cat silhouette, sitting in a tunnel arch and reaching up. The outline below is
# copied from the icon file, in the icon's own 128-unit coordinates.
CAT_OUTLINE = ("M63 44 L65 27 L72 35.5 C74 35 76 35 78 35.5 L84.5 27.5 L85 38.5 C87 40.5 88.5 43 90.5 45.5 C91 47 90 48.5 88.5 49 C87.5 50 86.5 50.8 85 51 C90 45 96 36 101 29.5 C102.5 26.8 106 26 107.6 28.4 C108.8 30.6 107.4 33.4 105.6 34.6 C99.5 41 94 49 90.5 56 C88.5 62 88 71 88.5 81 C89 89 90 96 92 100 C94 101 94.6 104 93 105.2 L84.2 105.2 C83.4 99 82.4 94 80.8 89.5 C77.5 93 74.5 98 73.2 101 C75.2 102 75.8 104.6 74 105.6 L37 105.6 C30 105.6 25.5 101.5 26.2 96.5 C26.7 93 30 92.4 31 95 C31.6 97.5 33.2 99.8 38 100.2 L46.5 100.2 C43 97 42.4 90 43.4 85 C45 77 50 71.5 55.5 67.5 C60.5 63.5 62.3 58 62.6 53 C62.8 49.5 62.9 46.5 63 44 Z")
# Where the cat sits inside the tray icon's 64-unit box (inside the arch).
_CAT_SCALE, _CAT_DX, _CAT_DY = 0.42, 32 - 67 * 0.42, 58 - 105.6 * 0.42


def svg_path(d: str, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> "QtGui.QPainterPath":
    """Build a QPainterPath from an absolute SVG path using M, L, C and Z."""
    import re
    tokens = re.findall(r"[MLCZ]|-?\d*\.?\d+", d)
    path = QtGui.QPainterPath()
    pt = lambda i: QtCore.QPointF(float(tokens[i]) * scale + dx, float(tokens[i + 1]) * scale + dy)  # noqa: E731
    i, cmd = 0, None
    while i < len(tokens):
        if tokens[i] in "MLCZ":
            cmd = tokens[i]
            i += 1
            if cmd == "Z":
                path.closeSubpath()
                continue
        if cmd == "M":
            path.moveTo(pt(i))
            i += 2
            cmd = "L"            # further pairs after M are line-tos
        elif cmd == "L":
            path.lineTo(pt(i))
            i += 2
        elif cmd == "C":
            path.cubicTo(pt(i), pt(i + 2), pt(i + 4))
            i += 6
        else:
            raise ValueError(f"unsupported SVG path command near token {i}")
    return path


def _cat_path(s: float) -> "QtGui.QPainterPath":
    return svg_path(CAT_OUTLINE, _CAT_SCALE * s, _CAT_DX * s, _CAT_DY * s)


def _arch_path(s: float) -> "QtGui.QPainterPath":
    """Tunnel arch: up the left side, half circle over the top, down the right."""
    path = QtGui.QPainterPath()
    path.moveTo(11 * s, 58 * s)
    path.lineTo(11 * s, 31 * s)
    path.arcTo(QtCore.QRectF(11 * s, 10 * s, 42 * s, 42 * s), 180, -180)
    path.lineTo(53 * s, 58 * s)
    return path


def make_pixmap(colors: list[str], error: bool = False, size: int = 64,
                style: str = "site", phase: float = 0.0) -> "QtGui.QPixmap":
    """The tray icon: the cat sitting in its tunnel, reaching up.

    Disconnected: grey outline. Failed: grey outline with a red dot.
    Connected: a solid tile with the white tunnel and cat and a green dot (the dot
    shows how many sites are connected when there's more than one). ``colors``
    holds one color per connected site; ``style`` picks the tile:

    * ``"site"``: the site's color; with several sites, a gradient across all of them
    * ``"cycle"``: fades from one site's color to the next (``phase`` 0..n moves it)
    * ``"blue"``: always the blue app icon
    """
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    s = size / 64.0
    cat = _cat_path(s)
    if colors:
        tile = _tile_gradient(colors, size, style, phase)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QBrush(tile))
        p.drawRoundedRect(QtCore.QRectF(2 * s, 2 * s, 60 * s, 60 * s), 14 * s, 14 * s)
        p.setPen(QtGui.QPen(QtGui.QColor("#BFDBFE"), 4.5 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_arch_path(s))
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor("#F8FAFC"))
        p.drawPath(cat)
        _badge(p, s, "#22C55E", str(len(colors)) if len(colors) > 1 else "")
    else:
        p.setPen(QtGui.QPen(QtGui.QColor(GREY), 3 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(cat)
        p.setPen(QtGui.QPen(QtGui.QColor(GREY), 5 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(_arch_path(s))
        if error:
            _badge(p, s, RED, "")
    p.end()
    return pm


def _mix(a: "QtGui.QColor", b: "QtGui.QColor", t: float) -> "QtGui.QColor":
    t = max(0.0, min(1.0, t))
    return QtGui.QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t,
                                 a.greenF() + (b.greenF() - a.greenF()) * t,
                                 a.blueF() + (b.blueF() - a.blueF()) * t)


def _tile_gradient(colors: list[str], size: int, style: str, phase: float) -> "QtGui.QLinearGradient":
    g = QtGui.QLinearGradient(0, 0, size, size)
    if style == "blue" or not colors:
        g.setColorAt(0, QtGui.QColor("#3B82F6"))
        g.setColorAt(1, QtGui.QColor("#1E3A8A"))
        return g
    cols = [QtGui.QColor(c) for c in colors]
    if style == "cycle" and len(cols) > 1:
        i = int(phase) % len(cols)
        base = _mix(cols[i], cols[(i + 1) % len(cols)], phase - int(phase))
        cols = [base]
    if len(cols) == 1:
        # One color: a gentle light-to-deep shade of it, like the app icon.
        c = cols[0]
        g.setColorAt(0, c.lighter(115))
        g.setColorAt(1, c.darker(150))
        return g
    for i, c in enumerate(cols):
        g.setColorAt(i / (len(cols) - 1), c)
    return g


def _badge(p: "QtGui.QPainter", s: float, color: str, text: str) -> None:
    """Status dot in the bottom-right corner, with an optional number."""
    r = QtCore.QRectF(39 * s, 39 * s, 24 * s, 24 * s)
    p.setPen(QtGui.QPen(QtGui.QColor("white"), 2.5 * s))
    p.setBrush(QtGui.QColor(color))
    p.drawEllipse(r)
    if text:
        f = p.font()
        f.setBold(True)
        f.setPixelSize(max(6, int(15 * s)))
        p.setFont(f)
        p.setPen(QtGui.QColor("white"))
        p.drawText(r, Qt.AlignCenter, text)


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
    for name in (os.environ.get("FLATPAK_ID", ""), "CatFlap"):
        if name and QtGui.QIcon.hasThemeIcon(name):
            return QtGui.QIcon.fromTheme(name)
    return QtGui.QIcon(make_pixmap(["#3B82F6"], size=128))


def make_icon(colors: list[str], error: bool = False, size: int = 64,
              style: str = "site", phase: float = 0.0) -> "QtGui.QIcon":
    return QtGui.QIcon(make_pixmap(colors, error, size, style, phase))


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
    dlg.setWindowTitle("CatFlap")
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


BLUE = "#3B82F6"
INFO = "info:"   # prefix for informational notes kept alongside errors


def status_text(st: Any, err: str | None, busy: bool = False) -> tuple[str, str]:
    """(text, color) for a site's state, in plain words. ``err`` may be an error or,
    prefixed with INFO, a note (e.g. "you're at this site" or a conflict workaround)."""
    note = err[len(INFO):] if err and err.startswith(INFO) else None
    if busy:
        return "Working…", GREY
    if st is not None and getattr(st, "active", False):
        return (f"Connected. {note}" if note else "Connected"), GREEN
    if note:
        return note, BLUE
    if err:
        return f"Couldn't connect: {err.splitlines()[0]}", RED
    return "Not connected", GREY


def short_status(st: Any, err: str | None, busy: bool = False) -> str:
    """A few words for the tray menu; the window shows the full text."""
    note = err[len(INFO):] if err and err.startswith(INFO) else None
    if busy:
        return "Working…"
    if st is not None and getattr(st, "active", False):
        return "Connected (workaround)" if note and "same addresses" in note else "Connected"
    if note and "You're at" in note:
        return "You're here"
    if err and not note:
        return "Couldn't connect"
    return "Not connected"


# ============================================================ color picker
_Widget = QtWidgets.QWidget if QtWidgets else object


class ColorPicker(_Widget):
    """Swatches in rows of 12 plus "Custom…" for any other color."""

    PER_ROW = 12

    def __init__(self, parent=None, size: int = 26):
        super().__init__(parent)
        self._size = size
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.group = QtWidgets.QButtonGroup(self)
        for i, c in enumerate(PALETTE):
            lay.addWidget(self._swatch(c), i // self.PER_ROW, i % self.PER_ROW)
        self.custom = QtWidgets.QPushButton("Custom…")
        self.custom.clicked.connect(self._pick_custom)
        rows = (len(PALETTE) + self.PER_ROW - 1) // self.PER_ROW
        lay.addWidget(self.custom, rows, 0, 1, 4)
        self._custom_btn = None

    def _swatch(self, c: str) -> "QtWidgets.QToolButton":
        b = QtWidgets.QToolButton()
        b.setCheckable(True)
        b.setFixedSize(self._size, self._size)
        r = self._size // 2
        b.setStyleSheet(f"QToolButton {{ background:{c}; border-radius:{r}px; border:2px solid transparent; }}"
                        f"QToolButton:checked {{ border:3px solid palette(text); }}")
        b.setProperty("color", c)
        b.setToolTip(c)
        self.group.addButton(b)
        return b

    def color(self) -> str:
        b = self.group.checkedButton()
        return b.property("color") if b else PALETTE[0]

    def set_color(self, c: str) -> None:
        c = (c or "").upper()
        for b in self.group.buttons():
            if b.property("color").upper() == c:
                b.setChecked(True)
                return
        if c:
            self._set_custom(c)

    def first_unused(self, used: set[str]) -> None:
        used = {u.upper() for u in used if u}
        for b in self.group.buttons():
            if b.property("color").upper() not in used:
                b.setChecked(True)
                return
        self.group.buttons()[0].setChecked(True)

    def _set_custom(self, c: str) -> None:
        if self._custom_btn is None:
            self._custom_btn = self._swatch(c)
            rows = (len(PALETTE) + self.PER_ROW - 1) // self.PER_ROW
            self.layout().addWidget(self._custom_btn, rows, 4)
        r = self._size // 2
        self._custom_btn.setProperty("color", c)
        self._custom_btn.setToolTip(c)
        self._custom_btn.setStyleSheet(
            f"QToolButton {{ background:{c}; border-radius:{r}px; border:2px solid transparent; }}"
            f"QToolButton:checked {{ border:3px solid palette(text); }}")
        self._custom_btn.setChecked(True)

    def _pick_custom(self) -> None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color()), self, "Pick a color")
        if c.isValid():
            self._set_custom(c.name().upper())


class ColorDialog(QtWidgets.QDialog if QtWidgets else object):
    def __init__(self, parent, title: str, current: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Used for the tray icon while you're connected to this site."))
        self.picker = ColorPicker(self)
        self.picker.set_color(current)
        lay.addWidget(self.picker)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
