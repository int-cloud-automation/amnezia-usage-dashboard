from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from .config import Settings

log = logging.getLogger("awg-stats.performance")

MEM_RE = re.compile(
    r"([\d.]+)\s*([KMGT]?i?B)\s*/\s*([\d.]+)\s*([KMGT]?i?B)",
    re.IGNORECASE,
)
UNIT_BYTES = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}

try:
    CLK_TCK = os.sysconf("SC_CLK_TCK")
except (ValueError, OSError, AttributeError):
    CLK_TCK = 100

PAGE_SIZE = 4096
TOP_N = 14
DOCKER_STATS_TTL_SEC = 3.0


def _to_bytes(value: float, unit: str) -> int:
    return int(value * UNIT_BYTES.get(unit.upper(), 1))


def _parse_cpu_line(parts: list[str]) -> tuple[int, int] | None:
    if len(parts) < 5:
        return None
    try:
        values = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    # Only the idle field — same as htop/top "non-idle" busy %.
    # iowait counts as busy so wait-on-disk shows up.
    idle = values[3]
    total = sum(values)
    if total <= 0:
        return None
    return idle, total


def _read_cpu_samples(proc_root: Path) -> dict[str, tuple[int, int]]:
    """Return {'all': (idle, total), '0': (...), '1': (...), ...} from /proc/stat."""
    out: dict[str, tuple[int, int]] = {}
    try:
        lines = (proc_root / "stat").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        name = parts[0]
        sample = _parse_cpu_line(parts)
        if sample is None:
            continue
        if name == "cpu":
            out["all"] = sample
        elif name.startswith("cpu") and name[3:].isdigit():
            out[name[3:]] = sample
    return out


def _cpu_pct(prev: tuple[int, int], cur: tuple[int, int]) -> float | None:
    prev_idle, prev_total = prev
    idle, total = cur
    d_total = total - prev_total
    d_idle = idle - prev_idle
    if d_total <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0)), 1)


def _cpu_count(proc_root: Path) -> int:
    samples = _read_cpu_samples(proc_root)
    n = sum(1 for k in samples if k != "all")
    return max(n, 1)


def _read_meminfo(proc_root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        text = (proc_root / "meminfo").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        num = raw.strip().split()[0]
        try:
            out[key] = int(num) * 1024
        except ValueError:
            continue
    return out


def _read_loadavg(proc_root: Path) -> list[float]:
    try:
        parts = (proc_root / "loadavg").read_text(encoding="utf-8").split()
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except (OSError, ValueError, IndexError):
        return []


def _parse_docker_stats_line(line: str) -> dict | None:
    parts = line.strip().split("\t")
    if len(parts) < 4:
        parts = re.split(r"\s{2,}", line.strip())
    if len(parts) < 4:
        return None
    name, cpu_raw, mem_raw, mem_pct_raw = parts[0], parts[1], parts[2], parts[3]
    try:
        cpu_pct = float(cpu_raw.replace("%", "").strip())
    except ValueError:
        cpu_pct = 0.0
    try:
        mem_pct = float(mem_pct_raw.replace("%", "").strip())
    except ValueError:
        mem_pct = 0.0
    used = limit = 0
    m = MEM_RE.search(mem_raw)
    if m:
        used = _to_bytes(float(m.group(1)), m.group(2))
        limit = _to_bytes(float(m.group(3)), m.group(4))
    return {
        "name": name,
        "cpu_pct": round(cpu_pct, 1),
        "mem_used_bytes": used,
        "mem_limit_bytes": limit,
        "mem_pct": round(mem_pct, 1),
    }


def _parse_pid_stat(text: str) -> tuple[int, str, str, int, int] | None:
    lparen = text.find("(")
    rparen = text.rfind(")")
    if lparen < 0 or rparen < lparen:
        return None
    try:
        pid = int(text[:lparen].strip())
    except ValueError:
        return None
    comm = text[lparen + 1 : rparen]
    rest = text[rparen + 2 :].split()
    if len(rest) < 22:
        return None
    state = rest[0]
    utime = int(rest[11])
    stime = int(rest[12])
    rss_pages = int(rest[21])
    return pid, comm, state, utime + stime, rss_pages * PAGE_SIZE


def _cmd_for(entry: Path, comm: str) -> str:
    try:
        raw = (entry / "cmdline").read_bytes()
    except OSError:
        return comm
    if not raw:
        return comm
    cmd = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    return (cmd[:48] + "…") if len(cmd) > 48 else cmd or comm


def _user_for(entry: Path) -> str:
    try:
        for line in (entry / "status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Uid:"):
                uid = int(line.split()[1])
                return "root" if uid == 0 else str(uid)
    except (OSError, ValueError, IndexError):
        pass
    return "?"


class PerformanceMonitor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.latest: dict = self._empty()
        self._prev_cpus: dict[str, tuple[int, int]] = {}
        self._prev_procs: dict[int, tuple[int, float]] = {}
        self._prev_wall: float = 0.0
        self._amnezia_cache: dict | None = None
        self._amnezia_at: float = 0.0
        self._lock = asyncio.Lock()

    def _proc_root(self) -> Path:
        host = Path(self.settings.host_proc_path)
        if (host / "stat").is_file() and (host / "meminfo").is_file():
            return host
        return Path("/proc")

    @staticmethod
    def _empty() -> dict:
        return {
            "cpu_pct": None,
            "cpu_count": 1,
            "cpus": [],
            "mem_used_bytes": None,
            "mem_total_bytes": None,
            "mem_pct": None,
            "swap_used_bytes": None,
            "swap_total_bytes": None,
            "load": [],
            "processes": [],
            "amnezia": {
                "cpu_pct": None,
                "mem_used_bytes": None,
                "mem_limit_bytes": None,
                "mem_pct": None,
                "container": None,
            },
            # keep old shape for Overview cards
            "host": {
                "cpu_pct": None,
                "mem_used_bytes": None,
                "mem_total_bytes": None,
                "mem_pct": None,
            },
            "error": None,
        }

    async def refresh(self) -> dict:
        async with self._lock:
            if self.settings.awg_mode == "demo":
                self.latest = self._demo()
                return self.latest

            out = self._empty()
            errors: list[str] = []
            try:
                host = await asyncio.to_thread(self._snapshot_host)
                out.update(host)
                out["host"] = {
                    "cpu_pct": host.get("cpu_pct"),
                    "mem_used_bytes": host.get("mem_used_bytes"),
                    "mem_total_bytes": host.get("mem_total_bytes"),
                    "mem_pct": host.get("mem_pct"),
                }
            except Exception as exc:  # noqa: BLE001
                log.exception("host htop snapshot failed")
                errors.append(f"host: {exc}")
            try:
                out["amnezia"] = await self._container_stats_cached()
            except Exception as exc:  # noqa: BLE001
                log.exception("amnezia stats failed")
                errors.append(f"amnezia: {exc}")
                out["amnezia"] = {
                    "cpu_pct": None,
                    "mem_used_bytes": None,
                    "mem_limit_bytes": None,
                    "mem_pct": None,
                    "container": self.settings.awg_container,
                }
            out["error"] = "; ".join(errors) if errors else None
            self.latest = out
            return out

    def _demo(self) -> dict:
        ncpu = os.cpu_count() or 2
        cpus = [
            {"id": i, "pct": round(4.0 + (i * 2.3) % 11, 1)} for i in range(ncpu)
        ]
        avg = round(sum(c["pct"] for c in cpus) / len(cpus), 1) if cpus else 0.0
        return {
            "cpu_pct": avg,
            "cpu_count": ncpu,
            "cpus": cpus,
            "mem_used_bytes": 1_288_490_188,
            "mem_total_bytes": 2_147_483_648,
            "mem_pct": 60.0,
            "swap_used_bytes": 0,
            "swap_total_bytes": 0,
            "load": [0.42, 0.38, 0.31],
            "processes": [
                {
                    "pid": 812,
                    "user": "root",
                    "state": "S",
                    "cpu_pct": 2.1,
                    "mem_pct": 4.0,
                    "rss_bytes": 88_080_384,
                    "command": "amneziawg-go awg0",
                    "highlight": True,
                },
                {
                    "pid": 1,
                    "user": "root",
                    "state": "S",
                    "cpu_pct": 0.3,
                    "mem_pct": 0.4,
                    "rss_bytes": 8_000_000,
                    "command": "systemd",
                    "highlight": False,
                },
            ],
            "amnezia": {
                "cpu_pct": 2.0,
                "mem_used_bytes": 88_080_384,
                "mem_limit_bytes": 2_147_483_648,
                "mem_pct": 4.1,
                "container": "amnezia-awg2",
            },
            "host": {
                "cpu_pct": 8.2,
                "mem_used_bytes": 1_288_490_188,
                "mem_total_bytes": 2_147_483_648,
                "mem_pct": 60.0,
            },
            "error": None,
        }

    def _snapshot_host(self) -> dict:
        root = self._proc_root()
        now = time.monotonic()
        mem = _read_meminfo(root)
        samples = _read_cpu_samples(root)
        ncpu = max(sum(1 for k in samples if k != "all"), 1)

        if not self._prev_cpus and samples:
            self._prev_cpus = samples
            time.sleep(0.25)
            samples = _read_cpu_samples(root) or samples
            now = time.monotonic()

        cpus: list[dict] = []
        core_pcts: list[float] = []
        for key in sorted(
            (k for k in samples if k != "all"),
            key=lambda x: int(x) if x.isdigit() else x,
        ):
            cur = samples[key]
            prev = self._prev_cpus.get(key)
            pct = _cpu_pct(prev, cur) if prev else None
            if pct is not None:
                core_pcts.append(pct)
            cpus.append({"id": int(key) if key.isdigit() else key, "pct": pct})

        cpu_pct = None
        if "all" in samples and "all" in self._prev_cpus:
            cpu_pct = _cpu_pct(self._prev_cpus["all"], samples["all"])
        elif core_pcts:
            cpu_pct = round(sum(core_pcts) / len(core_pcts), 1)

        if samples:
            self._prev_cpus = samples

        used = total_mem = mem_pct = None
        if "MemTotal" in mem and "MemAvailable" in mem and mem["MemTotal"] > 0:
            total_mem = mem["MemTotal"]
            used = max(0, total_mem - mem["MemAvailable"])
            mem_pct = round((used / total_mem) * 100, 1)

        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = max(0, swap_total - swap_free) if swap_total else 0

        processes = self._process_list(root, now, total_mem or 1)
        return {
            "cpu_pct": cpu_pct,
            "cpu_count": ncpu,
            "cpus": cpus,
            "mem_used_bytes": used,
            "mem_total_bytes": total_mem,
            "mem_pct": mem_pct,
            "swap_used_bytes": swap_used,
            "swap_total_bytes": swap_total,
            "load": _read_loadavg(root),
            "processes": processes,
        }

    def _process_list(
        self, root: Path, now: float, mem_total: int
    ) -> list[dict]:
        wall = now - self._prev_wall if self._prev_wall else 0.0
        current: dict[int, tuple[int, float]] = {}
        rows: list[dict] = []
        awg_name = self.settings.awg_container.lower()
        highlight_needles = (
            awg_name,
            "amneziawg",
            "amnezia-awg",
            "awg-quick",
            " awg0",
            "/awg0",
        )

        for entry in root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                text = (entry / "stat").read_text(encoding="utf-8")
            except OSError:
                continue
            parsed = _parse_pid_stat(text)
            if not parsed:
                continue
            pid, comm, state, ticks, rss = parsed
            current[pid] = (ticks, now)
            cpu_pct = 0.0
            prev = self._prev_procs.get(pid)
            if prev and wall > 0.05:
                d_ticks = ticks - prev[0]
                if d_ticks < 0:
                    d_ticks = ticks
                cpu_pct = (d_ticks / CLK_TCK) / wall * 100.0
            cmd = _cmd_for(entry, comm)
            blob = f"{comm} {cmd}".lower()
            highlight = any(n in blob for n in highlight_needles)
            rows.append(
                {
                    "pid": pid,
                    "user": _user_for(entry),
                    "state": state,
                    "cpu_pct": round(min(cpu_pct, 999.0), 1),
                    "mem_pct": round((rss / mem_total) * 100, 1) if mem_total else 0.0,
                    "rss_bytes": rss,
                    "command": cmd,
                    "highlight": highlight,
                }
            )

        self._prev_procs = current
        self._prev_wall = now
        rows.sort(key=lambda r: (-r["cpu_pct"], -r["rss_bytes"], r["pid"]))
        # Keep highlighted Amnezia-related rows even if not in top CPU.
        top = rows[:TOP_N]
        seen = {r["pid"] for r in top}
        for r in rows:
            if r["highlight"] and r["pid"] not in seen:
                top.append(r)
                seen.add(r["pid"])
            if len(top) >= TOP_N + 3:
                break
        return top

    async def _container_stats_cached(self) -> dict:
        now = time.monotonic()
        if (
            self._amnezia_cache is not None
            and now - self._amnezia_at < DOCKER_STATS_TTL_SEC
        ):
            return self._amnezia_cache
        stats = await self._container_stats()
        self._amnezia_cache = stats
        self._amnezia_at = now
        return stats

    async def _container_stats(self) -> dict:
        name = self.settings.awg_container
        empty = {
            "cpu_pct": None,
            "mem_used_bytes": None,
            "mem_limit_bytes": None,
            "mem_pct": None,
            "container": name,
        }
        cmd = [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
            name,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            raise RuntimeError(err or f"docker stats failed ({proc.returncode})")
        lines = stdout.decode(errors="replace").strip().splitlines()
        if not lines:
            return empty
        parsed = _parse_docker_stats_line(lines[0])
        if not parsed:
            return empty
        return {
            "cpu_pct": parsed["cpu_pct"],
            "mem_used_bytes": parsed["mem_used_bytes"],
            "mem_limit_bytes": parsed["mem_limit_bytes"],
            "mem_pct": parsed["mem_pct"],
            "container": name,
        }
