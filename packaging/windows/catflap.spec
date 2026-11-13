# PyInstaller spec for the Windows build: one folder with CatFlap.exe (the app)
# and catflap-cli.exe (the command line; Windows file names ignore case, so it
# can't also be called catflap.exe), sharing the same libraries.
# Build from the repository root:  pyinstaller packaging/windows/catflap.spec
import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

HERE = SPECPATH                                            # packaging/windows
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))     # repository root
ICON = os.path.join(HERE, "catflap.ico")

hidden = collect_submodules("keyring") + ["win32ctypes.core", "PySide6.QtSvg"]
datas = [(os.path.join(ROOT, "catflap", "resources", "catflap.svg"), "catflap/resources")] + copy_metadata("keyring")
excludes = ["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.Qt3DCore"]

common = dict(pathex=[ROOT], hiddenimports=hidden, datas=datas, excludes=excludes)
a_app = Analysis([os.path.join(HERE, "run_tray.py")], **common)
a_cli = Analysis([os.path.join(HERE, "run_cli.py")], **common)

pyz_app = PYZ(a_app.pure)
pyz_cli = PYZ(a_cli.pure)

app = EXE(pyz_app, a_app.scripts, [], exclude_binaries=True, name="CatFlap",
          console=False, icon=ICON)
cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="catflap-cli",
          console=True, icon=ICON)

coll = COLLECT(app, a_app.binaries, a_app.datas, cli, a_cli.binaries, a_cli.datas,
               name="CatFlap")
