"""catflap command-line interface.

Designed to be scriptable (Ansible): every command works non-interactively,
``--json`` gives machine-readable output, secrets are read from a prompt,
stdin or an environment variable (never from argv), and exit codes are:
0 ok, 1 error, 2 usage error, 3 checks failed (pre-flight/health).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from typing import Any

from . import checks, netutil
from .bundle import BundleError
from .core import CatflapError, Manager
from .drivers import DRIVERS, DriverError
from .http import HttpError, TlsPinError, fetch_fingerprint
from .platform.base import BackendError
from .safety import SafetyError
from .version import __version__

EXIT_OK, EXIT_ERR, EXIT_USAGE, EXIT_CHECKS = 0, 1, 2, 3


class Out:
    def __init__(self, as_json: bool):
        self.json = as_json
        self.data: dict[str, Any] = {}

    def say(self, msg: str = "") -> None:
        if not self.json:
            print(msg)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value

    def finish(self) -> None:
        if self.json:
            print(json.dumps(self.data, indent=2, default=lambda o: getattr(o, "__dict__", str(o))))


def read_secret(prompt: str, env: str, from_stdin: bool) -> str | None:
    if from_stdin:
        return sys.stdin.readline().strip() or None
    if os.environ.get(env):
        return os.environ[env]
    if sys.stdin.isatty():
        return getpass.getpass(prompt) or None
    return None


def print_findings(out: Out, findings: list[checks.Finding]) -> None:
    marks = {"error": "✗", "warning": "!", "info": "i", "ok": "✓"}
    for f in findings:
        tag = f" [#{f.problem}]" if f.problem else ""
        out.say(f"  {marks.get(f.level, '-')} {f.message}{tag}")
    out.put("findings", [f.to_dict() for f in findings])


def ask_networks(nets: list) -> list[str]:
    print("Which site networks should go through the tunnel?")
    for i, n in enumerate(nets, 1):
        vlan = f" VLAN {n.vlan}" if n.vlan else ""
        print(f"  [{i}] {n.name:<24} {n.cidr:<18}{vlan}")
    raw = input("Numbers, comma separated (e.g. 1,3): ").strip()
    picked = []
    for tok in raw.replace(" ", "").split(","):
        if tok.isdigit() and 1 <= int(tok) <= len(nets):
            picked.append(nets[int(tok) - 1].cidr)
    return picked


def fmt_age(ts: int | None) -> str:
    if not ts:
        return "?"
    s = int(time.time() - ts)
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago"


# ============================================================ commands
def cmd_version(m: Manager, a, out: Out) -> int:
    from .update import detect_install
    info = detect_install()
    out.say(f"catflap {__version__}")
    out.say(f"installed via: {info.method} ({info.detail})" if info.detail else f"installed via: {info.method}")
    out.put("version", __version__)
    out.put("install", info.__dict__)
    return EXIT_OK


def cmd_update(m: Manager, a, out: Out) -> int:
    from . import update
    st = update.check(m.store.settings(), force=True)
    m.store.save()
    out.put("update", {"current": st.current, "latest": st.latest.version if st.latest else None,
                       "available": st.available, "install": st.install.__dict__,
                       "instructions": st.instructions, "error": st.error})
    if st.error:
        out.say(st.error)
        return EXIT_ERR
    if not st.available:
        out.say(f"catflap {st.current} is up to date.")
        return EXIT_OK
    out.say(f"catflap {st.latest.version} is available (you have {st.current}, installed via "
            f"{st.install.method}).")
    out.say(f"Release notes: {st.latest.url}")
    if a.check or not st.install.automatic:
        out.say("To update:")
        for line in st.instructions:
            out.say(f"  {line}")
        return EXIT_OK
    if not a.yes and sys.stdin.isatty():
        if input("Update now? [y/N] ").strip().lower() != "y":
            return EXIT_OK
    done, msg = update.apply_update(st)
    out.say(msg)
    return EXIT_OK if done else EXIT_ERR


def cmd_gateway_add(m: Manager, a, out: Out) -> int:
    fp = a.fingerprint
    if a.tofu:
        fp = fetch_fingerprint(a.url)
        out.say(f"Gateway certificate SHA-256 fingerprint:\n  {fp}")
        out.say("Compare it with the one in the gateway UI/browser before trusting it.")
        if sys.stdin.isatty() and not a.yes and input("Trust this certificate? [y/N] ").strip().lower() != "y":
            return EXIT_USAGE
    api_key = None
    if a.vendor != "generic":
        api_key = read_secret("API key (leave empty to skip): ", "CATFLAP_API_KEY", a.api_key_stdin)
    password = None
    if a.username:
        password = read_secret(f"Password for local user {a.username}: ", "CATFLAP_PASSWORD",
                               a.password_stdin)
    opts = {"legacy_controller": True} if a.legacy_controller else {}
    gw = m.add_gateway(a.name, a.vendor, a.url or "", api_key=api_key, username=a.username,
                       password=password, fingerprint=fp, ca_file=a.ca_file,
                       insecure=a.insecure, options=opts)
    out.say(f"Gateway '{gw.name}' ({DRIVERS[gw.vendor].display_name}) saved. "
            f"Secrets stored in: {m.secrets.backend_name}")
    out.put("gateway", gw.to_dict() | {"name": gw.name})
    return EXIT_OK


def cmd_gateway_list(m: Manager, a, out: Out) -> int:
    gws = m.store.gateways()
    out.put("gateways", gws)
    if not gws:
        out.say("No gateways yet. Add one: catflap gateway add <name> --vendor unifi --url https://<ip> --tofu")
    for n, g in gws.items():
        out.say(f"{n:<16} {DRIVERS[g['vendor']].display_name:<34} {g.get('url', '')}")
    return EXIT_OK


def cmd_gateway_remove(m: Manager, a, out: Out) -> int:
    m.remove_gateway(a.name)
    out.say(f"Removed gateway '{a.name}' and its stored credentials.")
    return EXIT_OK


def cmd_gateway_set_key(m: Manager, a, out: Out) -> int:
    what = "password" if a.password else "api_key"
    v = read_secret(f"New {what.replace('_', ' ')}: ",
                    "CATFLAP_PASSWORD" if a.password else "CATFLAP_API_KEY", a.stdin)
    if not v:
        out.say("Nothing entered.")
        return EXIT_USAGE
    m.set_gateway_secret(a.name, what, v)
    out.say(f"Updated the {what.replace('_', ' ')} for '{a.name}'.")
    return EXIT_OK


def cmd_gateway_sites(m: Manager, a, out: Out) -> int:
    sites = m.list_sites(a.name)
    out.put("sites", [s.__dict__ for s in sites])
    for s in sites:
        out.say(f"{s.name:<24} ref={s.ref:<12} id={s.id}")
    return EXIT_OK


def cmd_gateway_networks(m: Manager, a, out: Out) -> int:
    site, nets = m.list_networks(a.name, a.site)
    out.put("site", site.__dict__)
    out.put("networks", [n.__dict__ for n in nets])
    out.say(f"Networks at {site.name}:")
    local = m.backend.local_network() if a.check_local else None
    for n in nets:
        flag = ""
        if local and any(netutil.overlaps(n.cidr, r.dest) for r in local.routes
                         if not netutil.is_default_route(r.dest)):
            flag = "  ! overlaps a network you're on now"
        out.say(f"  {n.name:<24} {n.cidr:<18} VLAN {n.vlan if n.vlan else '-':<5}{flag}")
    return EXIT_OK


def cmd_gateway_clients(m: Manager, a, out: Out) -> int:
    d = m.driver(a.name)
    s = d.find_site(a.site)
    srv = d.get_wireguard_server(s)
    cl = d.list_clients(s, srv)
    mine = {p.client_id: p.name for p in m.store.profiles().values() if p.gateway == a.name}
    out.put("server", srv.__dict__)
    out.put("clients", [c.__dict__ | {"local_profile": mine.get(c.id)} for c in cl])
    out.say(f"WireGuard server '{srv.name}' port {srv.port} subnet {srv.subnet}")
    for c in cl:
        tag = f"  <- this PC: profile '{mine[c.id]}'" if c.id in mine else ""
        out.say(f"  {c.name:<36} {c.address:<18}{tag}")
    return EXIT_OK


def cmd_provision(m: Manager, a, out: Out) -> int:
    nets = a.network or []
    if not nets:
        site, available = m.list_networks(a.gateway, a.site)
        if not sys.stdin.isatty():
            out.say("Choose networks with --network (name, CIDR or VLAN). Available: "
                    + ", ".join(f"{n.name} ({n.cidr})" for n in available))
            return EXIT_USAGE
        nets = ask_networks(available)
        if not nets:
            out.say("No networks chosen.")
            return EXIT_USAGE
    res = m.provision(a.gateway, a.name, nets, site=a.site, device=a.device, endpoint=a.endpoint,
                      server=a.server, dns_domain=a.dns_domain, dns_servers=a.dns_server,
                      replace=a.replace, install=not a.no_install, color=a.color)
    return _report_created(res, out)


def cmd_import(m: Manager, a, out: Out) -> int:
    res = m.import_conf(a.file, a.name, vendor=a.vendor, routes=a.route, networks=a.network,
                        gateway=a.gateway, site=a.site, endpoint=a.endpoint,
                        dns_domain=a.dns_domain, dns_servers=a.dns_server, replace=a.replace,
                        install=not a.no_install, color=a.color)
    return _report_created(res, out)


def _report_created(res, out: Out) -> int:
    p = res.profile
    for msg in res.messages:
        out.say(f"  - {msg}")
    for w in p.warnings:
        if w not in res.messages:
            out.say(f"  ! {w}")
    out.say(f"Profile '{p.name}' ready: interface {p.ifname}, routes {', '.join(p.routes)}, "
            f"endpoint {p.endpoint_host}:{p.endpoint_port}.")
    out.say(f"Connect with: catflap up {p.name}")
    out.put("profile", p.to_dict())
    out.put("messages", res.messages)
    return EXIT_OK


def cmd_list(m: Manager, a, out: Out) -> int:
    profiles = m.store.profiles()
    stat = {}
    for n, p in profiles.items():
        try:
            stat[n] = m.backend.status(p)
        except BackendError:
            stat[n] = None
    out.put("profiles", {n: p.to_dict() | {"active": bool(stat[n] and stat[n].active)}
                         for n, p in profiles.items()})
    if not profiles:
        out.say("No sites yet. Use 'catflap provision' or 'catflap import'.")
    by_gw: dict[str, list] = {}
    for p in profiles.values():
        by_gw.setdefault(f"{p.gateway or 'imported'} ({p.vendor})", []).append(p)
    for gw, ps in sorted(by_gw.items()):
        out.say(gw)
        for p in ps:
            st = stat[p.name]
            state = "connected" if st and st.active else "disconnected"
            out.say(f"  {p.name:<16} {state:<13} {p.ifname:<15} {', '.join(p.routes)}")
    return EXIT_OK


def cmd_show(m: Manager, a, out: Out) -> int:
    p = m.store.profile(a.name)
    out.put("profile", p.to_dict())
    for k, v in p.to_dict().items():
        if v not in (None, "", [], False):
            out.say(f"{k:<16} {v}")
    return EXIT_OK


def cmd_set(m: Manager, a, out: Out) -> int:
    p = m.update_profile(a.name, endpoint=a.endpoint, routes=a.route, color=a.color,
                         dns_domain=a.dns_domain, dns_servers=a.dns_server, clear_dns=a.no_dns,
                         ping_target=a.ping_target)
    out.say(f"Updated '{p.name}'.")
    out.put("profile", p.to_dict())
    return EXIT_OK


def cmd_apply(m: Manager, a, out: Out) -> int:
    names = list(m.store.profiles()) if a.all else [a.name]
    for n in names:
        m.apply(n)
        out.say(f"Re-created the connection for '{n}' with safe settings.")
    return EXIT_OK


def cmd_check(m: Manager, a, out: Out) -> int:
    f = m.preflight(a.name, online_checks=not a.offline)
    print_findings(out, f)
    return EXIT_CHECKS if checks.has_errors(f) else EXIT_OK


def cmd_up(m: Manager, a, out: Out) -> int:
    out.say(f"Connecting '{a.name}'...")
    res = m.connect(a.name, force=a.force, keep_on_failure=a.keep_on_failure)
    print_findings(out, res.findings)
    out.put("connected", res.ok)
    out.say("Connected." if res.ok else "Not connected.")
    return EXIT_OK if res.ok else EXIT_CHECKS


def cmd_down(m: Manager, a, out: Out) -> int:
    m.disconnect(a.name)
    out.say(f"Disconnected '{a.name}'.")
    return EXIT_OK


def cmd_status(m: Manager, a, out: Out) -> int:
    st = m.status(a.name)
    out.put("status", {n: s.__dict__ for n, s in st.items()})
    for n, s in st.items():
        if s.active:
            extra = f"rx {s.rx_bytes} B, tx {s.tx_bytes} B"
            if s.last_handshake:
                extra += f", handshake {fmt_age(s.last_handshake)}"
            out.say(f"{n:<16} connected     {extra}")
        else:
            out.say(f"{n:<16} disconnected")
    return EXIT_OK


def cmd_remove(m: Manager, a, out: Out) -> int:
    for msg in m.remove(a.name, revoke=a.revoke or a.cmd == "revoke"):
        out.say(msg)
    return EXIT_OK


def _passphrase(confirm: bool) -> str | None:
    if os.environ.get("CATFLAP_PASSPHRASE"):
        return os.environ["CATFLAP_PASSPHRASE"]
    if not sys.stdin.isatty():
        raise CatflapError("set CATFLAP_PASSPHRASE or run interactively to enter a passphrase")
    p = getpass.getpass("Export passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != p:
        raise CatflapError("passphrases do not match")
    return p


def cmd_export(m: Manager, a, out: Out) -> int:
    pw = None if a.plaintext else _passphrase(confirm=True)
    text = m.export(pw, gateways_only=a.gateways_only)
    fd = os.open(a.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    out.say(f"Exported to {a.file}" + (" (UNENCRYPTED: contains API keys and private keys!)"
                                       if a.plaintext else " (encrypted)."))
    return EXIT_OK


def cmd_import_bundle(m: Manager, a, out: Out) -> int:
    from .bundle import is_encrypted
    with open(a.file, encoding="utf-8") as f:
        text = f.read()
    pw = _passphrase(confirm=False) if is_encrypted(text) else None
    msgs = m.import_bundle(text, pw, overwrite=a.overwrite, install=not a.no_install)
    for msg in msgs:
        out.say(msg)
    out.put("messages", msgs)
    return EXIT_OK


def cmd_suggest_subnet(m: Manager, a, out: Out) -> int:
    used = {p.vpn_subnet for p in m.store.profiles().values() if p.vpn_subnet}
    sub = netutil.suggest_vpn_subnet(a.index)
    clash = [u for u in used if netutil.overlaps(u, sub)]
    out.say(sub + (f"  (overlaps {', '.join(clash)} already in use)" if clash else ""))
    out.put("subnet", sub)
    return EXIT_OK


def cmd_tray(m: Manager, a, out: Out) -> int:
    from .tray import main as tray_main
    return tray_main(["--minimized"] if a.minimized else [])


# ============================================================ parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="catflap", description="Safe one-click WireGuard remote access to your sites.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-V", "--version", action="version", version=f"catflap {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def dns_opts(p):
        p.add_argument("--dns-domain", help="resolve ONLY this internal domain via the tunnel (e.g. office.lan)")
        p.add_argument("--dns-server", action="append", help="DNS server for --dns-domain (repeatable)")

    def common_new(p):
        p.add_argument("--endpoint", help="public hostname of the site (DDNS) instead of the raw WAN IP")
        p.add_argument("--color", help="tray colour for this connection, e.g. '#2E9E44'")
        p.add_argument("--replace", action="store_true", help="replace an existing profile/client of the same name")
        p.add_argument("--no-install", action="store_true", help="only store the profile; don't create the OS connection")
        dns_opts(p)

    p = sub.add_parser("version", help="show version and how catflap was installed")
    p.set_defaults(fn=cmd_version)
    p = sub.add_parser("update", help="check for updates and update")
    p.add_argument("--check", action="store_true", help="only check, don't install")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(fn=cmd_update)

    g = sub.add_parser("gateway", help="manage gateways (UniFi, Omada, Alta Labs, generic)")
    gs = g.add_subparsers(dest="gcmd", required=True, metavar="ACTION")
    p = gs.add_parser("add", help="add or update a gateway and store its credentials")
    p.add_argument("name")
    p.add_argument("--vendor", required=True, choices=sorted(DRIVERS))
    p.add_argument("--url", help="console URL, e.g. https://192.168.1.1")
    p.add_argument("--api-key-stdin", action="store_true", help="read the API key from stdin")
    p.add_argument("--username", help="LOCAL admin user for APIs the key can't access")
    p.add_argument("--password-stdin", action="store_true")
    tls = p.add_mutually_exclusive_group()
    tls.add_argument("--tofu", action="store_true", help="fetch and pin the gateway's certificate fingerprint")
    tls.add_argument("--fingerprint", help="pin this SHA-256 certificate fingerprint")
    tls.add_argument("--ca-file", help="verify with this CA bundle")
    tls.add_argument("--insecure", action="store_true", help="do not verify TLS (not recommended)")
    p.add_argument("--legacy-controller", action="store_true", help="self-hosted UniFi Network Application (no UniFi OS)")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(fn=cmd_gateway_add)
    p = gs.add_parser("list", help="list gateways")
    p.set_defaults(fn=cmd_gateway_list)
    p = gs.add_parser("remove", help="remove a gateway and its stored credentials")
    p.add_argument("name")
    p.set_defaults(fn=cmd_gateway_remove)
    p = gs.add_parser("set-key", help="replace the stored API key (or --password)")
    p.add_argument("name")
    p.add_argument("--password", action="store_true")
    p.add_argument("--stdin", action="store_true")
    p.set_defaults(fn=cmd_gateway_set_key)
    p = gs.add_parser("sites", help="list sites on a gateway")
    p.add_argument("name")
    p.set_defaults(fn=cmd_gateway_sites)
    p = gs.add_parser("networks", help="list a site's networks")
    p.add_argument("name")
    p.add_argument("--site")
    p.add_argument("--check-local", action="store_true", help="flag networks that overlap where you are now")
    p.set_defaults(fn=cmd_gateway_networks)
    p = gs.add_parser("clients", help="list VPN clients (devices) on a site's WireGuard server")
    p.add_argument("name")
    p.add_argument("--site")
    p.set_defaults(fn=cmd_gateway_clients)

    p = sub.add_parser("provision", help="create a client for THIS device via the gateway API")
    p.add_argument("gateway")
    p.add_argument("name", help="profile name, e.g. home")
    p.add_argument("--site")
    p.add_argument("--network", action="append", help="site network to route (name, CIDR or VLAN; repeatable)")
    p.add_argument("--device", help="device name recorded on the gateway (default: hostname)")
    p.add_argument("--server", help="WireGuard server name if the site has several")
    common_new(p)
    p.set_defaults(fn=cmd_provision)

    p = sub.add_parser("import", help="import a downloaded WireGuard .conf safely")
    p.add_argument("file")
    p.add_argument("name")
    p.add_argument("--vendor", default="generic", choices=sorted(DRIVERS))
    p.add_argument("--route", action="append", help="CIDR to route through the tunnel (repeatable)")
    p.add_argument("--gateway", help="configured gateway to look networks up on")
    p.add_argument("--site")
    p.add_argument("--network", action="append", help="site network to route (needs --gateway)")
    common_new(p)
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("list", help="list site profiles grouped by gateway")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show", help="show a profile")
    p.add_argument("name")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("set", help="change a profile (endpoint, routes, colour, DNS)")
    p.add_argument("name")
    p.add_argument("--endpoint")
    p.add_argument("--route", action="append", help="replace the routed CIDRs (repeatable)")
    p.add_argument("--color")
    p.add_argument("--ping-target")
    dns_opts(p)
    p.add_argument("--no-dns", action="store_true", help="remove split DNS")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("apply", help="re-create the OS connection from the profile (idempotent)")
    ag = p.add_mutually_exclusive_group(required=True)
    ag.add_argument("name", nargs="?")
    ag.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("check", help="run pre-flight checks without connecting")
    p.add_argument("name")
    p.add_argument("--offline", action="store_true", help="skip public-IP lookup and ping")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("up", help="connect (pre-flight, connect, health check)")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="connect despite pre-flight errors")
    p.add_argument("--keep-on-failure", action="store_true", help="leave the tunnel up if the health check fails")
    p.set_defaults(fn=cmd_up)
    p = sub.add_parser("down", help="disconnect")
    p.add_argument("name")
    p.set_defaults(fn=cmd_down)
    p = sub.add_parser("status", help="connection status")
    p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("remove", help="delete a profile and its connection")
    p.add_argument("name")
    p.add_argument("--revoke", action="store_true", help="also delete the client on the gateway")
    p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("revoke", help="revoke this device's client on the gateway and remove the profile")
    p.add_argument("name")
    p.set_defaults(fn=cmd_remove, revoke=True)
    p = sub.add_parser("export", help="export gateways, profiles and keys (encrypted) to move to another PC")
    p.add_argument("file")
    p.add_argument("--gateways-only", action="store_true", help="only gateways + API keys (for a second PC)")
    p.add_argument("--plaintext", action="store_true", help="don't encrypt (contains secrets!)")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("import-bundle", help="import a catflap export file")
    p.add_argument("file")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-install", action="store_true")
    p.set_defaults(fn=cmd_import_bundle)
    p = sub.add_parser("suggest-subnet", help="suggest a per-site VPN subnet (10.250.N.0/28)")
    p.add_argument("index", type=int, help="site number 0-255")
    p.set_defaults(fn=cmd_suggest_subnet)
    p = sub.add_parser("tray", help="start the tray app")
    p.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    p.set_defaults(fn=cmd_tray)
    return ap


def main(argv: list[str] | None = None, manager: Manager | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    out = Out(a.json)
    if (hasattr(os, "geteuid") and os.geteuid() == 0 and manager is None
            and not os.environ.get("CATFLAP_ALLOW_ROOT") and a.cmd not in ("version",)):
        print("catflap: error: don't run catflap as root or with sudo. Run it as your normal "
              "desktop user; NetworkManager/polkit authorizes the connection changes, and your "
              "profiles and keyring live in your own account (problem #13). "
              "Set CATFLAP_ALLOW_ROOT=1 to override.", file=sys.stderr)
        return EXIT_ERR
    try:
        m = manager or Manager()
        rc = a.fn(m, a, out)
    except (CatflapError, DriverError, BackendError, SafetyError, BundleError, TlsPinError,
            HttpError, KeyError, ValueError, OSError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        out.put("error", msg)
        if out.json:
            out.finish()
        else:
            print(f"catflap: error: {msg}", file=sys.stderr)
        return EXIT_ERR
    except KeyboardInterrupt:
        return 130
    out.finish()
    return rc


if __name__ == "__main__":
    sys.exit(main())
