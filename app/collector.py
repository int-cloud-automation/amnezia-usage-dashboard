from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .awg import AwgClient, PeerSnapshot
from .config import Settings
from .db import Database

log = logging.getLogger("awg-stats.collector")

# Below this gap a rate sample is noise (e.g. manual refresh right after a poll).
MIN_RATE_DT_SEC = 5.0


class Collector:
    def __init__(self, settings: Settings, db: Database, awg: AwgClient):
        self.settings = settings
        self.db = db
        self.awg = awg
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.last_ok_at: datetime | None = None
        self.latest_peers: list[dict] = []
        self._lock = asyncio.Lock()
        self._last_persist_at: float = 0.0
        self._prev_counters: dict[str, tuple[int, int, float]] = {}
        self._prev_rates: dict[str, tuple[float, float]] = {}

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        # Startup already persists via scrape_once; don't race it.
        await asyncio.sleep(self.settings.online_poll_sec)
        while True:
            try:
                await self.poll_once()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.exception("poll failed")
            await asyncio.sleep(self.settings.online_poll_sec)

    async def poll_once(self) -> None:
        await self._collect()

    async def scrape_once(self) -> None:
        # Manual refresh / startup: reload names from Amnezia and always persist.
        await self._collect(force_persist=True, force_refresh_names=True)

    def _rates_for(
        self, public_key: str, rx: int, tx: int, now_ts: float
    ) -> tuple[float, float]:
        """Return (rx_bps, tx_bps) since previous sample."""
        prev = self._prev_counters.get(public_key)
        if prev is None:
            self._prev_counters[public_key] = (rx, tx, now_ts)
            return 0.0, 0.0
        prev_rx, prev_tx, prev_ts = prev
        dt = now_ts - prev_ts
        if dt < MIN_RATE_DT_SEC:
            return self._prev_rates.get(public_key, (0.0, 0.0))
        self._prev_counters[public_key] = (rx, tx, now_ts)
        # Counter reset (container recreate / peer re-added)
        d_rx = rx - prev_rx if rx >= prev_rx else rx
        d_tx = tx - prev_tx if tx >= prev_tx else tx
        rates = (d_rx / dt, d_tx / dt)
        self._prev_rates[public_key] = rates
        return rates

    def _peer_view(self, peer: PeerSnapshot, now_ts: float) -> dict:
        rx_bps, tx_bps = self._rates_for(peer.public_key, peer.rx, peer.tx, now_ts)
        return {
            "public_key": peer.public_key,
            "name": peer.name,
            "allowed_ips": peer.allowed_ips,
            "endpoint": peer.endpoint,
            "handshake": peer.latest_handshake,
            "rx": peer.rx,
            "tx": peer.tx,
            "online": peer.online,
            "status": peer.status,
            # Server RX = client upload; server TX = client download.
            "rx_bps": round(rx_bps, 1),
            "tx_bps": round(tx_bps, 1),
            "up_bps": round(rx_bps, 1),
            "down_bps": round(tx_bps, 1),
        }

    def _learned_real_name(self, peers: list[PeerSnapshot]) -> bool:
        previous = {p["public_key"]: p.get("name") for p in self.latest_peers}
        return any(
            previous.get(p.public_key) == p.public_key[:8]
            and p.name != p.public_key[:8]
            for p in peers
        )

    async def _collect(
        self, *, force_persist: bool = False, force_refresh_names: bool = False
    ) -> None:
        async with self._lock:
            await self._collect_locked(
                force_persist=force_persist,
                force_refresh_names=force_refresh_names,
            )

    async def _collect_locked(
        self, *, force_persist: bool, force_refresh_names: bool
    ) -> None:
        result = await self.awg.fetch(force_refresh_names=force_refresh_names)
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        latest = [self._peer_view(peer, now_ts) for peer in result.peers]
        live_keys = {peer.public_key for peer in result.peers}
        for stale in set(self._prev_counters) - live_keys:
            self._prev_counters.pop(stale, None)
            self._prev_rates.pop(stale, None)

        scheduled = (
            now_ts - self._last_persist_at >= self.settings.scrape_interval_sec
        )
        # Persist off-schedule once we finally learn a client's real name.
        should_persist = (
            force_persist or scheduled or self._learned_real_name(result.peers)
        )

        self.latest_peers = latest
        self.last_ok_at = now
        if should_persist:
            if self.settings.awg_mode == "demo":
                await self._seed_demo_history(result.peers)
            for peer in result.peers:
                await self.db.upsert_peer_and_diff(
                    scraped_at=result.scraped_at,
                    public_key=peer.public_key,
                    name=peer.name,
                    allowed_ips=peer.allowed_ips,
                    rx=peer.rx,
                    tx=peer.tx,
                    online=peer.online,
                    # Only regular ticks count as online time; extra writes
                    # (startup, manual refresh) must not inflate DAU samples.
                    count_sample=scheduled or self._last_persist_at == 0.0,
                )
                await self.db.rename_peer_history(peer.public_key, peer.name)
            await self.db.commit()
            # Name-only extra writes must not push the next sample out.
            if scheduled or self._last_persist_at == 0.0:
                self._last_persist_at = now_ts
            await self._enforce_quotas()
        await self._restore_disabled(live_keys)

    async def _restore_disabled(self, live_keys: set[str]) -> None:
        """Keep disabled peers off the interface after an AWG container restart."""
        if self.settings.awg_mode == "demo" or not live_keys:
            return
        for peer in await self.db.list_peers():
            if not peer["disabled"] or peer["public_key"] not in live_keys:
                continue
            try:
                await self.awg.disable_peer(peer["public_key"])
            except Exception:  # noqa: BLE001
                log.exception("failed to keep peer disabled")

    async def _enforce_quotas(self) -> None:
        quotas = await self.db.list_quotas()
        if not quotas:
            return
        usage_cache: dict[str, dict[str, dict]] = {}
        for q in quotas:
            if not q["enabled"] or not q["auto_disable"]:
                continue
            period = q["period"]
            if period not in usage_cache:
                usage_cache[period] = await self.db.totals_for_period(period)
            used = usage_cache[period].get(q["public_key"], {}).get("total", 0)
            peer = await self.db.get_peer(q["public_key"])
            if not peer:
                continue
            over = used >= int(q["limit_bytes"])
            if over and not peer["disabled"]:
                log.warning(
                    "quota exceeded for %s (%s) — disabling",
                    q["name"],
                    q["public_key"][:8],
                )
                try:
                    await self.awg.disable_peer(q["public_key"])
                    await self.db.set_disabled(q["public_key"], True, by="quota")
                except Exception:  # noqa: BLE001
                    log.exception("failed to disable peer")
            elif not over and peer["disabled"]:
                # Never undo a manual disable — only peers this quota turned off.
                if peer["disabled_by"] != "quota":
                    continue
                allowed_ips = peer["allowed_ips"]
                if not allowed_ips:
                    log.warning(
                        "not re-enabling %s: allowed-ips unknown",
                        q["public_key"][:8],
                    )
                    continue
                try:
                    await self.awg.enable_peer(q["public_key"], allowed_ips)
                    await self.db.set_disabled(q["public_key"], False)
                except Exception:  # noqa: BLE001
                    log.exception("failed to re-enable peer")

    async def _seed_demo_history(self, peers) -> None:
        from datetime import timedelta

        cur = await self.db.db.execute("SELECT COUNT(*) AS c FROM daily_usage")
        row = await cur.fetchone()
        if row and int(row["c"]) > 0:
            return
        today = self.db.today()
        for i in range(30):
            day = (today - timedelta(days=29 - i)).isoformat()
            for idx, peer in enumerate(peers):
                base = (idx + 1) * 400_000_000
                rx = int(base * (0.6 + (i % 7) * 0.1))
                tx = int(base * 0.25)
                await self.db.db.execute(
                    """
                    INSERT OR IGNORE INTO daily_usage
                    (day, public_key, name, rx_bytes, tx_bytes, online_samples)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (day, peer.public_key, peer.name, rx, tx, 1 if i % 3 else 0),
                )
        await self.db.commit()
