"""The two main-window views.

* :class:`BasicView` - big cards, one Connect button per site, no jargon.
* :class:`AdvancedView` - sites and gateways as tables, routes, endpoints,
  checks and every management action.

Both are display-only: they take data through ``update()`` and report clicks
through signals, so they can also be rendered with demo data as previews in the
first-run wizard.
"""

from __future__ import annotations

from typing import Any

from .drivers import DRIVERS
from .model import Profile
from .ui_common import (GREY, QtCore, QtGui, QtWidgets, Qt, dot_pixmap, make_pixmap,
                        status_text)

_Base = QtWidgets.QWidget if QtWidgets else object


def _vendor_name(v: str) -> str:
    return DRIVERS[v].display_name if v in DRIVERS else v


class SiteCard(_Base):
    def __init__(self, view: "BasicView"):
        super().__init__()
        self.view = view
        self.name = ""
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)
        self.dot = QtWidgets.QLabel()
        lay.addWidget(self.dot)
        text = QtWidgets.QVBoxLayout()
        text.setSpacing(2)
        self.title = QtWidgets.QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.35)
        f.setBold(True)
        self.title.setFont(f)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.status)
        lay.addLayout(text, 1)
        self.button = QtWidgets.QPushButton()
        self.button.setMinimumSize(130, 40)
        self.button.clicked.connect(lambda: self.view.toggleRequested.emit(self.name))
        lay.addWidget(self.button)

    def set_data(self, p: Profile, st: Any, err: str | None, busy: bool) -> None:
        self.name = p.name
        active = bool(st and st.active)
        self.dot.setPixmap(dot_pixmap(p.color or GREY, active, 22))
        self.title.setText(p.display)
        text, color = status_text(st, err, busy)
        self.status.setText(f"<span style='color:{color}'>{text}</span>")
        self.button.setText("Disconnect" if active else "Connect")
        self.button.setEnabled(not busy)
        self.button.setDefault(not active)


class BasicView(_Base):
    toggleRequested = QtCore.Signal(str) if QtCore else None
    addSiteRequested = QtCore.Signal() if QtCore else None
    switchRequested = QtCore.Signal() if QtCore else None

    def __init__(self):
        super().__init__()
        self.setStyleSheet("#card { border: 1px solid palette(mid); border-radius: 12px;"
                           " background: palette(base); }")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        head = QtWidgets.QLabel("Your sites")
        f = head.font()
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setBold(True)
        head.setFont(f)
        outer.addWidget(head)
        self.hint = QtWidgets.QLabel("Click <b>Connect</b> to reach a site. Your normal internet "
                                     "keeps working while you're connected.")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        self.stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.stack, 1)

        # empty state
        empty = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(empty)
        el.addStretch(1)
        pic = QtWidgets.QLabel()
        pic.setPixmap(make_pixmap([], size=120))
        pic.setAlignment(Qt.AlignCenter)
        el.addWidget(pic)
        msg = QtWidgets.QLabel("<h2>No sites yet</h2>Add the first place you want to reach, "
                               "like your home or office. A step-by-step guide will help you.")
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        el.addWidget(msg)
        first = QtWidgets.QPushButton("Add your first site")
        first.setMinimumHeight(44)
        first.setDefault(True)
        first.clicked.connect(self.addSiteRequested.emit)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(first)
        row.addStretch(1)
        el.addLayout(row)
        el.addStretch(2)
        self.stack.addWidget(empty)

        # cards
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        holder = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(holder)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch(1)
        scroll.setWidget(holder)
        self.stack.addWidget(scroll)
        self.cards: list[SiteCard] = []

        bottom = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("＋  Add a site")
        self.add_btn.setMinimumHeight(40)
        self.add_btn.clicked.connect(self.addSiteRequested.emit)
        bottom.addWidget(self.add_btn)
        bottom.addStretch(1)
        switch = QtWidgets.QPushButton("Advanced view")
        switch.setFlat(True)
        switch.clicked.connect(self.switchRequested.emit)
        bottom.addWidget(switch)
        outer.addLayout(bottom)

    def update_data(self, profiles: dict[str, Profile], status: dict[str, Any],
                    errors: dict[str, str], busy: set[str], **_: Any) -> None:
        items = sorted(profiles.values(), key=lambda p: p.display.lower())
        self.stack.setCurrentIndex(1 if items else 0)
        self.add_btn.setVisible(bool(items))
        self.hint.setVisible(bool(items))
        while len(self.cards) < len(items):
            c = SiteCard(self)
            self.cards_layout.insertWidget(len(self.cards), c)
            self.cards.append(c)
        while len(self.cards) > len(items):
            c = self.cards.pop()
            c.setParent(None)
            c.deleteLater()
        for card, p in zip(self.cards, items):
            card.set_data(p, status.get(p.name), errors.get(p.name), p.name in busy)


class AdvancedView(_Base):
    toggleRequested = QtCore.Signal(str) if QtCore else None
    addSiteRequested = QtCore.Signal() if QtCore else None
    addGatewayRequested = QtCore.Signal() if QtCore else None
    switchRequested = QtCore.Signal() if QtCore else None
    action = QtCore.Signal(str, str) if QtCore else None    # (action, profile-or-gateway name)

    SITE_COLS = ["", "Site", "Gateway", "Status", "Interface", "Routes", "Endpoint"]
    GW_COLS = ["Gateway", "Vendor", "Address", "Sites"]

    def __init__(self):
        super().__init__()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs, 1)

        # ---- sites tab
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        split = QtWidgets.QSplitter(Qt.Vertical)
        self.sites = QtWidgets.QTableWidget(0, len(self.SITE_COLS))
        self.sites.setHorizontalHeaderLabels(self.SITE_COLS)
        self.sites.horizontalHeader().setStretchLastSection(True)
        self.sites.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.sites.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.sites.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.sites.verticalHeader().setVisible(False)
        self.sites.doubleClicked.connect(lambda _: self._emit_site("toggle"))
        self.sites.itemSelectionChanged.connect(self._show_details)
        split.addWidget(self.sites)
        self.details = QtWidgets.QTextBrowser()
        self.details.setOpenExternalLinks(True)
        split.addWidget(self.details)
        split.setSizes([260, 160])
        lay.addWidget(split, 1)
        row = QtWidgets.QHBoxLayout()
        for text, act in (("Connect / Disconnect", "toggle"), ("Run checks", "check"),
                          ("Edit…", "edit"), ("Color…", "color"), ("Re-apply settings", "apply"),
                          ("Remove…", "remove")):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, a=act: self._emit_site(a))
            row.addWidget(b)
        row.addStretch(1)
        add = QtWidgets.QPushButton("＋ Add site…")
        add.clicked.connect(self.addSiteRequested.emit)
        row.addWidget(add)
        lay.addLayout(row)
        self.tabs.addTab(w, "Sites")

        # ---- gateways tab
        g = QtWidgets.QWidget()
        gl = QtWidgets.QVBoxLayout(g)
        self.gws = QtWidgets.QTableWidget(0, len(self.GW_COLS))
        self.gws.setHorizontalHeaderLabels(self.GW_COLS)
        self.gws.horizontalHeader().setStretchLastSection(True)
        self.gws.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.gws.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.gws.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.gws.verticalHeader().setVisible(False)
        gl.addWidget(self.gws, 1)
        grow = QtWidgets.QHBoxLayout()
        for text, act in (("Change API key…", "gw-key"), ("Remove gateway…", "gw-remove")):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, a=act: self._emit_gw(a))
            grow.addWidget(b)
        grow.addStretch(1)
        addg = QtWidgets.QPushButton("＋ Add gateway…")
        addg.clicked.connect(self.addGatewayRequested.emit)
        grow.addWidget(addg)
        gl.addLayout(grow)
        gl.addWidget(QtWidgets.QLabel("API keys and passwords are stored only in this "
                                      "computer's keyring."))
        self.tabs.addTab(g, "Gateways")

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        switch = QtWidgets.QPushButton("Basic view")
        switch.setFlat(True)
        switch.clicked.connect(self.switchRequested.emit)
        bottom.addWidget(switch)
        outer.addLayout(bottom)

        self._profiles: dict[str, Profile] = {}
        self._status: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._checks: dict[str, list] = {}

    # -- helpers
    def selected_site(self) -> str | None:
        rows = self.sites.selectionModel().selectedRows()
        return self.sites.item(rows[0].row(), 1).data(Qt.UserRole) if rows else None

    def selected_gateway(self) -> str | None:
        rows = self.gws.selectionModel().selectedRows()
        return self.gws.item(rows[0].row(), 0).text() if rows else None

    def _emit_site(self, act: str) -> None:
        n = self.selected_site()
        if n:
            if act == "toggle":
                self.toggleRequested.emit(n)
            else:
                self.action.emit(act, n)

    def _emit_gw(self, act: str) -> None:
        n = self.selected_gateway()
        if n:
            self.action.emit(act, n)

    def _show_details(self) -> None:
        n = self.selected_site()
        p = self._profiles.get(n) if n else None
        if not p:
            self.details.setHtml("<i>Select a site to see its details.</i>")
            return
        st = self._status.get(n)
        rows = [("Name (for the command line)", p.name), ("Interface", p.ifname),
                ("Vendor", _vendor_name(p.vendor)), ("Gateway", p.gateway or "imported .conf"),
                ("Site", p.site_name or "–"), ("Endpoint", f"{p.endpoint_host}:{p.endpoint_port}"),
                ("Tunnel address", ", ".join(p.addresses)), ("VPN subnet", p.vpn_subnet or "–"),
                ("Routed networks", ", ".join(p.routes)),
                ("Split DNS", f"{p.dns_domain} via {', '.join(p.dns_servers)}" if p.dns_domain else "off"),
                ("Device / client id", f"{p.device or '–'} / {p.client_id or '–'}"),
                ("Gateway WAN IP", p.wan_ip or "–")]
        if st and st.active:
            rows.append(("Traffic", f"received {st.rx_bytes or 0} B, sent {st.tx_bytes or 0} B"))
        html = "<table>" + "".join(f"<tr><td><b>{k}</b>&nbsp;&nbsp;</td><td>{v}</td></tr>"
                                   for k, v in rows) + "</table>"
        if p.warnings:
            html += "<p><b>Warnings</b><ul>" + "".join(f"<li>{w}</li>" for w in p.warnings) + "</ul></p>"
        checks = self._checks.get(n)
        if checks:
            marks = {"error": "✗", "warning": "!", "info": "i", "ok": "✓"}
            html += "<p><b>Last checks</b><br>" + "<br>".join(
                f"{marks.get(f.level, '-')} {f.message}" for f in checks) + "</p>"
        self.details.setHtml(html)

    def update_data(self, profiles: dict[str, Profile], status: dict[str, Any],
                    errors: dict[str, str], busy: set[str], gateways: dict[str, dict] | None = None,
                    checks: dict[str, list] | None = None) -> None:
        self._profiles, self._status, self._errors = profiles, status, errors
        self._checks = checks or {}
        sel = self.selected_site()
        self.sites.setRowCount(0)
        for name, p in sorted(profiles.items(), key=lambda kv: ((kv[1].gateway or ""), kv[1].display.lower())):
            st = status.get(name)
            r = self.sites.rowCount()
            self.sites.insertRow(r)
            sw = QtWidgets.QTableWidgetItem()
            sw.setIcon(QtGui.QIcon(dot_pixmap(p.color or GREY, bool(st and st.active))))
            self.sites.setItem(r, 0, sw)
            it = QtWidgets.QTableWidgetItem(p.display)
            it.setData(Qt.UserRole, name)
            self.sites.setItem(r, 1, it)
            self.sites.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{p.gateway or 'imported'} ({_vendor_name(p.vendor)})"))
            self.sites.setItem(r, 3, QtWidgets.QTableWidgetItem(status_text(st, errors.get(name), name in busy)[0]))
            self.sites.setItem(r, 4, QtWidgets.QTableWidgetItem(p.ifname))
            self.sites.setItem(r, 5, QtWidgets.QTableWidgetItem(", ".join(p.routes)))
            self.sites.setItem(r, 6, QtWidgets.QTableWidgetItem(f"{p.endpoint_host}:{p.endpoint_port}"))
            if name == sel:
                self.sites.selectRow(r)
        self.sites.resizeColumnsToContents()
        self._show_details()

        gsel = self.selected_gateway()
        self.gws.setRowCount(0)
        for gname, g in sorted((gateways or {}).items()):
            r = self.gws.rowCount()
            self.gws.insertRow(r)
            used = [p.display for p in profiles.values() if p.gateway == gname]
            for c, v in enumerate((gname, _vendor_name(g.get("vendor", "")), g.get("url", ""),
                                   ", ".join(used) or "–")):
                self.gws.setItem(r, c, QtWidgets.QTableWidgetItem(v))
            if gname == gsel:
                self.gws.selectRow(r)
        self.gws.resizeColumnsToContents()


def demo_data() -> dict[str, Any]:
    """Example sites for the view previews in the first-run wizard."""
    from .platform.base import TunnelStatus
    home = Profile(name="home", label="Home", ifname="cf-home", vendor="unifi",
                   addresses=["10.250.1.2/32"], peer_public_key="", endpoint_host="home.example.net",
                   endpoint_port=51820, routes=["192.168.1.0/24", "192.168.20.0/24"],
                   gateway="home-udm", color="#22C55E", vpn_subnet="10.250.1.0/28")
    office = Profile(name="office", label="Office", ifname="cf-office", vendor="unifi",
                     addresses=["10.250.2.2/32"], peer_public_key="", endpoint_host="office.example.com",
                     endpoint_port=51820, routes=["10.0.10.0/24"], gateway="office-ucg",
                     color="#3B82F6", vpn_subnet="10.250.2.0/28")
    cabin = Profile(name="cabin", label="Cabin", ifname="cf-cabin", vendor="omada",
                    addresses=["10.250.3.2/32"], peer_public_key="", endpoint_host="cabin.example.org",
                    endpoint_port=51820, routes=["192.168.50.0/24"], color="#F97316")
    return {
        "profiles": {"home": home, "office": office, "cabin": cabin},
        "status": {"home": TunnelStatus(active=True, rx_bytes=48211, tx_bytes=10233),
                   "office": TunnelStatus(active=False), "cabin": TunnelStatus(active=False)},
        "errors": {}, "busy": set(),
        "gateways": {"home-udm": {"vendor": "unifi", "url": "https://192.168.1.1"},
                     "office-ucg": {"vendor": "unifi", "url": "https://10.0.10.1"}},
    }


def render_preview(view_cls, size=(560, 380)) -> "QtGui.QPixmap":
    """Render a view filled with demo data to a pixmap (for the first-run wizard)."""
    v = view_cls()
    v.resize(*size)
    d = demo_data()
    v.update_data(d["profiles"], d["status"], d["errors"], d["busy"], gateways=d["gateways"])
    if isinstance(v, AdvancedView):
        v.sites.selectRow(0)
    v.setAttribute(Qt.WA_DontShowOnScreen, True)
    v.show()
    pm = v.grab()
    v.hide()
    v.deleteLater()
    return pm
