"""Parse and render wg-quick style .conf files.

Only the subset that matters for a client is supported: one [Interface] and one
[Peer]. Multiple-peer files are rejected with a clear error rather than being
half-imported.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import PeerConfig


class ConfError(ValueError):
    pass


_SECTION_RE = re.compile(r"^\[(\w+)\]$")


def _split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_endpoint(value: str) -> tuple[str, int]:
    value = value.strip()
    m = re.match(r"^\[([0-9a-fA-F:]+)\]:(\d+)$", value)  # [v6]:port
    if m:
        return m.group(1), int(m.group(2))
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not host:
        raise ConfError(f"invalid Endpoint {value!r}: expected host:port")
    return host, int(port)


def format_endpoint(host: str, port: int) -> str:
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def parse_text(text: str) -> PeerConfig:
    sections: list[tuple[str, dict[str, list[str]]]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _SECTION_RE.match(line)
        if m:
            sections.append((m.group(1).lower(), {}))
            continue
        if "=" not in line:
            raise ConfError(f"line {lineno}: expected 'Key = Value', got {raw!r}")
        if not sections:
            raise ConfError(f"line {lineno}: key outside of a section")
        key, _, value = line.partition("=")
        sections[-1][1].setdefault(key.strip().lower(), []).append(value.strip())

    ifaces = [s for n, s in sections if n == "interface"]
    peers = [s for n, s in sections if n == "peer"]
    if len(ifaces) != 1:
        raise ConfError(f"expected exactly one [Interface] section, found {len(ifaces)}")
    if len(peers) != 1:
        raise ConfError(f"expected exactly one [Peer] section, found {len(peers)}")
    i, p = ifaces[0], peers[0]

    def one(sec: dict[str, list[str]], key: str, required: bool = True) -> str | None:
        vals = sec.get(key)
        if not vals:
            if required:
                raise ConfError(f"missing required key {key!r}")
            return None
        return vals[-1]

    def many(sec: dict[str, list[str]], key: str) -> list[str]:
        out: list[str] = []
        for v in sec.get(key, []):
            out.extend(_split_list(v))
        return out

    host, port = parse_endpoint(one(p, "endpoint") or "")
    ka = one(p, "persistentkeepalive", required=False)
    mtu = one(i, "mtu", required=False)
    return PeerConfig(
        private_key=one(i, "privatekey") or "",
        addresses=many(i, "address"),
        dns=many(i, "dns"),
        mtu=int(mtu) if mtu else None,
        peer_public_key=one(p, "publickey") or "",
        preshared_key=one(p, "presharedkey", required=False),
        endpoint_host=host,
        endpoint_port=port,
        allowed_ips=many(p, "allowedips"),
        keepalive=int(ka) if ka and ka.lower() != "off" else None,
    )


def parse_file(path: str | Path) -> PeerConfig:
    return parse_text(Path(path).read_text(encoding="utf-8"))


def render(cfg: PeerConfig) -> str:
    lines = ["[Interface]", f"PrivateKey = {cfg.private_key}"]
    if cfg.addresses:
        lines.append(f"Address = {', '.join(cfg.addresses)}")
    if cfg.dns:
        lines.append(f"DNS = {', '.join(cfg.dns)}")
    if cfg.mtu:
        lines.append(f"MTU = {cfg.mtu}")
    lines += ["", "[Peer]", f"PublicKey = {cfg.peer_public_key}"]
    if cfg.preshared_key:
        lines.append(f"PresharedKey = {cfg.preshared_key}")
    lines.append(f"AllowedIPs = {', '.join(cfg.allowed_ips)}")
    lines.append(f"Endpoint = {format_endpoint(cfg.endpoint_host, cfg.endpoint_port)}")
    if cfg.keepalive:
        lines.append(f"PersistentKeepalive = {cfg.keepalive}")
    return "\n".join(lines) + "\n"
