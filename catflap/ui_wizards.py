"""Step-by-step wizards for people who have never set up a VPN.

* :class:`FirstRunWizard` - welcome, pick Basic or Advanced (with live previews),
  offer to add the first site.
* :class:`SiteWizard` - "I want to reach a site", with exact steps per brand:
  - UniFi: automatic, through the UniFi cloud (one account API key from
    unifi.ui.com, pick the console) or directly on the local network;
  - TP-Link Omada / other routers: catflap creates this computer's keys, you add
    it as a WireGuard peer on the router and type a few values back;
  - Alta Labs / other: pick a WireGuard file downloaded from the router.
* :class:`GatewayWizard` - add a UniFi console without adding a site yet.

Whatever the route, the result is a WireGuard connection with catflap's safe
defaults. All work goes through :class:`catflap.core.Manager`; network calls run
in the background behind a "please wait" dialog.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from . import keys, netutil
from .core import Manager
from .drivers import get_driver_class
from .drivers.base import GatewayConfig
from .http import fetch_fingerprint
from .ui_common import (app_icon, PALETTE, QtWidgets, Qt, friendly_error,
                        run_bg, run_blocking)

_Wizard = QtWidgets.QWizard if QtWidgets else object
_Page = QtWidgets.QWizardPage if QtWidgets else object


# ---------------------------------------------------------------- what each brand offers
VENDORS = [
    ("unifi", "UniFi (Ubiquiti)", "Automatic setup through the UniFi cloud (unifi.ui.com): you "
                                  "paste one API key and pick your console. Works from anywhere."),
    ("omada", "TP-Link Omada", "You add this computer as a WireGuard peer in your Omada "
                               "controller and type a few details here. Step-by-step help included."),
    ("alta", "Alta Labs", "You download a WireGuard file for this computer from manage.alta.inc "
                          "and pick it here."),
    ("generic", "Another WireGuard router", "Use a WireGuard file from your router, or type its "
                                            "details."),
]

UNIFI_CLOUD_HELP = """
<p><b>Get a UniFi cloud API key</b> (one time, about a minute, from any computer):</p>
<ol>
<li>Sign in at <a href="https://unifi.ui.com">unifi.ui.com</a> with the UniFi account that
    <b>owns</b> the console. An admin who was only invited to the console can't create this key.</li>
<li>Open <b>Settings</b> (bottom of the left menu) → <b>API Keys</b>. On some accounts it's
    called <b>API</b> in the left menu.</li>
<li>Click <b>Create API Key</b>, name it <i>catflap</i>, and <b>copy</b> it. It's shown only once.</li>
<li>Paste it below.</li>
</ol>
<p><small>This is an <b>account</b> key: it covers every console you own, and catflap reaches
them through Ubiquiti's cloud, so you don't need to be at the site. Your console must show as
<b>Online</b> on unifi.ui.com (Remote Management on). The key is saved only on this computer, in
your keyring.<br>
Not the same as the <i>console</i> key under UniFi Network → Settings → Control Plane →
Integrations: that one only works on the console's own network (choose <i>Directly</i> below).
</small></p>
"""

UNIFI_DIRECT_HELP = """
<p><b>Direct access</b> only works while this computer is on the same network as the console
(for example, at the site). In UniFi Network go to <b>Settings → Control Plane →
Integrations</b>, click <b>Create API Key</b>, copy it, and paste it below with the console's
address on that network.</p>
"""

UNIFI_SERVER_HELP = """
<b>Your UniFi console doesn't have a WireGuard VPN server yet.</b> Create one (one time):
<ol>
<li>Open your console on <a href="https://unifi.ui.com">unifi.ui.com</a> → <b>Network</b>.</li>
<li>Go to <b>Settings → VPN → VPN Server</b>.</li>
<li>Click <b>Create New</b> and choose <b>WireGuard</b>. Keep the suggested port (51820).</li>
<li>Click <b>Create</b> / <b>Apply Changes</b>. This step is easy to miss: without it the VPN
    never works.</li>
<li>Come back here and click <b>Check again</b>.</li>
</ol>
"""

FILE_HELP = {
    "unifi": """<p><b>Get a WireGuard file from UniFi:</b></p><ol>
<li>Open your console on <a href="https://unifi.ui.com">unifi.ui.com</a> → <b>Network</b> →
    <b>Settings → VPN → VPN Server</b>.</li>
<li>Open your <b>WireGuard</b> server (create it and click <b>Apply Changes</b> first if there
    isn't one).</li>
<li>Click <b>Add Client</b>, name it after this computer, and <b>Download</b> its configuration
    file.</li></ol>""",
    "alta": """<p><b>Get a WireGuard file from Alta Labs</b> (Route10, Control 1.0q or later):</p><ol>
<li>Sign in at <a href="https://manage.alta.inc">manage.alta.inc</a> and open your site.</li>
<li>Select your <b>Route10</b> and open its <b>VPN</b> tab.</li>
<li>Turn on the <b>WireGuard server</b> if it's off, and save.</li>
<li>Add a <b>user profile</b> for this computer (one per device), then download or export its
    WireGuard configuration file (.conf).</li></ol>""",
    "omada": """<p>Omada usually doesn't give you a file; choose <b>Type the details</b> instead.
If you do have a WireGuard .conf for this computer, pick it here.</p>""",
    "generic": """<p>In your router's VPN settings, create a <b>WireGuard client / peer</b> for this
computer and download its configuration (a file ending in <b>.conf</b>).</p>""",
}

MANUAL_HELP = {
    "omada": """<p><b>Add this computer to your Omada gateway</b> (Omada Controller 5.9 or later,
or Omada Central; menu names can differ slightly between versions):</p><ol>
<li>In your Omada controller, open the site and go to <b>Settings → VPN → WireGuard</b>.</li>
<li>If there's no WireGuard VPN yet: click <b>Create New</b>, give it a name, keep
    <b>Listen Port 51820</b>, set <b>Local IP Address</b> to a free private range just for the VPN
    (for example <i>10.250.1.1</i>), and save. Copy its <b>Public Key</b>: that's the
    <i>router's public key</i> below.</li>
<li>Open the <b>Peers</b> tab → <b>Create New</b>: name it after this computer, choose the
    WireGuard VPN above, paste <b>this computer's public key</b> (shown below), set
    <b>Allowed Address</b> to this computer's VPN address with /32 (for example
    <i>10.250.1.2/32</i>), set <b>Persistent Keepalive</b> to 25, and save.</li>
<li>Fill in the details below. The site's internet address is your Dynamic DNS name
    (<b>Settings → Services → Dynamic DNS</b>) or, if you don't use one, the gateway's WAN IP.</li>
</ol>""",
    "generic": """<p><b>Add this computer to your router as a WireGuard peer:</b></p><ol>
<li>In your router's WireGuard settings, add a peer/client and paste <b>this computer's public
    key</b> (below).</li>
<li>Give it a VPN address (for example <i>10.250.1.2/32</i>) and copy the router's own
    <b>public key</b> and <b>listen port</b>.</li>
<li>Fill in the details below.</li></ol>""",
}

NETWORK_HELP = {
    "unifi": "UniFi Network → <b>Settings → Networks</b> lists each network with its range "
             "(for example <i>192.168.1.0/24</i>).",
    "omada": "Omada controller → <b>Settings → Wired Networks → LAN</b> lists each network with its "
             "gateway/subnet (for example <i>192.168.0.1/24</i> means <i>192.168.0.0/24</i>).",
    "alta": "manage.alta.inc → your site → <b>Networks</b> shows each network's subnet.",
    "generic": "Your router's LAN / network settings show the range (for example "
               "<i>192.168.1.0/24</i>).",
}

P_VENDOR, P_LOGIN, P_SITE, P_SERVER, P_NETWORKS, P_FILE, P_NAME, P_DONE = range(8)
P_CONSOLE, P_METHOD, P_MANUAL, P_SITE_NETS = range(8, 12)


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


# ============================================================ add a site
class VendorPage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("What kind of router is at the site?")
        self.setSubTitle("The router or gateway at the place you want to reach.")
        lay = QtWidgets.QVBoxLayout(self)
        self.group = QtWidgets.QButtonGroup(self)
        self.existing = QtWidgets.QComboBox()
        unifi_gws = [n for n, g in wiz.m.store.gateways().items() if g.get("vendor") == "unifi"]
        if unifi_gws:
            rb = QtWidgets.QRadioButton("A UniFi console I've already added")
            rb.setProperty("vendor", "existing")
            self.group.addButton(rb)
            lay.addWidget(rb)
            for n in unifi_gws:
                opts = wiz.m.store.gateways()[n].get("options") or {}
                self.existing.addItem(opts.get("console_name") or n, n)
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
        self.use_file = QtWidgets.QCheckBox("UniFi, but I'd rather use a downloaded WireGuard file")
        lay.addWidget(self.use_file)
        lay.addStretch(1)
        self.group.buttonToggled.connect(lambda *_: self.use_file.setVisible(self.choice == "unifi"))

    @property
    def choice(self) -> str:
        b = self.group.checkedButton()
        return b.property("vendor") if b else "unifi"

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        w = self.wiz
        w.vendor = "unifi" if self.choice == "existing" else self.choice
        w.conf_path = None
        w.manual_cfg = None
        w.file_routes = []
        if self.choice == "existing":
            w.gateway = self.existing.currentData()
            w.sites = []
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        c = self.choice
        if c == "existing":
            return P_SITE
        if c == "unifi":
            return P_FILE if self.use_file.isChecked() else P_LOGIN
        if c == "alta":
            return P_FILE
        return P_METHOD


class UnifiAccessPage(_Page):
    """UniFi: cloud (account API key, pick a console) or direct (address + console key)."""

    def __init__(self, wiz, final: bool = False):
        super().__init__()
        self.wiz = wiz
        self.final = final
        self.consoles: list[dict] = []
        self.setTitle("Connect to UniFi")
        self.setSubTitle("catflap uses your UniFi account to find your console and set up the VPN.")
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.cloud = QtWidgets.QRadioButton("Through the UniFi cloud (recommended, works from anywhere)")
        self.direct = QtWidgets.QRadioButton("Directly (only while I'm on the console's network)")
        self.cloud.setChecked(True)
        row.addWidget(self.cloud)
        row.addWidget(self.direct)
        lay.addLayout(row)
        self.help = QtWidgets.QTextBrowser()
        self.help.setOpenExternalLinks(True)
        lay.addWidget(self.help, 1)
        form = QtWidgets.QFormLayout()
        self.key = QtWidgets.QLineEdit()
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key.setPlaceholderText("paste the API key here")
        show = QtWidgets.QCheckBox("Show")
        show.toggled.connect(lambda v: self.key.setEchoMode(
            QtWidgets.QLineEdit.Normal if v else QtWidgets.QLineEdit.Password))
        keyrow = QtWidgets.QHBoxLayout()
        keyrow.addWidget(self.key, 1)
        keyrow.addWidget(show)
        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("the console's address on that network, e.g. 192.168.1.1")
        self.address_label = QtWidgets.QLabel("Console address")
        form.addRow("API key", keyrow)
        form.addRow(self.address_label, self.address)
        lay.addLayout(form)
        for w in (self.key, self.address):
            w.textChanged.connect(self.completeChanged)
        self.cloud.toggled.connect(self._mode)
        self._mode()

    def _mode(self) -> None:
        cloud = self.cloud.isChecked()
        self.help.setHtml(UNIFI_CLOUD_HELP if cloud else UNIFI_DIRECT_HELP)
        self.address.setVisible(not cloud)
        self.address_label.setVisible(not cloud)
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        if not self.key.text().strip():
            return False
        return self.cloud.isChecked() or bool(self.address.text().strip())

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        m, key = self.wiz.m, self.key.text().strip()
        if self.cloud.isChecked():
            from .drivers.unifi import list_cloud_consoles
            try:
                self.consoles = run_blocking(self, "Looking up your UniFi consoles…",
                                             lambda: list_cloud_consoles(key))
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(self, "Couldn't sign in to UniFi", friendly_error(e))
                return False
            if not self.consoles:
                QtWidgets.QMessageBox.warning(
                    self, "No consoles found",
                    "That key works, but no UniFi consoles are linked to the account. Check that "
                    "the console shows as Online on unifi.ui.com and that you signed in as its owner.")
                return False
            self.wiz.cloud_key = key
            if len(self.consoles) == 1 and self.final:
                c = self.consoles[0]
                self.wiz.gateway = m.add_unifi_cloud_gateway(key, c["id"], c["name"])
            return True
        url = normalize_url(self.address.text())
        host = urlsplit(url).hostname or ""
        existing = next((n for n, g in m.store.gateways().items() if g.get("url") == url), None)
        name = existing or m.unique_gateway_name(f"unifi-{host}")

        def work():
            fp = fetch_fingerprint(url) if url.startswith("https://") else None
            m.add_gateway(name, "unifi", url, api_key=key, fingerprint=fp)
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
        self.consoles = []
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        if self.cloud.isChecked() and not (self.final and len(self.consoles) == 1):
            return P_CONSOLE
        return -1 if self.final else P_SITE


class ConsolePage(_Page):
    def __init__(self, wiz, final: bool = False):
        super().__init__()
        self.wiz = wiz
        self.final = final
        self.setTitle("Which console?")
        self.setSubTitle("These are the UniFi consoles on your account.")
        lay = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(lambda _r: self.completeChanged.emit())
        lay.addWidget(self.list, 1)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        self.list.clear()
        for c in self.wiz.access_page.consoles:
            label = c["name"] + (f"  ({c['model']})" if c.get("model") else "")
            if not c.get("online", True):
                label += "  - offline"
            it = QtWidgets.QListWidgetItem(label)
            it.setData(Qt.UserRole, c)
            self.list.addItem(it)
        self.list.setCurrentRow(0)

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return self.list.currentItem() is not None

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        c = self.list.currentItem().data(Qt.UserRole)
        m = self.wiz.m
        name = m.add_unifi_cloud_gateway(self.wiz.cloud_key, c["id"], c["name"])
        try:
            sites = run_blocking(self, f"Connecting to {c['name']} through the UniFi cloud…",
                                 lambda: m.list_sites(name))
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Couldn't reach the console", friendly_error(e))
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




class MethodPage(_Page):
    """Omada / other: type the details (catflap makes the keys) or pick a WireGuard file."""

    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("How do you want to add it?")
        lay = QtWidgets.QVBoxLayout(self)
        self.manual = QtWidgets.QRadioButton("Type the details (catflap creates this computer's keys)")
        self.file = QtWidgets.QRadioButton("Pick a WireGuard file (.conf) from the router")
        for rb, desc in ((self.manual, "You add this computer as a WireGuard peer on the router "
                                       "and copy a few values back here. Step-by-step help on the "
                                       "next page."),
                         (self.file, "If your router gave you a configuration file for this "
                                     "computer.")):
            f = rb.font()
            f.setBold(True)
            rb.setFont(f)
            lay.addWidget(rb)
            d = _title(f"<span style='color:palette(placeholder-text)'>{desc}</span>")
            d.setContentsMargins(24, 0, 0, 8)
            lay.addWidget(d)
        self.note = _title("")
        lay.addWidget(self.note)
        lay.addStretch(1)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        omada = self.wiz.vendor == "omada"
        (self.manual if omada else self.file).setChecked(True)
        self.note.setText(
            "<small>Automatic Omada setup through the Omada Open API is being added; until then "
            "this takes about two minutes by hand.</small>" if omada else "")

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_MANUAL if self.manual.isChecked() else P_FILE


class FilePage(_Page):
    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Pick the WireGuard file")
        lay = QtWidgets.QVBoxLayout(self)
        self.help = QtWidgets.QTextBrowser()
        self.help.setOpenExternalLinks(True)
        lay.addWidget(self.help, 1)
        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit()
        self.path.setPlaceholderText("…/Downloads/something.conf")
        browse = QtWidgets.QPushButton("Choose file…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        form = QtWidgets.QFormLayout()
        self.endpoint = QtWidgets.QLineEdit()
        self.endpoint.setPlaceholderText("optional, e.g. myhome.ddns.net")
        form.addRow("Site's internet name (if the file has an IP that changes)", self.endpoint)
        lay.addLayout(form)
        self.path.textChanged.connect(self.completeChanged)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        self.help.setHtml(FILE_HELP.get(self.wiz.vendor, FILE_HELP["generic"])
                          + "<p>Then choose the downloaded file below. Use a separate file for "
                            "each computer, so each one can be removed on its own later.</p>")

    def _browse(self) -> None:
        start = str(Path.home() / "Downloads")
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "WireGuard file", start,
                                                     "WireGuard configuration (*.conf);;All files (*)")
        if f:
            self.path.setText(f)

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return bool(self.path.text().strip())

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        path = self.path.text().strip()
        if not os.path.isfile(path):
            QtWidgets.QMessageBox.warning(self, "catflap", "That file doesn't exist.")
            return False
        try:
            cfg = get_driver_class(self.wiz.vendor)(GatewayConfig(name="", vendor=self.wiz.vendor), {}).parse_config(path)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "catflap", "That doesn't look like a WireGuard "
                                          f"configuration file:\n\n{friendly_error(e)}")
            return False
        self.wiz.conf_path = path
        self.wiz.manual_cfg = None
        self.wiz.endpoint = self.endpoint.text().strip() or None
        self.wiz.file_routes = [r for r in cfg.allowed_ips
                                if not netutil.is_default_route(r) and netutil.net(r).version == 4]
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_SITE_NETS


class ManualPage(_Page):
    """Routers without config files: catflap makes the key pair, the user adds the peer."""

    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.priv, self.pub = keys.generate_keypair()
        self.setTitle("Add this computer to the router")
        lay = QtWidgets.QVBoxLayout(self)
        self.help = QtWidgets.QTextBrowser()
        self.help.setOpenExternalLinks(True)
        lay.addWidget(self.help, 1)
        mine = QtWidgets.QHBoxLayout()
        mine.addWidget(QtWidgets.QLabel("This computer's public key:"))
        pub = QtWidgets.QLineEdit(self.pub)
        pub.setReadOnly(True)
        mine.addWidget(pub, 1)
        copy = QtWidgets.QPushButton("Copy")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.pub))
        mine.addWidget(copy)
        lay.addLayout(mine)
        form = QtWidgets.QFormLayout()
        self.server_key = QtWidgets.QLineEdit()
        self.server_key.setPlaceholderText("the router's WireGuard public key (44 characters, ends in =)")
        self.endpoint = QtWidgets.QLineEdit()
        self.endpoint.setPlaceholderText("DDNS name or public IP, e.g. cabin.tplinkdns.com")
        self.port = QtWidgets.QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(51820)
        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("e.g. 10.250.1.2 (the Allowed Address you gave this computer)")
        form.addRow("Router's public key", self.server_key)
        form.addRow("Site's internet address", self.endpoint)
        form.addRow("Port", self.port)
        form.addRow("This computer's VPN address", self.address)
        lay.addLayout(form)
        for w in (self.server_key, self.endpoint, self.address):
            w.textChanged.connect(self.completeChanged)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        self.help.setHtml(MANUAL_HELP.get(self.wiz.vendor, MANUAL_HELP["generic"]))

    def isComplete(self) -> bool:  # noqa: N802 (Qt API)
        return all(w.text().strip() for w in (self.server_key, self.endpoint, self.address))

    def validatePage(self) -> bool:  # noqa: N802 (Qt API)
        try:
            cfg = self.wiz.m.manual_peer(self.priv, self.server_key.text(), self.endpoint.text(),
                                         self.port.value(), self.address.text().split("/")[0])
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "catflap", friendly_error(e))
            return False
        self.wiz.manual_cfg = cfg
        self.wiz.conf_path = None
        self.wiz.file_routes = []
        return True

    def nextId(self) -> int:  # noqa: N802 (Qt API)
        return P_SITE_NETS


class SiteNetworksPage(_Page):
    """Which networks to reach, when they can't be read from the router's API."""

    def __init__(self, wiz: "SiteWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle("What do you want to reach at the site?")
        self.setSubTitle("Only these go through the VPN. Web browsing and everything else keeps "
                         "using your normal internet.")
        lay = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QListWidget()
        self.list.itemChanged.connect(lambda _i: self.completeChanged.emit())
        lay.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        self.new = QtWidgets.QLineEdit()
        self.new.setPlaceholderText("a network at the site, e.g. 192.168.1.0/24")
        add = QtWidgets.QPushButton("Add")
        add.clicked.connect(self._add)
        self.new.returnPressed.connect(self._add)
        row.addWidget(self.new, 1)
        row.addWidget(add)
        lay.addLayout(row)
        self.help = _title("")
        lay.addWidget(self.help)

    def initializePage(self) -> None:  # noqa: N802 (Qt API)
        self.list.clear()
        for r in self.wiz.file_routes:
            self._add_item(r, checked=True)
        self.help.setText("<small>Where to find it: " + NETWORK_HELP.get(self.wiz.vendor, NETWORK_HELP["generic"])
                          + "</small>")
        self.completeChanged.emit()

    def _add_item(self, cidr: str, checked: bool) -> None:
        local = _local_routes(self.wiz.m)
        text = cidr + ("   ⚠ same addresses as the network you're on now" if any(
            netutil.overlaps(cidr, r) for r in local) else "")
        it = QtWidgets.QListWidgetItem(text)
        it.setData(Qt.UserRole, cidr)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list.addItem(it)

    def _add(self) -> None:
        raw = self.new.text().strip()
        if not raw:
            return
        if "/" not in raw:
            raw += "/24"
        try:
            cidr = str(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "catflap", f"'{self.new.text()}' isn't a network "
                                          "range like 192.168.1.0/24.")
            return
        self._add_item(cidr, checked=True)
        self.new.clear()
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
        if w.manual_cfg is not None:
            fn = lambda: m.import_peer(w.manual_cfg, name, vendor=w.vendor, routes=w.routes,  # noqa: E731
                                       color=color, label=label)
        elif w.conf_path:
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


class SiteWizard(_Wizard):
    def __init__(self, manager: Manager, parent=None):
        super().__init__(parent)
        self.m = manager
        self.vendor = "unifi"
        self.gateway: str | None = None
        self.cloud_key: str | None = None
        self.sites: list = []
        self.site = None
        self.networks: list = []
        self.routes: list[str] = []
        self.file_routes: list[str] = []
        self.conf_path: str | None = None
        self.manual_cfg = None
        self.endpoint: str | None = None
        self.result = None
        self.connect_after = False
        self.setWindowTitle("Add a site")
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.setPixmap(QtWidgets.QWizard.LogoPixmap, app_icon().pixmap(56, 56))
        self.setOption(QtWidgets.QWizard.NoBackButtonOnLastPage, True)
        self.resize(760, 640)
        self.access_page = UnifiAccessPage(self)
        self.pages = {
            P_VENDOR: VendorPage(self), P_LOGIN: self.access_page, P_CONSOLE: ConsolePage(self),
            P_SITE: SitePage(self), P_SERVER: ServerPage(self), P_NETWORKS: NetworksPage(self),
            P_METHOD: MethodPage(self), P_FILE: FilePage(self), P_MANUAL: ManualPage(self),
            P_SITE_NETS: SiteNetworksPage(self), P_NAME: NamePage(self), P_DONE: DonePage(self),
        }
        for pid, page in self.pages.items():
            self.setPage(pid, page)
        self.setStartId(P_VENDOR)


class GatewayWizard(_Wizard):
    """Add a UniFi console (cloud or direct) without adding a site yet."""

    def __init__(self, manager: Manager, parent=None):
        super().__init__(parent)
        self.m = manager
        self.gateway: str | None = None
        self.cloud_key: str | None = None
        self.sites: list = []
        self.setWindowTitle("Add a gateway")
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.setPixmap(QtWidgets.QWizard.LogoPixmap, app_icon().pixmap(56, 56))
        intro = QtWidgets.QWizardPage()
        intro.setTitle("Add a gateway")
        il = QtWidgets.QVBoxLayout(intro)
        il.addWidget(_title(
            "<p>Adding a gateway lets catflap set up sites on it automatically. That works with "
            "<b>UniFi</b> today, through the UniFi cloud.</p><p>For <b>TP-Link Omada</b>, "
            "<b>Alta Labs</b> and other routers you don't need a gateway: use <b>Add a site</b>, "
            "which walks you through it.</p>"))
        il.addStretch(1)
        self.setPage(0, intro)
        self.access_page = UnifiAccessPage(self, final=True)
        self.login = self.access_page
        self.setPage(P_LOGIN, self.access_page)
        self.setPage(P_CONSOLE, ConsolePage(self, final=True))
        self.resize(760, 640)
