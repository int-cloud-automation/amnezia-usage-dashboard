from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_usage (
    day TEXT NOT NULL,
    public_key TEXT NOT NULL,
    name TEXT NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    online_samples INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, public_key)
);

CREATE TABLE IF NOT EXISTS peer_state (
    public_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    allowed_ips TEXT NOT NULL DEFAULT '',
    last_rx INTEGER NOT NULL DEFAULT 0,
    last_tx INTEGER NOT NULL DEFAULT 0,
    lifetime_rx INTEGER NOT NULL DEFAULT 0,
    lifetime_tx INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    disabled_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS quotas (
    public_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    limit_bytes INTEGER NOT NULL,
    period TEXT NOT NULL DEFAULT 'month',  -- day | week | month | total
    enabled INTEGER NOT NULL DEFAULT 1,
    auto_disable INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str, tz_name: str = "UTC"):
        self.path = path
        try:
            self.tz = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 — bad tz must not break startup
            self.tz = timezone.utc
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        assert self._db is not None
        cur = await self._db.execute("PRAGMA table_info(peer_state)")
        cols = {row["name"] for row in await cur.fetchall()}
        if "lifetime_rx" not in cols:
            await self._db.execute(
                "ALTER TABLE peer_state ADD COLUMN lifetime_rx INTEGER NOT NULL DEFAULT 0"
            )
            await self._db.execute(
                "ALTER TABLE peer_state ADD COLUMN lifetime_tx INTEGER NOT NULL DEFAULT 0"
            )
            await self._db.execute(
                "UPDATE peer_state SET lifetime_rx = last_rx, lifetime_tx = last_tx"
            )
        if "disabled_by" not in cols:
            await self._db.execute(
                "ALTER TABLE peer_state ADD COLUMN disabled_by TEXT NOT NULL DEFAULT ''"
            )
            await self._db.execute(
                "UPDATE peer_state SET disabled_by = 'manual' WHERE disabled = 1"
            )
        # Raw per-scrape snapshots were never read back; they only burned disk.
        await self._db.execute("DROP TABLE IF EXISTS snapshots")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None
        return self._db

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def period_start(self, period: str) -> date:
        today = self.today()
        if period == "day":
            return today
        if period == "week":
            return today - timedelta(days=6)
        if period == "month":
            return today.replace(day=1)
        return date(1970, 1, 1)

    async def upsert_peer_and_diff(
        self,
        *,
        scraped_at: datetime,
        public_key: str,
        name: str,
        allowed_ips: str,
        rx: int,
        tx: int,
        online: bool,
        count_sample: bool = True,
    ) -> tuple[int, int]:
        """Fold one scrape into daily usage; return (delta_rx, delta_tx)."""
        cur = await self.db.execute(
            "SELECT last_rx, last_tx FROM peer_state WHERE public_key = ?",
            (public_key,),
        )
        row = await cur.fetchone()
        if row is None:
            # First sight: adopt current counters as the lifetime baseline so
            # traffic from before this panel existed is not lost, but is also
            # not booked into today.
            delta_rx, delta_tx = 0, 0
        else:
            prev_rx, prev_tx = int(row["last_rx"]), int(row["last_tx"])
            # Counter reset (container recreate / peer re-added) → treat as absolute
            delta_rx = rx - prev_rx if rx >= prev_rx else rx
            delta_tx = tx - prev_tx if tx >= prev_tx else tx

        await self.db.execute(
            """
            INSERT INTO peer_state
                (public_key, name, allowed_ips, last_rx, last_tx,
                 lifetime_rx, lifetime_tx, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(public_key) DO UPDATE SET
                name = excluded.name,
                allowed_ips = excluded.allowed_ips,
                last_rx = excluded.last_rx,
                last_tx = excluded.last_tx,
                lifetime_rx = peer_state.lifetime_rx + ?,
                lifetime_tx = peer_state.lifetime_tx + ?,
                last_seen = excluded.last_seen
            """,
            (
                public_key,
                name,
                allowed_ips,
                rx,
                tx,
                rx,
                tx,
                scraped_at.isoformat(),
                delta_rx,
                delta_tx,
            ),
        )

        day = scraped_at.astimezone(self.tz).date().isoformat()
        await self.db.execute(
            """
            INSERT INTO daily_usage (day, public_key, name, rx_bytes, tx_bytes, online_samples)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, public_key) DO UPDATE SET
                name = excluded.name,
                rx_bytes = rx_bytes + excluded.rx_bytes,
                tx_bytes = tx_bytes + excluded.tx_bytes,
                online_samples = online_samples + excluded.online_samples
            """,
            (day, public_key, name, delta_rx, delta_tx, 1 if (online and count_sample) else 0),
        )
        return delta_rx, delta_tx

    async def rename_peer_history(self, public_key: str, name: str) -> None:
        await self.db.execute(
            "UPDATE daily_usage SET name = ? WHERE public_key = ? AND name <> ?",
            (name, public_key, name),
        )

    async def commit(self) -> None:
        await self.db.commit()

    async def list_peers(self) -> list[dict]:
        cur = await self.db.execute(
            "SELECT * FROM peer_state ORDER BY name COLLATE NOCASE"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_peer(self, public_key: str) -> dict | None:
        cur = await self.db.execute(
            "SELECT * FROM peer_state WHERE public_key = ?", (public_key,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_disabled(
        self, public_key: str, disabled: bool, *, by: str = ""
    ) -> None:
        await self.db.execute(
            "UPDATE peer_state SET disabled = ?, disabled_by = ? WHERE public_key = ?",
            (1 if disabled else 0, by if disabled else "", public_key),
        )
        await self.db.commit()

    async def usage_between(self, start: date, end: date) -> list[dict]:
        cur = await self.db.execute(
            """
            SELECT public_key, MAX(name) AS name,
                   SUM(rx_bytes) AS rx, SUM(tx_bytes) AS tx,
                   SUM(online_samples) AS online_samples
            FROM daily_usage
            WHERE day >= ? AND day <= ?
            GROUP BY public_key
            ORDER BY (rx + tx) DESC
            """,
            (start.isoformat(), end.isoformat()),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def daily_series(self, days: int) -> list[dict]:
        """Continuous day-by-day series, gaps filled with zeros."""
        today = self.today()
        start = today - timedelta(days=days - 1)
        cur = await self.db.execute(
            """
            SELECT day, SUM(rx_bytes) AS rx, SUM(tx_bytes) AS tx,
                   COUNT(DISTINCT CASE WHEN online_samples > 0 THEN public_key END)
                     AS active_peers
            FROM daily_usage
            WHERE day >= ? AND day <= ?
            GROUP BY day
            """,
            (start.isoformat(), today.isoformat()),
        )
        rows = {r["day"]: r for r in await cur.fetchall()}
        series = []
        for i in range(days):
            key = (start + timedelta(days=i)).isoformat()
            row = rows.get(key)
            series.append(
                {
                    "day": key,
                    "rx": int(row["rx"] or 0) if row else 0,
                    "tx": int(row["tx"] or 0) if row else 0,
                    "active_peers": int(row["active_peers"] or 0) if row else 0,
                }
            )
        return series

    async def dau(self, day: date | None = None) -> int:
        day = day or self.today()
        cur = await self.db.execute(
            "SELECT COUNT(*) AS c FROM daily_usage WHERE day = ? AND online_samples > 0",
            (day.isoformat(),),
        )
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def mau(self) -> int:
        start = (self.today() - timedelta(days=29)).isoformat()
        cur = await self.db.execute(
            """
            SELECT COUNT(DISTINCT public_key) AS c FROM daily_usage
            WHERE day >= ? AND online_samples > 0
            """,
            (start,),
        )
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def totals_for_period(self, period: str) -> dict[str, dict]:
        """Usage per peer for a quota period.

        `total` means real lifetime (WireGuard counters carried over from
        before this panel started), everything else is panel-recorded traffic.
        """
        if period == "total":
            return {
                p["public_key"]: {
                    "name": p["name"],
                    "rx": int(p["lifetime_rx"] or 0),
                    "tx": int(p["lifetime_tx"] or 0),
                    "total": int(p["lifetime_rx"] or 0) + int(p["lifetime_tx"] or 0),
                }
                for p in await self.list_peers()
            }
        rows = await self.usage_between(self.period_start(period), self.today())
        return {
            r["public_key"]: {
                "name": r["name"],
                "rx": int(r["rx"] or 0),
                "tx": int(r["tx"] or 0),
                "total": int(r["rx"] or 0) + int(r["tx"] or 0),
            }
            for r in rows
        }

    async def list_quotas(self) -> list[dict]:
        cur = await self.db.execute("SELECT * FROM quotas ORDER BY name COLLATE NOCASE")
        return [dict(r) for r in await cur.fetchall()]

    async def upsert_quota(
        self,
        *,
        public_key: str,
        name: str,
        limit_bytes: int,
        period: str,
        enabled: bool,
        auto_disable: bool,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO quotas (public_key, name, limit_bytes, period, enabled, auto_disable, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(public_key) DO UPDATE SET
                name = excluded.name,
                limit_bytes = excluded.limit_bytes,
                period = excluded.period,
                enabled = excluded.enabled,
                auto_disable = excluded.auto_disable,
                updated_at = excluded.updated_at
            """,
            (
                public_key,
                name,
                limit_bytes,
                period,
                1 if enabled else 0,
                1 if auto_disable else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await self.db.commit()

    async def delete_quota(self, public_key: str) -> None:
        await self.db.execute("DELETE FROM quotas WHERE public_key = ?", (public_key,))
        await self.db.commit()
