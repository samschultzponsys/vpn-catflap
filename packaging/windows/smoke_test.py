"""Real-Windows check of the tunnel backend, run by the release workflow on a
GitHub Windows runner (an administrator account, so the permission prompt is
granted automatically).

Downloads WireGuard's MSI with the app's own code, sets up a dummy site (which
extracts the engine into Program Files\\CatFlap\\WireGuard), connects,
disconnects and removes it, and checks the engine is gone afterwards. The
WireGuard app (window, tray icon, manager service) must never appear.
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from catflap import keys, wgconf  # noqa: E402
from catflap.model import Profile  # noqa: E402
from catflap.platform.base import run as run_cmd  # noqa: E402
from catflap.platform.windows import WindowsBackend, engine_dir  # noqa: E402


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        sys.exit(1)


def main():
    b = WindowsBackend(run_cmd)
    check(not b.engine.exists(), "no engine before the test")
    b.install_wireguard()
    check(b.downloaded_msi() is not None, f"MSI downloaded: {b.downloaded_msi()}")

    priv, _ = keys.generate_keypair()
    _, peer_pub = keys.generate_keypair()
    cfg = wgconf.parse_text(f"[Interface]\nPrivateKey = {priv}\nAddress = 10.255.77.2/32\n[Peer]\n"
                            f"PublicKey = {peer_pub}\nAllowedIPs = 10.255.77.0/24\n"
                            "Endpoint = 192.0.2.1:51820\nPersistentKeepalive = 25\n")
    p = Profile(name="smoke", label="Smoke test", ifname="cf-smoke", vendor="generic",
                addresses=["10.255.77.2/32"], peer_public_key=peer_pub, endpoint_host="192.0.2.1",
                endpoint_port=51820, routes=["10.255.77.0/24"])
    b.install(p, cfg)
    check(b.engine.exists(), f"engine extracted to {b.engine}")
    check((engine_dir() / "wg.exe").exists(), "wg.exe extracted")
    check(b._service_state(p) == "STOPPED", "tunnel service created, stopped (on demand)")
    check(b._service_program(p) == str(b.engine), f"service runs CatFlap's engine ({b._service_program(p)})")

    b.up(p)
    for _ in range(40):
        if b.status(p).active:
            break
        time.sleep(0.5)
    check(b.status(p).active, "connected (service RUNNING)")
    show = subprocess.run([str(engine_dir() / "wg.exe"), "show", "cf-smoke"], capture_output=True, text=True)
    print(show.stdout or show.stderr)
    check("cf-smoke" in show.stdout, "wg show sees the tunnel")
    procs = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    svcs = subprocess.run(["sc", "query", "WireGuardManager"], capture_output=True, text=True)
    check(svcs.returncode != 0, "no WireGuard manager service (no WireGuard app or tray icon)")
    print(f"wireguard.exe processes: {procs.count('wireguard.exe')}")

    b.down(p)
    time.sleep(2)
    check(not b.status(p).active, "disconnected")
    b.remove(p)
    time.sleep(1)
    check(b._service_state(p) is None, "tunnel service removed")
    check(not engine_dir().exists(), "engine removed with the last site")
    print("Windows smoke test passed")


if __name__ == "__main__":
    main()
