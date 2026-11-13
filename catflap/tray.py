"""CatFlap window and tray app (Qt / PySide6).

KDE Plasma on Wayland shows QSystemTrayIcon through StatusNotifierItem;
Windows uses the notification area.

* First start: a wizard asks for Basic or Advanced (with live previews) and
  offers to add the first site with a step-by-step guide.
* Basic view: one big card per site with a Connect button.
* Advanced view: sites and gateways tables, details, checks and every action.
* The window minimizes to the tray; closing asks "keep running in the tray or
  quit" (with "remember my choice").
* Tray icon: the cat is a grey outline when nothing is connected, and turns
  the site's color when connected (striped for several sites); red badge after
  a failure.
* Notifications, start-at-login, daily update check with install-aware
  update instructions.

All real work happens in :class:`catflap.core.Manager` on worker threads.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import autostart, checks, update
from .core import Manager
from .drivers import DRIVERS
from .model import Profile
from .ui_common import (GREY, INFO, ColorDialog, app_icon, offer_wireguard_install, short_status, QtCore, QtGui, QtWidgets, dot_icon, friendly_error, make_icon, require_qt, run_bg, run_blocking)
from .version import __version__

__all__ = ["MainWindow", "TrayApp", "make_icon", "main"]


# ============================================================ small dialogs
class EditSiteDialog(QtWidgets.QDialog if QtWidgets else object):
    def __init__(self, parent, p: Profile):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {p.display}")
        f = QtWidgets.QFormLayout(self)
        self.label = QtWidgets.QLineEdit(p.display)
        self.endpoint = QtWidgets.QLineEdit(p.endpoint_host)
        self.routes = QtWidgets.QLineEdit(", ".join(p.routes))
        self.dns_domain = QtWidgets.QLineEdit(p.dns_domain or "")
        self.dns_domain.setPlaceholderText("e.g. office.lan (optional)")
        self.dns_server = QtWidgets.QLineEdit(", ".join(p.dns_servers))
        self.dns_server.setPlaceholderText("DNS server inside the site, e.g. 192.168.1.1")
        f.addRow("Name", self.label)
        f.addRow("Site's internet address / DDNS name", self.endpoint)
        f.addRow("Networks to reach (comma separated)", self.routes)
        f.addRow("Internal domain (split DNS)", self.dns_domain)
        f.addRow("DNS server for that domain", self.dns_server)
        f.addRow(QtWidgets.QLabel("<small>Only names ending in the internal domain are looked "
                                  "up through the VPN; everything else uses your normal DNS."
                                  "</small>"))
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


# ============================================================ main window
class MainWindow(QtWidgets.QMainWindow if QtWidgets else object):
    def __init__(self, app: "TrayApp"):
        super().__init__()
        from .ui_views import AdvancedView, BasicView
        self.app = app
        self.setWindowTitle("CatFlap")
        self.setWindowIcon(app_icon())
        self.resize(820, 560)
        self.stack = QtWidgets.QStackedWidget()
        self.basic = BasicView()
        self.advanced = AdvancedView()
        self.stack.addWidget(self.basic)
        self.stack.addWidget(self.advanced)
        self.setCentralWidget(self.stack)
        for v in (self.basic, self.advanced):
            v.toggleRequested.connect(self.app.toggle)
            v.addSiteRequested.connect(self.app.add_site)
        self.basic.switchRequested.connect(lambda: self.set_mode("advanced"))
        self.advanced.switchRequested.connect(lambda: self.set_mode("basic"))
        self.advanced.addGatewayRequested.connect(self.app.add_gateway)
        self.advanced.action.connect(self.on_action)

        mb = self.menuBar()
        fm = mb.addMenu("&File")
        fm.addAction("Add a site…", self.app.add_site)
        fm.addAction("Add a gateway…", self.app.add_gateway)
        fm.addSeparator()
        fm.addAction("Export everything (to move to another computer)…", self.export)
        fm.addAction("Import from another computer…", self.import_bundle)
        fm.addSeparator()
        fm.addAction("Hide to tray", self.hide)
        fm.addAction("Quit", self.app.quit)
        vm = mb.addMenu("&View")
        grp = QtGui.QActionGroup(self)
        self.a_basic = vm.addAction("Basic")
        self.a_adv = vm.addAction("Advanced")
        for a, mode in ((self.a_basic, "basic"), (self.a_adv, "advanced")):
            a.setCheckable(True)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, mo=mode: self.set_mode(mo))
        sm = mb.addMenu("&Settings")
        self.a_autostart = sm.addAction("Start CatFlap when I log in")
        self.a_autostart.setCheckable(True)
        self.a_autostart.setChecked(autostart.is_enabled())
        self.a_autostart.toggled.connect(self.set_autostart)
        self.a_updates = sm.addAction("Check for updates automatically")
        self.a_updates.setCheckable(True)
        self.a_updates.setChecked(self.app.settings.get("update_check", True))
        self.a_updates.toggled.connect(lambda v: self.app.set_setting("update_check", v))
        sm.addAction("Ask again what the close button does",
                     lambda: self.app.set_setting("close_behavior", "ask"))
        tm = sm.addMenu("Tray icon when connected")
        tgrp = QtGui.QActionGroup(self)
        current = self.app.settings.get("tray_style", "site")
        for style, label in (("site", "Site colors (blend when several are connected)"),
                             ("cycle", "Cycle through site colors"),
                             ("blue", "Blue")):
            a = tm.addAction(label)
            a.setCheckable(True)
            a.setChecked(style == current)
            tgrp.addAction(a)
            a.triggered.connect(lambda _=False, st=style: self.app.set_tray_style(st))
        hm = mb.addMenu("&Help")
        hm.addAction("Setup guide…", self.app.first_run)
        hm.addAction("Check for updates…", lambda: self.app.check_updates(interactive=True))
        hm.addAction("About CatFlap", self.about)
        self.set_mode(self.app.settings.get("ui_mode", "basic"), save=False)

    # -- view
    def set_mode(self, mode: str, save: bool = True) -> None:
        mode = "advanced" if mode == "advanced" else "basic"
        self.stack.setCurrentWidget(self.advanced if mode == "advanced" else self.basic)
        (self.a_adv if mode == "advanced" else self.a_basic).setChecked(True)
        if save:
            self.app.set_setting("ui_mode", mode)
        self.refresh()

    def refresh(self) -> None:
        a = self.app
        self.stack.currentWidget().update_data(a.profiles, a.status, a.errors, a.busy,
                                               gateways=a.m.store.gateways(), checks=a.checks)

    # -- advanced actions
    def on_action(self, act: str, name: str) -> None:
        a, m = self.app, self.app.m
        try:
            if act == "check":
                a.checks[name] = run_blocking(self, "Running checks…", lambda: m.preflight(name))
                self.refresh()
            elif act == "color":
                p = m.store.profile(name)
                dlg = ColorDialog(self, f"Color for {p.display}", p.color or "")
                if a.run_dialog(dlg):
                    m.update_profile(name, color=dlg.picker.color())
                    a.poll()
            elif act == "edit":
                self.edit_site(name)
            elif act == "apply":
                run_blocking(self, "Re-applying safe settings…", lambda: m.apply(name))
                QtWidgets.QMessageBox.information(self, "CatFlap", "Settings re-applied.")
            elif act == "remove":
                self.remove_site(name)
            elif act == "gw-key":
                key, ok = QtWidgets.QInputDialog.getText(self, "Change API key",
                                                         f"New API key for '{name}':",
                                                         QtWidgets.QLineEdit.Password)
                if ok and key.strip():
                    m.set_gateway_secret(name, "api_key", key.strip())
                    QtWidgets.QMessageBox.information(self, "CatFlap", "API key updated.")
            elif act == "gw-remove":
                if QtWidgets.QMessageBox.question(
                        self, "Remove gateway", f"Remove gateway '{name}' and its saved API key "
                        "from this computer?") == QtWidgets.QMessageBox.Yes:
                    m.remove_gateway(name)
                    a.poll()
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "CatFlap", friendly_error(e))

    def edit_site(self, name: str) -> None:
        m = self.app.m
        p = m.store.profile(name)
        dlg = EditSiteDialog(self, p)
        if not self.app.run_dialog(dlg):
            return
        routes = [r.strip() for r in dlg.routes.text().split(",") if r.strip()]
        dom = dlg.dns_domain.text().strip()
        servers = [s.strip() for s in dlg.dns_server.text().split(",") if s.strip()]
        endpoint = dlg.endpoint.text().strip()
        kw: dict[str, Any] = {"label": dlg.label.text().strip() or p.display}
        if endpoint and endpoint != p.endpoint_host:
            kw["endpoint"] = endpoint
        if routes != p.routes:
            kw["routes"] = routes
        if dom != (p.dns_domain or "") or servers != p.dns_servers:
            if dom:
                kw.update(dns_domain=dom, dns_servers=servers or None)
            else:
                kw["clear_dns"] = True
        try:
            run_blocking(self, "Saving…", lambda: m.update_profile(name, **kw))
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "CatFlap", friendly_error(e))
        self.app.poll()

    def remove_site(self, name: str) -> None:
        m = self.app.m
        p = m.store.profile(name)
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Remove site")
        box.setText(f"Remove <b>{p.display}</b> from this computer?")
        revoke = None
        if p.gateway and p.client_id:
            box.setInformativeText("You can also delete this computer's access on the router, so "
                                   "the key on this computer stops working.")
            revoke = box.addButton("Remove and delete on router", QtWidgets.QMessageBox.DestructiveRole)
        local = box.addButton("Remove from this computer", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (revoke, local):
            return
        do_revoke = box.clickedButton() is revoke
        try:
            run_blocking(self, "Removing…", lambda: m.remove(name, revoke=do_revoke))
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "CatFlap", friendly_error(e))
        self.app.poll()

    # -- settings / files
    def set_autostart(self, v: bool) -> None:
        try:
            autostart.set_enabled(v)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "CatFlap", str(e))

    def _ask_pass(self, title: str, confirm: bool) -> str | None:
        pw, ok = QtWidgets.QInputDialog.getText(
            self, title, "Passphrase (at least 8 characters). You'll need it on the other "
                         "computer:", QtWidgets.QLineEdit.Password)
        if not ok:
            return None
        if confirm:
            again, ok = QtWidgets.QInputDialog.getText(self, title, "Type it again:",
                                                       QtWidgets.QLineEdit.Password)
            if not ok or again != pw:
                QtWidgets.QMessageBox.warning(self, "CatFlap", "The passphrases don't match.")
                return None
        return pw

    def export(self) -> None:
        QtWidgets.QMessageBox.information(
            self, "Export everything",
            "This saves your routers, API keys, sites and their keys into one file, locked "
            "with a passphrase, so you can set up another computer quickly.\n\n"
            "Don't use the same site on both computers at the same time; for a second "
            "computer it's best to add the site again there.")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export CatFlap", os.path.expanduser("~/Downloads/catflap-export.json"))
        if not path:
            return
        pw = self._ask_pass("Lock the export", confirm=True)
        if pw is None:
            return
        try:
            text = self.app.m.export(pw)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text)
            QtWidgets.QMessageBox.information(self, "CatFlap", f"Saved to {path}.")
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "CatFlap", friendly_error(e))

    def import_bundle(self) -> None:
        from .bundle import is_encrypted
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import CatFlap export", os.path.expanduser("~/Downloads"),
            "CatFlap export (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            pw = self._ask_pass("Unlock the export", confirm=False) if is_encrypted(text) else None
            plan = run_blocking(self, "Reading the export…", lambda: self.app.m.preview_bundle(text, pw))
            choice = "keep"
            clashes = plan["conflict_sites"] + plan["conflict_gateways"]
            if clashes:
                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle("Merge with what's here?")
                box.setText("These already exist on this computer with different settings:\n\n  "
                            + "\n  ".join(clashes))
                box.setInformativeText(
                    f"{len(plan['new_sites'])} new site(s) and {len(plan['new_gateways'])} new "
                    "gateway(s) will be added either way; identical ones are left alone.")
                keep = box.addButton("Keep mine", QtWidgets.QMessageBox.AcceptRole)
                replace = box.addButton("Replace with imported", QtWidgets.QMessageBox.DestructiveRole)
                both = box.addButton("Keep both", QtWidgets.QMessageBox.ActionRole)
                box.addButton(QtWidgets.QMessageBox.Cancel)
                box.setDefaultButton(keep)
                self.app.run_dialog(box)
                clicked = box.clickedButton()
                if clicked not in (keep, replace, both):
                    return
                choice = "replace" if clicked is replace else "both" if clicked is both else "keep"
            msgs = run_blocking(self, "Importing…",
                                lambda: self.app.m.import_bundle(text, pw, on_conflict=choice))
            QtWidgets.QMessageBox.information(self, "CatFlap", "\n".join(msgs))
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "CatFlap", friendly_error(e))
        self.app.poll()

    def about(self) -> None:
        info = update.detect_install()
        QtWidgets.QMessageBox.about(
            self, "About CatFlap",
            f"<b>CatFlap {__version__}</b><br>Safe WireGuard remote access to your sites.<br><br>"
            f"Installed via: <b>{info.method}</b> {info.detail}<br>"
            f"Licensed under the Apache License 2.0; see NOTICE.<br>"
            f"<a href='https://github.com/{update.REPO}'>github.com/{update.REPO}</a>")

    # -- window behaviour
    def closeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        if self.app.quitting:
            ev.accept()
            return
        behavior = self.app.settings.get("close_behavior", "ask")
        if behavior == "ask":
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Close CatFlap?")
            box.setText("Keep CatFlap running in the system tray, or quit completely?")
            box.setInformativeText("Connected sites stay connected either way.")
            tray_btn = box.addButton("Keep running in tray", QtWidgets.QMessageBox.AcceptRole)
            quit_btn = box.addButton("Quit CatFlap", QtWidgets.QMessageBox.DestructiveRole)
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
    CYCLE_MS = 100          # tray color cycling: 100 ms steps,
    CYCLE_STEP = 0.04       # 2.5 s per site color

    def __init__(self, qapp: "QtWidgets.QApplication", manager: Manager | None = None):
        super().__init__()
        self.qapp = qapp
        self.m = manager or Manager()
        self.settings = self.m.store.settings()
        self.quitting = False
        self.paused = 0
        self.profiles: dict[str, Profile] = self.m.store.profiles()
        self.status: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self.busy: set[str] = set()
        self.checks: dict[str, list] = {}
        self.update_status: update.UpdateStatus | None = None

        self.tray = QtWidgets.QSystemTrayIcon(make_icon([]), self)
        self.tray.setToolTip("CatFlap: not connected")
        self.menu = QtWidgets.QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.tray.messageClicked.connect(self._message_clicked)
        self.window = MainWindow(self)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(self.POLL_MS)
        self._phase = 0.0
        self._cycle = QtCore.QTimer(self)        # tray color cycling (style "cycle")
        self._cycle.timeout.connect(self._cycle_tick)
        self._render()
        self.poll()
        if self.settings.get("update_check", True):
            QtCore.QTimer.singleShot(3000, lambda: self.check_updates(interactive=False))

    # -- settings
    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        self.m.store.save()

    # -- dialogs pause polling so the store isn't reloaded under them
    def run_dialog(self, dlg) -> bool:
        self.paused += 1
        try:
            return bool(dlg.exec())
        finally:
            self.paused -= 1
            self.poll()

    # -- polling
    def poll(self) -> None:
        if self.paused:
            return
        m = self.m

        def work():
            m.store.reload()   # the CLI may have changed things
            profiles = m.store.profiles()
            st = {}
            for n, p in profiles.items():
                try:
                    st[n] = m.backend.status(p)
                except Exception:  # noqa: BLE001
                    st[n] = None
            return profiles, st
        run_bg(work, self._polled)

    def _polled(self, res, err) -> None:
        if err or res is None:
            return
        self.profiles, self.status = res
        self._render()

    def _tray_icon(self) -> "QtGui.QIcon":
        active = [n for n, s in self.status.items() if s and s.active and n in self.profiles]
        colors = [self.profiles[n].color or "#22C55E" for n in sorted(active)]
        failed = any(not e.startswith(INFO) for e in self.errors.values())
        style = self.settings.get("tray_style", "site")
        return make_icon(colors, error=failed and not active, style=style, phase=self._phase)

    def _cycle_tick(self) -> None:
        """Fade the tray icon through the connected sites' colors (style "cycle")."""
        n = sum(1 for s in self.status.values() if s and s.active)
        if self.settings.get("tray_style", "site") != "cycle" or n < 2:
            self._cycle.stop()
            return
        self._phase = (self._phase + self.CYCLE_STEP) % n
        self.tray.setIcon(self._tray_icon())

    def _update_cycle_timer(self) -> None:
        n = sum(1 for s in self.status.values() if s and s.active)
        want = self.settings.get("tray_style", "site") == "cycle" and n >= 2
        if want and not self._cycle.isActive():
            self._cycle.start(self.CYCLE_MS)
        elif not want and self._cycle.isActive():
            self._cycle.stop()

    def set_tray_style(self, style: str) -> None:
        self.set_setting("tray_style", style)
        self._phase = 0.0
        self._render()

    def _render(self) -> None:
        active = [n for n, s in self.status.items() if s and s.active and n in self.profiles]
        self.tray.setIcon(self._tray_icon())
        self._update_cycle_timer()
        names = [self.profiles[n].display for n in sorted(active)]
        self.tray.setToolTip("CatFlap: " + (", ".join(names) + " connected" if names
                                             else "not connected"))
        self._build_menu()
        if self.window.isVisible():
            self.window.refresh()

    def _build_menu(self) -> None:
        m = self.menu
        m.clear()
        groups: dict[str, list[Profile]] = {}
        for p in self.profiles.values():
            vendor = DRIVERS[p.vendor].display_name if p.vendor in DRIVERS else p.vendor
            groups.setdefault(f"{p.gateway or 'Imported'} · {vendor}", []).append(p)
        for g in sorted(groups):
            if len(groups) > 1:
                m.addSection(g)
            for p in sorted(groups[g], key=lambda x: x.display.lower()):
                st = self.status.get(p.name)
                active = bool(st and st.active)
                text = short_status(st, self.errors.get(p.name), p.name in self.busy)
                verb = "Disconnect" if active else "Connect"
                a = m.addAction(dot_icon(p.color or GREY, active), f"{p.display}: {text}  ({verb})")
                a.setEnabled(p.name not in self.busy)
                a.triggered.connect(lambda _=False, n=p.name: self.toggle(n))
        if groups:
            m.addSeparator()
        m.addAction("Add a site…", self.add_site)
        m.addAction("Open CatFlap", self.show_window)
        if self.update_status and self.update_status.available:
            m.addAction(f"Update to {self.update_status.latest.version}…",
                        lambda: self.check_updates(interactive=True))
        m.addSeparator()
        m.addAction("Quit", self.quit)

    # -- actions
    def _activated(self, reason) -> None:
        if reason in (QtWidgets.QSystemTrayIcon.Trigger, QtWidgets.QSystemTrayIcon.DoubleClick):
            if self.window.isVisible():
                self.window.hide()
            else:
                self.show_window()

    def _message_clicked(self) -> None:
        if self.update_status and self.update_status.available:
            self.check_updates(interactive=True)
        else:
            self.show_window()

    def show_window(self) -> None:
        self.window.refresh()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def first_run(self) -> None:
        from .ui_wizards import FirstRunWizard
        self.show_window()
        wiz = FirstRunWizard(self.window)
        ok = self.run_dialog(wiz)
        self.settings["first_run_done"] = True
        if ok:
            self.settings["ui_mode"] = wiz.mode
        self.m.store.save()
        self.window.set_mode(self.settings.get("ui_mode", "basic"), save=False)
        if ok and wiz.add_now.isChecked():
            self.add_site()

    def add_site(self) -> None:
        from .ui_wizards import SiteWizard
        self.show_window()
        wiz = SiteWizard(self.m, self.window)
        if self.run_dialog(wiz) and wiz.result is not None:
            name = wiz.result.profile.name
            self.profiles = self.m.store.profiles()
            self._render()
            if wiz.connect_after:
                self.toggle(name)

    def add_gateway(self) -> None:
        from .ui_wizards import GatewayWizard
        self.show_window()
        wiz = GatewayWizard(self.m, self.window)
        if self.run_dialog(wiz) and wiz.gateway:
            QtWidgets.QMessageBox.information(
                self.window, "CatFlap", "Gateway added. Use 'Add a site' to choose what to reach.")

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
        p = self.profiles.get(name)
        label = p.display if p else name
        codes = {f.code: f for f in (res.findings if res is not None else [])}
        if err and offer_wireguard_install(self.window, err, self.m.backend):
            self.toggle(name)                       # WireGuard installed: try again
            return
        if err:
            self.errors[name] = friendly_error(err)
            self.tray.showMessage(f"CatFlap: {label}", self.errors[name], QtWidgets.QSystemTrayIcon.Critical)
        elif connecting and res is not None and not res.ok and "on-site" in codes:
            self.errors[name] = INFO + codes["on-site"].message
            self.checks[name] = res.findings
            self.tray.showMessage(f"CatFlap: {label}", codes["on-site"].message,
                                  QtWidgets.QSystemTrayIcon.Information, 6000)
        elif connecting and res is not None and not res.ok and self._offer_switch(name, label, res):
            return
        elif connecting and res is not None and not res.ok:
            bad = [f.message for f in res.findings if f.level == checks.ERROR]
            self.errors[name] = bad[0] if bad else "failed"
            self.checks[name] = res.findings
            self.tray.showMessage(f"CatFlap: couldn't connect to {label}", "\n".join(bad)[:400],
                                  QtWidgets.QSystemTrayIcon.Warning)
        else:
            self.errors.pop(name, None)
            if res is not None:
                self.checks[name] = res.findings
            if connecting and "local-overlap" in codes:
                self.errors[name] = (INFO + "Your current network uses the same addresses as "
                                     f"{label}; CatFlap is routing around it.")
                self.tray.showMessage(f"CatFlap: {label} connected", codes["local-overlap"].message,
                                      QtWidgets.QSystemTrayIcon.Information, 8000)
            else:
                self.tray.showMessage("CatFlap", f"{label} {'connected' if connecting else 'disconnected'}",
                                      QtWidgets.QSystemTrayIcon.Information, 3000)
        self.poll()

    def _offer_switch(self, name: str, label: str, res) -> bool:
        """Two sites with the same addresses can't both be up: offer to switch."""
        errors = [f for f in res.findings if f.level == checks.ERROR]
        if not errors or any(f.code != "tunnel-overlap" for f in errors):
            return False
        others = sorted({f.other for f in errors if f.other})
        names = ", ".join(self.profiles[o].display if o in self.profiles else o for o in others)
        box = QtWidgets.QMessageBox(self.window)
        box.setWindowTitle("Switch sites?")
        box.setText(f"{label} and {names} use the same addresses, so only one of them can be "
                    "connected at a time.")
        box.setInformativeText(f"Disconnect {names} and connect {label}?")
        switch = box.addButton(f"Switch to {label}", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(switch)
        self.run_dialog(box)
        if box.clickedButton() is switch:
            self.busy.add(name)
            self._render()
            run_bg(lambda: self.m.connect(name, switch=True),
                   lambda r, e: self._toggled(name, r, e, True))
        else:
            self.poll()
        return True

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
                QtWidgets.QMessageBox.information(self.window, "CatFlap",
                                                  st.error or f"CatFlap {st.current} is up to date.")
            return
        if not interactive:
            self.tray.showMessage("CatFlap update", f"Version {st.latest.version} is available.",
                                  QtWidgets.QSystemTrayIcon.Information)
            return
        from .ui_update import UpdateDialog
        dlg = UpdateDialog(st, self.window, self.settings)
        self.run_dialog(dlg)
        self.m.store.save()
        if dlg.restart_requested and getattr(dlg, "quit_only", False):
            self.quit()
            return
        if dlg.restart_requested:
            try:
                update.restart(st.install)
            except OSError as e:
                QtWidgets.QMessageBox.warning(self.window, "CatFlap",
                                              f"Couldn't restart automatically ({e}). Start CatFlap "
                                              "again from your application menu.")
            self.quit()

    def check_conflicting_apps(self, finder=None) -> None:
        """Windows: offer to remove WiFiman Desktop, whose Teleport VPN can clash with CatFlap."""
        if (sys.platform != "win32" and finder is None) or self.settings.get("wifiman_prompt") == "never":
            return
        try:
            from .platform import windows
            info = (finder or windows.find_wifiman)()
        except Exception:  # noqa: BLE001 - best effort
            return
        if not info:
            return
        box = QtWidgets.QMessageBox(self.window)
        box.setWindowTitle("WiFiman Desktop is installed")
        box.setText(f"{info['name']} {info.get('version', '')} is installed on this computer.")
        box.setInformativeText(
            "Its Teleport VPN changes routes and DNS too, which can interfere with CatFlap's "
            "connections. CatFlap replaces it, so you can uninstall it.")
        remove = box.addButton("Uninstall WiFiman", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton("Keep it", QtWidgets.QMessageBox.RejectRole)
        never = box.addButton("Don't ask again", QtWidgets.QMessageBox.ActionRole)
        self.run_dialog(box)
        if box.clickedButton() is never:
            self.set_setting("wifiman_prompt", "never")
        elif box.clickedButton() is remove:
            from .platform.windows import uninstall_program
            try:
                run_blocking(self.window, "Uninstalling WiFiman Desktop…",
                             lambda: uninstall_program(info, self.m.backend.run))
                self.tray.showMessage("CatFlap", "WiFiman Desktop was uninstalled.",
                                      QtWidgets.QSystemTrayIcon.Information, 4000)
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(self.window, "CatFlap", friendly_error(e))

    def show_after_restart(self) -> None:
        from .ui_update import after_restart_message
        msg = after_restart_message(self.settings)
        self.m.store.save()
        if not msg:
            return
        title, text = msg
        if title.startswith("Update not"):
            QtWidgets.QMessageBox.information(self.window, title, text)
        else:
            self.tray.showMessage(title, text, QtWidgets.QSystemTrayIcon.Information, 6000)

    def quit(self) -> None:
        self.quitting = True
        self.tray.hide()
        self.qapp.quit()


def main(argv: list[str] | None = None) -> int:
    require_qt()
    argv = sys.argv[1:] if argv is None else argv
    QtWidgets.QApplication.setApplicationName("catflap")
    QtWidgets.QApplication.setApplicationDisplayName("CatFlap")
    QtWidgets.QApplication.setDesktopFileName(os.environ.get("FLATPAK_ID", "catflap"))
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(app_icon())
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        print("catflap: no system tray available; showing the window only", file=sys.stderr)
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
    QtCore.QTimer.singleShot(500, app.show_after_restart)
    QtCore.QTimer.singleShot(2500, app.check_conflicting_apps)
    if not app.settings.get("first_run_done"):
        QtCore.QTimer.singleShot(0, app.first_run)
    elif "--minimized" not in argv:
        app.show_window()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
