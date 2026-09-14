from __future__ import annotations

import asyncio
import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Settings


@dataclass
class PeerSnapshot:
    public_key: str
    name: str
    allowed_ips: str
    endpoint: str | None
    latest_handshake: int  # unix ts, 0 if never
    rx: int
    tx: int
    online: bool


@dataclass
class DumpResult:
    peers: list[PeerSnapshot]
    scraped_at: datetime


COMMENT_NAME_RE = re.compile(
    r"#\s*(?:Client|client|Name|name|Peer|peer)[:\s]+(.+)", re.IGNORECASE
)
PUBKEY_RE = re.compile(r"PublicKey\s*=\s*(\S+)")


def _parse_name_map(conf_text: str) -> dict[str, str]:
    """Map peer public keys to names from wg config comments."""
    mapping: dict[str, str] = {}
    pending_name: str | None = None
    for line in conf_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = COMMENT_NAME_RE.match(stripped)
        if m:
            pending_name = m.group(1).strip()
            continue
        if stripped.startswith("#"):
            if pending_name is None and not stripped.startswith("#["):
                maybe = stripped.lstrip("#").strip()
                if maybe and "=" not in maybe and not maybe.startswith("["):
                    pending_name = maybe
            continue
        if stripped.startswith("[Peer]"):
            continue
        pm = PUBKEY_RE.match(stripped)
        if pm and pending_name:
            mapping[pm.group(1)] = pending_name
            pending_name = None
    return mapping


def _parse_clients_table(raw: str) -> dict[str, str]:
    """Amnezia clientsTable JSON → {public_key: clientName}."""
    mapping: dict[str, str] = {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return mapping
    if not isinstance(data, list):
        return mapping
    for item in data:
        if not isinstance(item, dict):
            continue
        key = item.get("clientId") or item.get("publicKey")
        user = item.get("userData") or {}
        name = user.get("clientName") if isinstance(user, dict) else None
        if key and name:
            mapping[str(key)] = str(name)
    return mapping


def _looks_like_key(value: str) -> bool:
    return value.endswith("=") and len(value) >= 40


def parse_awg_dump(
    dump: str,
    name_map: dict[str, str],
    online_threshold_sec: int,
    now: datetime | None = None,
) -> list[PeerSnapshot]:
    """
    Parse `awg show all dump`.

    Classic WireGuard peer line:
      public_key  psk  endpoint  allowed_ips  handshake  rx  tx  keepalive

    AmneziaWG peer line (iface prefix on every row):
      iface  public_key  psk  endpoint  allowed_ips  handshake  rx  tx  keepalive

    Interface rows have many more columns (Jc/Jmin/…) — skip them.
    """
    now = now or datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    peers: list[PeerSnapshot] = []

    for line in dump.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue

        # Amnezia format: iface + peer fields (exactly 9 cols typical)
        if (
            len(parts) <= 12
            and not _looks_like_key(parts[0])
            and _looks_like_key(parts[1])
        ):
            public_key = parts[1]
            endpoint_raw = parts[3]
            allowed_ips = parts[4]
            hs_i, rx_i, tx_i = 5, 6, 7
        # Classic WG peer line
        elif _looks_like_key(parts[0]) and len(parts) <= 10:
            public_key = parts[0]
            endpoint_raw = parts[2]
            allowed_ips = parts[3]
            hs_i, rx_i, tx_i = 4, 5, 6
        else:
            # Interface / junk
            continue

        endpoint = None if endpoint_raw in {"(none)", ""} else endpoint_raw
        try:
            handshake = int(parts[hs_i])
        except ValueError:
            handshake = 0
        try:
            rx = int(parts[rx_i])
            tx = int(parts[tx_i])
        except ValueError:
            rx, tx = 0, 0

        online = handshake > 0 and (now_ts - handshake) <= online_threshold_sec
        name = name_map.get(public_key) or public_key[:8]
        peers.append(
            PeerSnapshot(
                public_key=public_key,
                name=name,
                allowed_ips=allowed_ips,
                endpoint=endpoint,
                latest_handshake=handshake,
                rx=rx,
                tx=tx,
                online=online,
            )
        )
    return peers


async def _run(cmd: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{err}")
    return stdout.decode(errors="replace")


def _demo_key(name: str) -> str:
    """Base64-shaped stand-in so demo data parses like real keys (44 chars)."""
    return (name + "x" * 43)[:43] + "="


def demo_dump() -> tuple[str, str, str]:
    """Synthetic data for local UI development."""
    now = int(datetime.now(timezone.utc).timestamp())
    phone, laptop, tablet, work = (
        _demo_key(n) for n in ("phone", "laptop", "tablet", "work")
    )
    conf = f"""
[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEE=
Address = 10.8.1.1/24

[Peer]
PublicKey = {phone}
AllowedIPs = 10.8.1.2/32
"""
    clients = json.dumps(
        [
            {"clientId": phone, "userData": {"clientName": "phone"}},
            {"clientId": laptop, "userData": {"clientName": "laptop"}},
            {"clientId": tablet, "userData": {"clientName": "tablet"}},
            {"clientId": work, "userData": {"clientName": "work"}},
        ]
    )
    peers = [
        f"awg0\t{phone}\t(none)\t1.2.3.4:51820\t10.8.1.2/32\t{now - 20}\t{12_400_000_000}\t{3_100_000_000}\toff",
        f"awg0\t{laptop}\t(none)\t5.6.7.8:44102\t10.8.1.3/32\t{now - 40}\t{48_200_000_000}\t{9_800_000_000}\toff",
        f"awg0\t{tablet}\t(none)\t(none)\t10.8.1.4/32\t{now - 900}\t{2_100_000_000}\t{450_000_000}\toff",
        f"awg0\t{work}\t(none)\t9.9.9.9:12345\t10.8.1.5/32\t{now - 15}\t{86_000_000_000}\t{22_000_000_000}\toff",
    ]
    dump = "awg0\tifacekey\tifacepub\t51820\t6\t10\t50\n" + "\n".join(peers)
    return dump, conf, clients


class AwgClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._name_map: dict[str, str] = {}
        self._name_map_at: float = 0.0

    async def fetch(self, *, force_refresh_names: bool = False) -> DumpResult:
        if self.settings.awg_mode == "demo":
            dump, conf, clients_raw = demo_dump()
            name_map = _parse_name_map(conf)
            name_map.update(_parse_clients_table(clients_raw))
        else:
            dump = await self._read_dump()
            name_map = await self._get_name_map(force=force_refresh_names)

        peers = parse_awg_dump(
            dump, name_map, self.settings.online_threshold_sec
        )
        # If any peer still has fallback short name, refresh clientsTable once.
        if (
            self.settings.awg_mode != "demo"
            and not force_refresh_names
            and any(p.name == p.public_key[:8] for p in peers)
        ):
            name_map = await self._get_name_map(force=True)
            peers = parse_awg_dump(
                dump, name_map, self.settings.online_threshold_sec
            )
        return DumpResult(peers=peers, scraped_at=datetime.now(timezone.utc))

    async def _get_name_map(self, *, force: bool = False) -> dict[str, str]:
        now = datetime.now(timezone.utc).timestamp()
        if (
            not force
            and self._name_map
            and now - self._name_map_at < self.settings.name_cache_ttl_sec
        ):
            return self._name_map
        conf = await self._read_file(self.settings.awg_conf_path)
        clients_raw = await self._read_file(self.settings.awg_clients_table)
        name_map = _parse_name_map(conf)
        name_map.update(_parse_clients_table(clients_raw))
        self._name_map = name_map
        self._name_map_at = now
        return name_map

    async def _docker_base(self) -> list[str]:
        return ["docker", "exec", self.settings.awg_container]

    async def _read_dump(self) -> str:
        show_parts = shlex.split(self.settings.awg_show_cmd)
        if self.settings.awg_mode == "docker_exec":
            cmd = await self._docker_base()
            cmd.extend(show_parts)
        else:
            cmd = show_parts
        return await _run(cmd)

    async def _read_file(self, path: str) -> str:
        if not path:
            return ""
        if self.settings.awg_mode == "docker_exec":
            cmd = await self._docker_base()
            cmd.extend(["cat", path])
        else:
            cmd = ["cat", path]
        try:
            return await _run(cmd)
        except RuntimeError:
            return ""

    async def disable_peer(self, public_key: str) -> None:
        """Remove peer from live interface (quota enforcement)."""
        iface = self.settings.awg_interface
        if self.settings.awg_mode == "demo":
            return
        set_cmd = ["awg", "set", iface, "peer", public_key, "remove"]
        if self.settings.awg_mode == "docker_exec":
            cmd = await self._docker_base()
            cmd.extend(set_cmd)
        else:
            cmd = set_cmd
        await _run(cmd)

    async def enable_peer(self, public_key: str, allowed_ips: str) -> None:
        """Re-add peer to live interface."""
        iface = self.settings.awg_interface
        if self.settings.awg_mode == "demo":
            return
        set_cmd = [
            "awg",
            "set",
            iface,
            "peer",
            public_key,
            "allowed-ips",
            allowed_ips,
        ]
        if self.settings.awg_mode == "docker_exec":
            cmd = await self._docker_base()
            cmd.extend(set_cmd)
        else:
            cmd = set_cmd
        await _run(cmd)
