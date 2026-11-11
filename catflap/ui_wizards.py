"""Step-by-step wizards for people who have never set up a VPN.

* :class:`FirstRunWizard` - welcome, pick Basic or Advanced (with live previews),
  offer to add the first site.
* :class:`SiteWizard` - "I want to reach a site": UniFi automatic setup with an
  API key, or a downloaded WireGuard file for Omada, Alta Labs and others.
* :class:`GatewayWizard` - add a UniFi router (API key) without adding a site yet.

All work goes through :class:`catflap.core.Manager`; network calls run in the
background behind a "please wait" dialog.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import netutil
from .core import Manager
from .drivers import get_driver_class
from .drivers.base import GatewayConfig
from .http import fetch_fingerprint
from .ui_common import (app_icon, PALETTE, QtCore, QtGui, QtWidgets, Qt, friendly_error, make_pixmap,
                        run_bg, run_blocking)

_Wizard = QtWidgets.QWizard if QtWidgets else object
_Page = QtWidgets.QWizardPage if QtWidgets else object

VENDORS = [
    ("unifi", "UniFi (Ubiquiti)", "Automatic setup. You'll paste an API key from your UniFi "
                                  "console and catflap does the rest."),
    ("omada", "TP-Link Omada", "You'll download a small WireGuard file from your Omada "
                               "controller and pick it here."),
    ("alta", "Alta Labs", "You'll download a small WireGuard file from the Alta Labs portal "
                          "and pick it here."),
    ("generic", "Another router, or I already have a WireGuard file",
     "Any router with a WireGuard VPN server. You'll pick the .conf file it gave you."),
]

UNIFI_KEY_HELP = """
<b>How to get an API key</b> (one time, takes a minute):
<ol>
<li>Open your UniFi console in a web browser, for example <i>https://192.168.1.1</i>
    or <i>unifi.ui.com</i>.</li>
<li>Go to <b>Settings → Control Plane → Integrations</b>.</li>
<li>Click <b>Create API Key</b>, call it <i>catflap</i>, and copy the key.</li>
<li>Paste it below.</li>
</ol>
The key is saved only on this computer, in your system keyring.
"""

UNIFI_SERVER_HELP = """
<b>Your router doesn't have a WireGuard VPN server yet.</b> Create one (one time):
<ol>
<li>In your UniFi console go to <b>Settings → VPN → VPN Server</b>.</li>
<li>Click <b>Create New</b> and choose <b>WireGuard</b>.</li>
<li>Click <b>Create</b> / <b>Apply Changes</b>. This step is easy to miss: without
    it the VPN never works.</li>
<li>Come back here and click <b>Check again</b>.</li>
</ol>
"""

FILE_HELP = {
    "unifi": "In your UniFi console: <b>Settings → VPN → VPN Server</b>, open your WireGuard "
             "server (make sure it has been created and applied), <b>add a client</b> for this "
             "computer and <b>download</b> its configuration file.",
    "omada": "In your Omada controller: <b>Settings → VPN → WireGuard</b>, add a <b>peer</b> "
             "for this computer and <b>export / download</b> its configuration file. "
             "(Menu names can differ slightly between controller versions.)",
    "alta": "In the Alta Labs portal, open your site's <b>WireGuard VPN</b>, add a <b>client</b> "
            "for this computer and <b>download</b> its configuration file.",
    "generic": "In your router's VPN settings, create a <b>WireGuard client / peer</b> for this "
               "computer and download its configuration (a file ending in <b>.conf</b>).",
}

P_VENDOR, P_LOGIN, P_SITE, P_SERVER, P_NETWORKS, P_FILE, P_NAME, P_DONE = range(8)


def _title(text: str) -> "QtWidgets.QLabel":
    lab = QtWidgets.QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    return lab


def normalize_url(address: str) -> str:
    a = address.strip().rstrip("/")
    if not a:
        return ""
    if "://" not in a:
        a = "https://" + a
    return a


def _local_routes(m: Manager) -> list[str]:
    try:
        return [r.dest for r in m.backend.local_network().routes
                if not netutil.is_default_route(r.dest)]
    except Exception:  # noqa: BLE001 - only used for a hint
        return []


# ============================================================ first run
class FirstRunWizard(_Wizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        from .ui_views import AdvancedView, BasicView, render_preview
        self.setWindowTitle("Welcome to catflap")
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.setPixmap(QtWidgets.QWizard.LogoPixmap, app_icon().pixmap(56, 56))
        self.resize(820, 600)

        welcome = QtWidgets.QWizardPage()
        welcome.setTitle("Welcome to catflap")
        welcome.setSubTitle("Safe, one-click access to your home, office and other sites.")
        wl = QtWidgets.QVBoxLayout(welcome)
        wl.addWidget(_title(
            "<p>catflap connects this computer to the places you manage (your home, your "
            "office, a client's site) as if you were there, using the WireGuard VPN built into "
            "your router.</p>"
            "<p>It works with <b>UniFi</b>, <b>TP-Link Omada</b>, <b>Alta Labs</b> and other "
            "WireGuard routers, and it sets everything up safely for you:</p><ul>"
            "<li>only traffic for your sites goes through the VPN; your normal internet is untouched</li>"
            "<li>your DNS (name lookups) is never taken over</li>"
            "<li>it only connects when you click <b>Connect</b></li>"
            "<li>it checks the connection really works and tells you in plain words when it doesn't</li>"
            "</ul><p>This takes about two minutes.</p>"))
        wl.addStretch(1)
        self.addPage(welcome)

        look = QtWidgets.QWizardPage()
        look.setTitle("How should catflap look?")
        look.setSubTitle("You can switch any time from the View menu.")
        ll = QtWidgets.QHBoxLayout(look)
        self.group = QtWidgets.QButtonGroup(self)
        for i, (mode, title, desc, cls) in enumerate((
                ("basic", "Basic", "Big buttons: one card per site with a Connect button. "
                                   "Best if you just want it to work.", BasicView),
                ("advanced", "Advanced", "Tables with every detail: routes, endpoints, gateways, "
                                         "checks and management tools.", AdvancedView))):
            box = QtWidgets.QVBoxLayout()
            rb = QtWidgets.QRadioButton(title)
            f = rb.font()
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 1.2)
            rb.setFont(f)
            rb.setProperty("mode", mode)
            self.group.addButton(rb, i)
            box.addWidget(rb)
            box.addWidget(_title(desc))
            pic = QtWidgets.QLabel()
            pic.setPixmap(render_preview(cls).scaledToWidth(360, Qt.SmoothTransformation))
            pic.setFrameShape(QtWidgets.QFrame.StyledPanel)
            pic.mousePressEvent = lambda _e, r=rb: r.setChecked(True)  # click the picture to choose
            box.addWidget(pic)
            box.addStretch(1)
            ll.addLayout(box)
            if mode == "basic":
                rb.setChecked(True)
        self.addPage(look)

        done = QtWidgets.QWizardPage()
        done.setTitle("All set")
        dl = QtWidgets.QVBoxLayout(done)
        dl.addWidget(_title("<p>catflap lives in your <b>system tray</b> (near the clock). The "
                            "cat icon is grey when nothing is connected and turns your site's "
                            "colour when you're connected.</p><p>Next, let's add the first place "
                            "you want to reach.</p>"))
        self.add_now = QtWidgets.QCheckBox("Add my first site now")
        self.add_now.setChecked(True)
        dl.addWidget(self.add_now)
        dl.addStretch(1)
        self.addPage(done)

    @property
    def mode(self) -> str:
        b = self.group.checkedButton()
        return b.property("mode") if b else "basic"


# ============================================================ add a site
class VendorPage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("What kind of router is at the site?")
        self.setSubTitle("This is the router or gateway with the VPN server you want to connect to.")
        lay = QtWidgets.QVBoxLayout(self)
        self.group = QtWidgets.QButtonGroup(self)
        self.existing = QtWidgets.QComboBox()
        unifi_gws = [n for n, g in wiz.m.store.gateways().items() if g.get("vendor") == "unifi"]
        if unifi_gws:
            rb = QtWidgets.QRadioButton("A UniFi router I've already added")
            rb.setProperty("vendor", "existing")
            self.group.addButton(rb)
            lay.addWidget(rb)
            self.existing.addItems(unifi_gws)
            row = QtWidgets.QHBoxLayout()
            row.addSpacing(24)
            row.addWidget(self.existing, 1)
            lay.addLayout(row)
            rb.setChecked(True)
        for key, name, desc in VENDORS:
            rb = QtWidgets.QRadioButton(name)
            f = rb.font()
            f.setBold(True)
            rb.setFont(f)
            rb.setProperty("vendor", key)
            self.group.addButton(rb)
            lay.addWidget(rb)
            d = _title(f"<span style='color:palette(placeholder-text)'>{desc}</span>")
            d.setContentsMargins(24, 0, 0, 6)
            lay.addWidget(d)
            if key == "unifi" and not unifi_gws:
                rb.setChecked(True)
        self.use_file = QtWidgets.QCheckBox("UniFi, but I'd rather pick a downloaded WireGuard file")
        lay.addWidget(self.use_file)
        lay.addStretch(1)
        self.group.buttonToggled.connect(lambda *_: self.use_file.setVisible(self.choice == "unifi"))

    @property
    def choice(self) -> str:
        b = self.group.checkedButton()
        return b.property("vendor") if b else "unifi"

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        self.wiz.vendor = "unifi" if self.choice == "existing" else self.choice
        self.wiz.conf_path = None
        if self.choice == "existing":
            self.wiz.gateway = self.existing.currentText()
            self.wiz.sites = []
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        c = self.choice
        if c == "existing":
            return P_SITE
        if c == "unifi" and not self.use_file.isChecked():
            return P_LOGIN
        return P_FILE


class LoginPage(_Page):
    """UniFi console address + API key; tested before moving on."""

    def __init__(self, wiz: "_GatewayMixin", final: bool = False):
        super().__init__()
        self.wiz = wiz
        self.final = final
        self.setTitle("Connect to your UniFi console")
        self.setSubTitle("Do this while you're at the site (or can otherwise reach the router).")
        lay = QtWidgets.QVBoxLayout(self)
        help_ = QtWidgets.QTextBrowser()
        help_.setHtml(UNIFI_KEY_HELP)
        help_.setOpenExternalLinks(True)
        help_.setMaximumHeight(190)
        lay.addWidget(help_)
        form = QtWidgets.QFormLayout()
        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("192.168.1.1")
        self.key = QtWidgets.QLineEdit()
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key.setPlaceholderText("paste the API key here")
        show = QtWidgets.QCheckBox("Show")
        show.toggled.connect(lambda v: self.key.setEchoMode(
            QtWidgets.QLineEdit.Normal if v else QtWidgets.QLineEdit.Password))
        keyrow = QtWidgets.QHBoxLayout()
        keyrow.addWidget(self.key, 1)
        keyrow.addWidget(show)
        form.addRow("Router address", self.address)
        form.addRow("API key", keyrow)
        lay.addLayout(form)
        self.more = QtWidgets.QCheckBox("More options (only if catflap asks for them)")
        lay.addWidget(self.more)
        self.more_box = QtWidgets.QWidget()
        mf = QtWidgets.QFormLayout(self.more_box)
        mf.setContentsMargins(24, 0, 0, 0)
        self.user = QtWidgets.QLineEdit()
        self.user.setPlaceholderText("a LOCAL UniFi admin, not your UI.com login")
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.legacy = QtWidgets.QCheckBox("Self-hosted UniFi Network Application (no UniFi OS)")
        mf.addRow("Local admin username", self.user)
        mf.addRow("Password", self.pw)
        mf.addRow(self.legacy)
        self.more_box.setVisible(False)
        self.more.toggled.connect(self.more_box.setVisible)
        lay.addWidget(self.more_box)
        lay.addStretch(1)
        self.address.textChanged.connect(self.completeChanged)
        self.key.textChanged.connect(self.completeChanged)
        self.user.textChanged.connect(self.completeChanged)

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return bool(self.address.text().strip()) and bool(self.key.text().strip() or self.user.text().strip())

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        url = normalize_url(self.address.text())
        host = urlsplit(url).hostname or ""
        m = self.wiz.m
        existing = next((n for n, g in m.store.gateways().items() if g.get("url") == url), None)
        name = existing or m.unique_gateway_name(f"unifi-{host}")

        def work():
            fp = fetch_fingerprint(url) if url.startswith("https://") else None
            m.add_gateway(name, "unifi", url, api_key=self.key.text().strip() or None,
                          username=self.user.text().strip() or None, password=self.pw.text() or None,
                          fingerprint=fp, options={"legacy_controller": True} if self.legacy.isChecked() else {})
            try:
                return m.list_sites(name)
            except Exception:
                if not existing:
                    m.remove_gateway(name)
                raise
        try:
            sites = run_blocking(self, f"Connecting to {host}…", work)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Couldn't connect", friendly_error(e, host))
            return False
        self.wiz.gateway, self.wiz.sites = name, sites
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return -1 if self.final else P_SITE


class SitePage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Which site?")
        self.setSubTitle("Your UniFi console can manage several sites. Pick the one to reach.")
        lay = QtWidgets.QVBoxLayout(self)
        self.combo = QtWidgets.QComboBox()
        lay.addWidget(self.combo)
        lay.addStretch(1)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        if not self.wiz.sites:
            try:
                self.wiz.sites = run_blocking(self, "Looking up sites…",
                                              lambda: self.wiz.m.list_sites(self.wiz.gateway))
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(self, "Couldn't connect", friendly_error(e))
                self.wiz.sites = []
        self.combo.clear()
        for s in self.wiz.sites:
            self.combo.addItem(s.name, s)
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return self.combo.count() > 0

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        self.wiz.site = self.combo.currentData()
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_SERVER


class ServerPage(_Page):
    """Checks the WireGuard server exists and was actually created (problem #1)."""

    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.ok = False
        self.setTitle("Checking your router's VPN server")
        lay = QtWidgets.QVBoxLayout(self)
        self.text = QtWidgets.QTextBrowser()
        lay.addWidget(self.text, 1)
        self.again = QtWidgets.QPushButton("Check again")
        self.again.clicked.connect(self.check)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.again)
        row.addStretch(1)
        lay.addLayout(row)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        self.check()

    def check(self) -> None:
        self.ok = False
        self.completeChanged.emit()
        self.again.setEnabled(False)
        self.text.setHtml("<p>Checking…</p>")
        m, gw, site = self.wiz.m, self.wiz.gateway, self.wiz.site

        def work():
            d = m.driver(gw)
            srv = d.get_wireguard_server(site)
            wan = d.get_wan_info(site)
            nets = d.list_networks(site)
            return srv, wan, nets
        run_bg(work, self._checked)

    def _checked(self, res, err) -> None:
        self.again.setEnabled(True)
        if err is not None:
            msg = friendly_error(err)
            if "No WireGuard VPN server" in msg or "not fully created" in msg:
                self.text.setHtml(UNIFI_SERVER_HELP)
            else:
                self.text.setHtml(f"<p><b>Something went wrong:</b></p><p>{msg}</p>"
                                  "<p>Fix it and click <b>Check again</b>.</p>")
            return
        srv, wan, nets = res
        if not (srv.enabled and srv.public_key and srv.port):
            self.text.setHtml(UNIFI_SERVER_HELP)
            return
        self.wiz.networks = nets
        html = (f"<p>✓ Found the WireGuard VPN server <b>{srv.name}</b> (port {srv.port}).</p>")
        if wan.ddns_hostname:
            html += f"<p>✓ The site will be reached at <b>{wan.ddns_hostname}</b>.</p>"
        elif wan.wan_ip:
            html += (f"<p>The site will be reached at its internet address <b>{wan.wan_ip}</b>. "
                     "If that address changes now and then, set up Dynamic DNS on the router "
                     "and add the name later (Advanced view → Edit).</p>")
        if wan.wan_ip and netutil.is_cgnat(wan.wan_ip):
            html += ("<p style='color:#D92D20'><b>Heads up:</b> this site's internet provider "
                     "shares one public address between many customers (\"CGNAT\"). A VPN can't "
                     "reach the site from outside until the provider gives you a public IP "
                     "address. You can continue, but connecting will likely fail.</p>")
        elif wan.wan_ip and netutil.is_private(wan.wan_ip):
            html += ("<p style='color:#B45309'><b>Note:</b> there's another router in front of "
                     f"this one. Ask whoever manages it to forward UDP port {srv.port} to your "
                     "UniFi router.</p>")
        self.text.setHtml(html)
        self.ok = True
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return self.ok

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_NETWORKS


class NetworksPage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("What do you want to reach?")
        self.setSubTitle("Tick the networks at the site you need. Everything else, like web "
                         "browsing, keeps using your normal internet.")
        lay = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QListWidget()
        self.list.itemChanged.connect(lambda _i: self.completeChanged.emit())
        lay.addWidget(self.list, 1)
        self.note = _title("")
        lay.addWidget(self.note)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        self.list.clear()
        local = _local_routes(self.wiz.m)
        clash_any = False
        for i, n in enumerate(self.wiz.networks):
            clash = any(netutil.overlaps(n.cidr, r) for r in local)
            clash_any |= clash
            vlan = f", VLAN {n.vlan}" if n.vlan and n.vlan != 1 else ""
            text = f"{n.name}   ({n.cidr}{vlan})"
            if clash:
                text += "   ⚠ same addresses as the network you're on now"
            it = QtWidgets.QListWidgetItem(text)
            it.setData(Qt.UserRole, n.cidr)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if i == 0 and not clash else Qt.Unchecked)
            self.list.addItem(it)
        self.note.setText(
            "<small>⚠ A network that uses the same addresses as the one you're connected to now "
            "can't be reached while you're here (common with home routers that all use "
            "192.168.1.x). It will work from other places.</small>" if clash_any else
            "<small>Not sure? The first one is usually your main network.</small>")
        self.completeChanged.emit()

    def chosen(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return bool(self.chosen())

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        self.wiz.routes = self.chosen()
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_NAME


class FilePage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Pick the WireGuard file")
        lay = QtWidgets.QVBoxLayout(self)
        self.help = _title("")
        lay.addWidget(self.help)
        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit()
        self.path.setPlaceholderText("…/Downloads/something.conf")
        browse = QtWidgets.QPushButton("Choose file…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        form = QtWidgets.QFormLayout()
        self.router = QtWidgets.QLineEdit()
        self.router.setPlaceholderText("e.g. 192.168.1.1")
        form.addRow("Router's address at the site", self.router)
        lay.addLayout(form)
        lay.addWidget(_title("<small>This is the address you'd type in a browser at the site to "
                             "reach the router. catflap routes that whole network (e.g. "
                             "192.168.1.x) through the VPN, and nothing else.</small>"))
        self.more = QtWidgets.QCheckBox("More options")
        lay.addWidget(self.more)
        self.more_box = QtWidgets.QWidget()
        mf = QtWidgets.QFormLayout(self.more_box)
        mf.setContentsMargins(24, 0, 0, 0)
        self.extra = QtWidgets.QLineEdit()
        self.extra.setPlaceholderText("e.g. 192.168.20.0/24, 10.0.0.0/24")
        self.endpoint = QtWidgets.QLineEdit()
        self.endpoint.setPlaceholderText("e.g. myhome.ddns.net (optional)")
        mf.addRow("More networks to reach", self.extra)
        mf.addRow("Site's internet name (DDNS)", self.endpoint)
        self.more_box.setVisible(False)
        self.more.toggled.connect(self.more_box.setVisible)
        lay.addWidget(self.more_box)
        lay.addStretch(1)
        for w in (self.path, self.router, self.extra):
            w.textChanged.connect(self.completeChanged)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        self.help.setText(FILE_HELP.get(self.wiz.vendor, FILE_HELP["generic"])
                          + "<br><br>Then choose the downloaded file here. Use a separate file "
                            "for each computer, so each one can be removed on its own later.")

    def _browse(self) -> None:
        start = str(Path.home() / "Downloads")
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "WireGuard file", start,
                                                     "WireGuard configuration (*.conf);;All files (*)")
        if f:
            self.path.setText(f)

    def routes(self) -> list[str] | None:
        out = []
        try:
            ip = ipaddress.ip_address(self.router.text().strip())
            out.append(str(ipaddress.ip_network(f"{ip}/24", strict=False)))
            for r in self.extra.text().replace(";", ",").split(","):
                if r.strip():
                    out.append(str(ipaddress.ip_network(r.strip(), strict=False)))
        except ValueError:
            return None
        return out

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return bool(self.path.text().strip()) and self.routes() is not None

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        path = self.path.text().strip()
        if not os.path.isfile(path):
            QtWidgets.QMessageBox.warning(self, "catflap", "That file doesn't exist.")
            return False
        try:
            get_driver_class(self.wiz.vendor)(GatewayConfig(name="", vendor=self.wiz.vendor), {}).parse_config(path)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "catflap", "That doesn't look like a WireGuard "
                                          f"configuration file:\n\n{friendly_error(e)}")
            return False
        self.wiz.conf_path = path
        self.wiz.routes = self.routes() or []
        self.wiz.endpoint = self.endpoint.text().strip() or None
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_NAME


class NamePage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Name and colour")
        self.setSubTitle("The colour is used for the tray icon while you're connected to this site.")
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("e.g. Home, Office, Mum's house")
        self.name.textChanged.connect(self.completeChanged)
        form.addRow("Name", self.name)
        swatches = QtWidgets.QHBoxLayout()
        self.colors = QtWidgets.QButtonGroup(self)
        for c in PALETTE:
            b = QtWidgets.QToolButton()
            b.setCheckable(True)
            b.setFixedSize(30, 30)
            b.setStyleSheet(f"QToolButton {{ background:{c}; border-radius:15px; border:2px solid transparent; }}"
                            f"QToolButton:checked {{ border:3px solid palette(text); }}")
            b.setProperty("color", c)
            self.colors.addButton(b)
            swatches.addWidget(b)
        swatches.addStretch(1)
        form.addRow("Colour", swatches)
        lay.addLayout(form)
        self.connect_now = QtWidgets.QCheckBox("Connect as soon as it's set up")
        lay.addWidget(self.connect_now)
        lay.addStretch(1)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        if not self.name.text():
            site = getattr(self.wiz, "site", None)
            self.name.setText(site.name if site and site.name.lower() != "default" else "")
        used = {p.color for p in self.wiz.m.store.profiles().values()}
        for b in self.colors.buttons():
            if b.property("color") not in used:
                b.setChecked(True)
                break
        else:
            self.colors.buttons()[0].setChecked(True)

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return bool(self.name.text().strip())

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        w, m = self.wiz, self.wiz.m
        label = self.name.text().strip()
        if any(p.display.lower() == label.lower() for p in m.store.profiles().values()):
            QtWidgets.QMessageBox.warning(self, "catflap", f"You already have a site called "
                                          f"'{label}'. Pick another name.")
            return False
        name = m.unique_profile_name(label)
        color = self.colors.checkedButton().property("color")
        if w.conf_path:
            fn = lambda: m.import_conf(w.conf_path, name, vendor=w.vendor, routes=w.routes,  # noqa: E731
                                       endpoint=w.endpoint, color=color, label=label)
        else:
            fn = lambda: m.provision(w.gateway, name, w.routes, site=w.site.id,  # noqa: E731
                                     color=color, label=label)
        try:
            w.result = run_blocking(self, f"Setting up {label}…", fn)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Setup didn't finish", friendly_error(e))
            return False
        w.connect_after = self.connect_now.isChecked()
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_DONE


class DonePage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Done!")
        lay = QtWidgets.QVBoxLayout(self)
        self.text = _title("")
        lay.addWidget(self.text)
        lay.addStretch(1)
        self.setFinalPage(True)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        p = self.wiz.result.profile
        self.text.setText(
            f"<p><b>{p.display}</b> is ready.</p>"
            "<p>catflap set it up safely:</p><ul>"
            f"<li>only {', '.join(p.routes)} goes through the VPN</li>"
            "<li>your internet and DNS stay as they are</li>"
            "<li>it never connects by itself, and keeps the connection alive while you use it</li>"
            "</ul>"
            "<p>Connect any time from the catflap window or by clicking the cat in the tray.</p>")

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return -1


class _GatewayMixin:
    m: Manager


class SiteWizard(_Wizard, _GatewayMixin):
    def __init__(self, manager: Manager, parent=None):
        super().__init__(parent)
        self.m = manager
        self.vendor = "unifi"
        self.gateway: str | None = None
        self.sites: list = []
        self.site = None
        self.networks: list = []
        self.routes: list[str] = []
        self.conf_path: str | None = None
        self.endpoint: str | None = None
        self.result = None
        self.connect_after = False
        self.setWindowTitle("Add a site")
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.setPixmap(QtWidgets.QWizard.LogoPixmap, app_icon().pixmap(56, 56))
        self.setOption(QtWidgets.QWizard.NoBackButtonOnLastPage, True)
        self.resize(720, 560)
        self.pages = {
            P_VENDOR: VendorPage(self), P_LOGIN: LoginPage(self), P_SITE: SitePage(self),
            P_SERVER: ServerPage(self), P_NETWORKS: NetworksPage(self), P_FILE: FilePage(self),
            P_NAME: NamePage(self), P_DONE: DonePage(self),
        }
        for pid, page in self.pages.items():
            self.setPage(pid, page)
        self.setStartId(P_VENDOR)


class GatewayWizard(_Wizard, _GatewayMixin):
    """Add a UniFi router (for automatic setup) without adding a site yet."""

    def __init__(self, manager: Manager, parent=None):
        super().__init__(parent)
        self.m = manager
        self.gateway: str | None = None
        self.sites: list = []
        self.setWindowTitle("Add a gateway")
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.setPixmap(QtWidgets.QWizard.LogoPixmap, app_icon().pixmap(56, 56))
        intro = QtWidgets.QWizardPage()
        intro.setTitle("Add a gateway")
        il = QtWidgets.QVBoxLayout(intro)
        il.addWidget(_title(
            "<p>Adding a gateway lets catflap set up sites on it automatically. That works with "
            "<b>UniFi</b> today.</p><p>For <b>TP-Link Omada</b>, <b>Alta Labs</b> and other "
            "routers you don't need a gateway: use <b>Add a site</b> and pick the WireGuard file "
            "the router gives you.</p>"))
        il.addStretch(1)
        self.addPage(intro)
        self.login = LoginPage(self, final=True)
        self.addPage(self.login)
        self.resize(700, 560)
