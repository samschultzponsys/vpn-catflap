"""The "update available" window.

Always shows the manual way (full, copyable commands and a link to the
download page) next to the automatic one. After an update catflap restarts
itself into the new version; inside the Flatpak this uses the portal's
"latest version" launch, so the reopened app really is the new one.
"""

from __future__ import annotations

from . import update
from .ui_common import app_icon, QtCore, QtGui, QtWidgets, Qt, friendly_error, run_blocking
from .version import __version__

_Dialog = QtWidgets.QDialog if QtWidgets else object

METHOD_NAMES = {"flatpak": "Flatpak", "rpm": "an RPM package", "deb": "a .deb package",
                "pipx": "pipx", "pip": "pip", "windows": "the Windows installer",
                "source": "a source checkout"}


class UpdateDialog(_Dialog):
    def __init__(self, st: "update.UpdateStatus", parent=None, settings: dict | None = None):
        super().__init__(parent)
        self.st = st
        self.settings = settings if settings is not None else {}
        self.restart_requested = False
        rel, info = st.latest, st.install
        self.setWindowTitle("Update catflap")
        self.resize(640, 520)
        lay = QtWidgets.QVBoxLayout(self)

        head = QtWidgets.QHBoxLayout()
        pic = QtWidgets.QLabel()
        pic.setPixmap(app_icon().pixmap(56, 56))
        head.addWidget(pic)
        title = QtWidgets.QLabel(
            f"<h2 style='margin:0'>catflap {rel.version} is available</h2>"
            f"You have {st.current}, installed with {METHOD_NAMES.get(info.method, info.method)}.")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        lay.addLayout(head)

        notes = QtWidgets.QTextBrowser()
        notes.setMarkdown(rel.notes or "No release notes.")
        notes.setOpenExternalLinks(True)
        lay.addWidget(QtWidgets.QLabel("<b>What's new</b>"))
        lay.addWidget(notes, 1)

        lay.addWidget(QtWidgets.QLabel("<b>Update by hand</b> (copy into a terminal)"))
        self.cmds = QtWidgets.QPlainTextEdit()
        self.cmds.setReadOnly(True)
        self.cmds.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.cmds.setMaximumHeight(80)
        lay.addWidget(self.cmds)
        self.set_commands(update.flatpak_commands(st) if info.method == "flatpak" else st.instructions)
        row = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton("Copy commands")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.cmds.toPlainText()))
        row.addWidget(copy)
        page = QtWidgets.QPushButton("Open download page")
        page.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(rel.url)))
        row.addWidget(page)
        row.addStretch(1)
        lay.addLayout(row)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.status)

        bb = QtWidgets.QHBoxLayout()
        bb.addStretch(1)
        self.later = QtWidgets.QPushButton("Later")
        self.later.clicked.connect(self.reject)
        bb.addWidget(self.later)
        self.restart_btn = QtWidgets.QPushButton("Restart catflap")
        self.restart_btn.clicked.connect(self.do_restart)
        self.restart_btn.setVisible(False)
        bb.addWidget(self.restart_btn)
        if info.method == "flatpak":
            label = "Download and install"
        elif info.automatic:
            label = "Update now"
        else:
            label = ""
        self.go = QtWidgets.QPushButton(label)
        self.go.setDefault(True)
        self.go.clicked.connect(self.do_update)
        self.go.setVisible(bool(label))
        bb.addWidget(self.go)
        lay.addLayout(bb)

    def set_commands(self, cmds: list[str]) -> None:
        self.cmds.setPlainText("\n".join(cmds))

    def do_update(self) -> None:
        self.go.setEnabled(False)
        try:
            res = run_blocking(self, f"Downloading catflap {self.st.latest.version}…",
                               lambda: update.apply_update(self.st))
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"<span style='color:#D92D20'>{friendly_error(e)}</span> "
                                "You can still update by hand with the commands above.")
            self.go.setEnabled(True)
            return
        if res.commands:
            self.set_commands(res.commands)
        if res.done:
            self.status.setText(f"<b>✓ {res.message}</b> Click <b>Restart catflap</b> to start "
                                "using it. Connected sites stay connected.")
            self.go.setVisible(False)
            self.restart_btn.setVisible(res.restart)
            self.restart_btn.setDefault(True)
        elif res.downloaded:
            self.status.setText(
                f"<b>Downloaded.</b> One step left: copy the command above into a terminal and "
                "run it, then click <b>Restart catflap</b>.<br><small>The Flatpak sandbox can't "
                "install updates by itself. To allow it once, run: <tt>flatpak override --user "
                f"--talk-name=org.freedesktop.Flatpak {self.st.install.detail or update.FLATPAK_ID}"
                "</tt></small>")
            self.go.setVisible(False)
            self.restart_btn.setVisible(True)
            self.restart_btn.setDefault(True)
        else:
            self.status.setText(f"<span style='color:#D92D20'>{res.message}</span>")
            self.go.setEnabled(True)

    def do_restart(self) -> None:
        # Remember what we expect to come back as, so the new instance can confirm it.
        self.settings["pending_update"] = self.st.latest.version
        self.restart_requested = True
        self.accept()


def after_restart_message(settings: dict) -> tuple[str, str] | None:
    """On start: (title, text) to show if an update just finished or didn't happen."""
    from .version import is_newer
    last = settings.get("last_run_version")
    pending = settings.pop("pending_update", None)
    settings["last_run_version"] = __version__
    if pending and is_newer(pending, __version__):
        return ("Update not installed yet",
                f"catflap is still version {__version__}; {pending} hasn't been installed. "
                "Use Help → Check for updates… to see the command again.")
    if last and is_newer(__version__, last):
        return ("catflap updated", f"catflap was updated from {last} to {__version__}.")
    return None
