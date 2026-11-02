"""Работа с базой данных SQLite (асинхронно, через aiosqlite)."""
import asyncio
from datetime import date, datetime, timedelta

import aiosqlite

from settings import DEFAULT_MASTERS, DEFAULT_SERVICES, DT_FMT

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    price     INTEGER NOT NULL,
    duration  INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS masters (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS bookings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    client_name TEXT    NOT NULL,
    phone       TEXT    NOT NULL,
    service_id  INTEGER NOT NULL REFERENCES services(id),
    master_id   INTEGER NOT NULL REFERENCES masters(id),
    start_at    TEXT    NOT NULL,
    end_at      TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active',  -- active | cancelled
    reminded    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_master_start ON bookings(master_id, start_at);
CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id);
"""

BOOKING_SELECT = """
SELECT b.*, s.name AS service_name, s.price, s.duration, m.name AS master_name
FROM bookings b
JOIN services s ON s.id = b.service_id
JOIN masters  m ON m.id = b.master_id
"""


def _fmt(dt: datetime) -> str:
    return dt.strftime(DT_FMT)


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, datetime.min.time())
    return _fmt(start), _fmt(start + timedelta(days=1))


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        # Одно соединение на весь бот, а запись сериализуем замком,
        # чтобы два клиента не заняли одно и то же время одновременно.
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.executescript(SCHEMA)
        await self._seed()
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _seed(self) -> None:
        cur = await self.conn.execute("SELECT COUNT(*) FROM services")
        if (await cur.fetchone())[0] == 0:
            await self.conn.executemany(
                "INSERT INTO services (name, price, duration) VALUES (?, ?, ?)", DEFAULT_SERVICES
            )
        cur = await self.conn.execute("SELECT COUNT(*) FROM masters")
        if (await cur.fetchone())[0] == 0:
            await self.conn.executemany(
                "INSERT INTO masters (name) VALUES (?)", [(m,) for m in DEFAULT_MASTERS]
            )

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self.conn.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

    async def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    # ---------- Пользователи ----------

    async def add_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        await self.conn.execute(
            """INSERT INTO users (id, username, first_name, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET username = excluded.username,
                                             first_name = excluded.first_name""",
            (user_id, username, first_name, _fmt(datetime.now())),
        )
        await self.conn.commit()

    async def get_user_ids(self) -> list[int]:
        cur = await self.conn.execute("SELECT id FROM users")
        return [row[0] for row in await cur.fetchall()]

    # ---------- Услуги и мастера ----------

    async def get_services(self) -> list[dict]:
        return await self._fetchall("SELECT * FROM services WHERE is_active = 1 ORDER BY id")

    async def get_service(self, service_id: int) -> dict | None:
        return await self._fetchone("SELECT * FROM services WHERE id = ?", (service_id,))

    async def get_masters(self) -> list[dict]:
        return await self._fetchall("SELECT * FROM masters WHERE is_active = 1 ORDER BY id")

    async def get_master(self, master_id: int) -> dict | None:
        return await self._fetchone("SELECT * FROM masters WHERE id = ?", (master_id,))

    # ---------- Записи ----------

    async def get_busy_intervals(self, master_id: int, day: date) -> list[tuple[datetime, datetime]]:
        """Занятые промежутки мастера в указанный день."""
        day_start, day_end = _day_bounds(day)
        cur = await self.conn.execute(
            """SELECT start_at, end_at FROM bookings
               WHERE master_id = ? AND status = 'active' AND start_at >= ? AND start_at < ?""",
            (master_id, day_start, day_end),
        )
        return [
            (datetime.strptime(s, DT_FMT), datetime.strptime(e, DT_FMT))
            for s, e in await cur.fetchall()
        ]

    async def create_booking(
        self,
        user_id: int,
        client_name: str,
        phone: str,
        service_id: int,
        master_id: int,
        start: datetime,
        duration: int,
        reminded: bool = False,
    ) -> int | None:
        """Создаёт запись. Возвращает id или None, если время уже заняли."""
        start_s = _fmt(start)
        end_s = _fmt(start + timedelta(minutes=duration))
        async with self._write_lock:
            # Пересечение интервалов: чужое начало раньше нашего конца и чужой конец позже нашего начала
            cur = await self.conn.execute(
                """SELECT 1 FROM bookings
                   WHERE master_id = ? AND status = 'active' AND start_at < ? AND end_at > ?
                   LIMIT 1""",
                (master_id, end_s, start_s),
            )
            if await cur.fetchone():
                return None
            cur = await self.conn.execute(
                """INSERT INTO bookings
                   (user_id, client_name, phone, service_id, master_id, start_at, end_at, reminded, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, client_name, phone, service_id, master_id,
                 start_s, end_s, int(reminded), _fmt(datetime.now())),
            )
            await self.conn.commit()
            return cur.lastrowid

    async def get_booking(self, booking_id: int) -> dict | None:
        return await self._fetchone(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,))

    async def get_user_bookings(self, user_id: int, now: datetime) -> list[dict]:
        """Предстоящие активные записи клиента."""
        return await self._fetchall(
            BOOKING_SELECT + " WHERE b.user_id = ? AND b.status = 'active' AND b.start_at >= ? ORDER BY b.start_at",
            (user_id, _fmt(now)),
        )

    async def get_bookings_by_date(self, day: date) -> list[dict]:
        day_start, day_end = _day_bounds(day)
        return await self._fetchall(
            BOOKING_SELECT + " WHERE b.status = 'active' AND b.start_at >= ? AND b.start_at < ? ORDER BY b.start_at",
            (day_start, day_end),
        )

    async def cancel_booking(self, booking_id: int) -> bool:
        async with self._write_lock:
            cur = await self.conn.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status = 'active'", (booking_id,)
            )
            await self.conn.commit()
            return cur.rowcount > 0

    # ---------- Напоминания ----------

    async def get_due_reminders(self, now: datetime, until: datetime) -> list[dict]:
        return await self._fetchall(
            BOOKING_SELECT
            + " WHERE b.status = 'active' AND b.reminded = 0 AND b.start_at > ? AND b.start_at <= ?",
            (_fmt(now), _fmt(until)),
        )

    async def mark_reminded(self, booking_id: int) -> None:
        await self.conn.execute("UPDATE bookings SET reminded = 1 WHERE id = ?", (booking_id,))
        await self.conn.commit()
