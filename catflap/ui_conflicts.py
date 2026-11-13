"""The "Other VPN software" window: one list of what was found, with checkboxes
to uninstall it (Windows) or not be warned about it again."""

from __future__ import annotations

from . import conflicts
from .ui_common import QtWidgets, Qt

_Dialog = QtWidgets.QDialog if QtWidgets else object


class ConflictsDialog(_Dialog):
    def __init__(self, found: list["conflicts.Found"], settings: dict, manual: bool,
                 windows: bool, parent=None):
        super().__init__(parent)
        self.found = found
        self.setWindowTitle("Other VPN software")
        self.resize(700, 520)
        lay = QtWidgets.QVBoxLayout(self)
        n = len(found)
        intro = QtWidgets.QLabel(
            f"<h3 style='margin:0'>Found {n} other VPN program{'s' if n != 1 else ''} that can "
            "interfere with CatFlap</h3>"
            + ("Tick <b>Uninstall</b> to remove one now (its own uninstaller runs; Windows may ask "
               "for permission). " if windows else
               "To remove one, use your software center or package manager. ")
            + "<b>Don't warn again</b> stays quiet about that version; a newer version is "
              "mentioned again.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        rows = QtWidgets.QVBoxLayout(inner)
        ign = conflicts.ignored(settings)
        self.rows = []
        for f in found:
            box = QtWidgets.QFrame()
            box.setFrameShape(QtWidgets.QFrame.StyledPanel)
            bl = QtWidgets.QVBoxLayout(box)
            title = QtWidgets.QLabel(f"<b>{f.label}</b>" + (f" — {f.detail}" if f.detail else ""))
            bl.addWidget(title)
            why = QtWidgets.QLabel(f.conflict.why)
            why.setWordWrap(True)
            bl.addWidget(why)
            cl = QtWidgets.QHBoxLayout()
            un = QtWidgets.QCheckBox("Uninstall")
            un.setVisible(windows)
            un.setEnabled(f.can_uninstall)
            if windows and not f.can_uninstall:
                un.setToolTip("No uninstaller registered; remove it from Settings > Apps.")
            hide = QtWidgets.QCheckBox("Hide tray icon (keep the app)")
            hide.setVisible(windows and f.conflict is conflicts.WIREGUARD_APP)
            quiet = QtWidgets.QCheckBox("Don't warn again")
            quiet.setChecked(ign.get(f.key) == f.version)
            un.toggled.connect(lambda on, h=hide: on and h.setChecked(False))
            hide.toggled.connect(lambda on, u=un: on and u.setChecked(False))
            for w in (un, hide, quiet):
                cl.addWidget(w)
            cl.addStretch(1)
            bl.addLayout(cl)
            rows.addWidget(box)
            self.rows.append((f, un, hide, quiet))
            un.toggled.connect(self._update_button)
        rows.addStretch(1)
        area.setWidget(inner)
        lay.addWidget(area, 1)
        if not windows:
            note = QtWidgets.QLabel("<small>On Linux CatFlap sees other VPNs only while they're "
                                    "running.</small>")
            lay.addWidget(note)

        bb = QtWidgets.QHBoxLayout()
        bb.addStretch(1)
        self.ok = QtWidgets.QPushButton("OK")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        bb.addWidget(self.ok)
        lay.addLayout(bb)

    def _update_button(self) -> None:
        n = len(self.uninstall())
        self.ok.setText(f"Uninstall {n} and close" if n else "OK")

    # -- choices
    def uninstall(self) -> list["conflicts.Found"]:
        return [f for f, un, _, _ in self.rows if not un.isHidden() and un.isEnabled() and un.isChecked()]

    def hide_tray(self) -> list["conflicts.Found"]:
        return [f for f, _, h, _ in self.rows if not h.isHidden() and h.isChecked()]

    def silenced(self) -> list["conflicts.Found"]:
        return [f for f, _, _, q in self.rows if q.isChecked()]

    def unsilenced(self) -> list["conflicts.Found"]:
        return [f for f, _, _, q in self.rows if not q.isChecked()]

    def set_choice(self, key: str, uninstall=None, hide=None, quiet=None) -> None:
        """For tests and scripting."""
        for f, un, h, q in self.rows:
            if f.key == key:
                for box, val in ((un, uninstall), (h, hide), (q, quiet)):
                    if val is not None:
                        box.setChecked(val)


__all__ = ["ConflictsDialog", "Qt"]
