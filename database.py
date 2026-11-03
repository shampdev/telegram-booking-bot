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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    price       INTEGER NOT NULL,
    duration    INTEGER NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS masters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS bookings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    client_name      TEXT    NOT NULL,
    phone            TEXT    NOT NULL,
    service_id       INTEGER NOT NULL REFERENCES services(id),
    master_id        INTEGER NOT NULL REFERENCES masters(id),
    start_at         TEXT    NOT NULL,
    end_at           TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'active',  -- active | cancelled
    booked_price     INTEGER,  -- цена на момент записи
    reminded         INTEGER NOT NULL DEFAULT 0,
    review_requested INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id  INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
    user_id     INTEGER NOT NULL,
    master_id   INTEGER NOT NULL REFERENCES masters(id),
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_master_start ON bookings(master_id, start_at);
CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id);
"""

# Колонки, добавленные в новых версиях: так старая база обновится без потери данных
MIGRATIONS = {
    "services": {"description": "TEXT NOT NULL DEFAULT ''"},
    "masters": {"description": "TEXT NOT NULL DEFAULT ''"},
    "bookings": {"review_requested": "INTEGER NOT NULL DEFAULT 0", "booked_price": "INTEGER"},
}

BOOKING_SELECT = """
SELECT b.*, s.name AS service_name, COALESCE(b.booked_price, s.price) AS price, s.duration, m.name AS master_name,
       r.rating, r.comment AS review_comment
FROM bookings b
JOIN services s ON s.id = b.service_id
JOIN masters  m ON m.id = b.master_id
LEFT JOIN reviews r ON r.booking_id = b.id
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
        await self._migrate()
        await self._seed()
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            cur = await self.conn.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in await cur.fetchall()}
            for column, ddl in columns.items():
                if column not in existing:
                    await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    async def _seed(self) -> None:
        cur = await self.conn.execute("SELECT COUNT(*) FROM services")
        if (await cur.fetchone())[0] == 0:
            await self.conn.executemany(
                "INSERT INTO services (name, price, duration, description) VALUES (?, ?, ?, ?)", DEFAULT_SERVICES
            )
        cur = await self.conn.execute("SELECT COUNT(*) FROM masters")
        if (await cur.fetchone())[0] == 0:
            await self.conn.executemany("INSERT INTO masters (name, description) VALUES (?, ?)", DEFAULT_MASTERS)

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self.conn.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

    async def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def _write(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        async with self._write_lock:
            cur = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cur

    # ---------- Пользователи ----------

    async def add_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        await self._write(
            """INSERT INTO users (id, username, first_name, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET username = excluded.username,
                                             first_name = excluded.first_name""",
            (user_id, username, first_name, _fmt(datetime.now())),
        )

    async def get_user_ids(self) -> list[int]:
        cur = await self.conn.execute("SELECT id FROM users")
        return [row[0] for row in await cur.fetchall()]

    # ---------- Услуги ----------

    async def get_services(self, include_hidden: bool = False) -> list[dict]:
        where = "" if include_hidden else "WHERE is_active = 1"
        return await self._fetchall(f"SELECT * FROM services {where} ORDER BY id")

    async def get_service(self, service_id: int) -> dict | None:
        return await self._fetchone("SELECT * FROM services WHERE id = ?", (service_id,))

    async def add_service(self, name: str, price: int, duration: int, description: str) -> int:
        cur = await self._write(
            "INSERT INTO services (name, price, duration, description) VALUES (?, ?, ?, ?)",
            (name, price, duration, description),
        )
        return cur.lastrowid

    async def set_service_price(self, service_id: int, price: int) -> None:
        await self._write("UPDATE services SET price = ? WHERE id = ?", (price, service_id))

    async def toggle_service(self, service_id: int) -> None:
        await self._write("UPDATE services SET is_active = 1 - is_active WHERE id = ?", (service_id,))

    # ---------- Мастера ----------

    async def get_masters(self, include_hidden: bool = False) -> list[dict]:
        """Мастера вместе со средней оценкой и числом отзывов."""
        where = "" if include_hidden else "WHERE m.is_active = 1"
        return await self._fetchall(
            f"""SELECT m.*, ROUND(AVG(r.rating), 1) AS rating, COUNT(r.id) AS reviews_count
                FROM masters m LEFT JOIN reviews r ON r.master_id = m.id
                {where} GROUP BY m.id ORDER BY m.id"""
        )

    async def get_master(self, master_id: int) -> dict | None:
        return await self._fetchone("SELECT * FROM masters WHERE id = ?", (master_id,))

    async def add_master(self, name: str, description: str) -> int:
        cur = await self._write("INSERT INTO masters (name, description) VALUES (?, ?)", (name, description))
        return cur.lastrowid

    async def toggle_master(self, master_id: int) -> None:
        await self._write("UPDATE masters SET is_active = 1 - is_active WHERE id = ?", (master_id,))

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
        price: int,
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
                   (user_id, client_name, phone, service_id, master_id, start_at, end_at,
                    booked_price, reminded, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, client_name, phone, service_id, master_id,
                 start_s, end_s, price, int(reminded), _fmt(datetime.now())),
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

    async def get_bookings_between(self, start: date | None, end: date | None) -> list[dict]:
        """Все записи (включая отменённые) за период [start, end). None — без ограничения."""
        conditions, params = [], []
        if start:
            conditions.append("b.start_at >= ?")
            params.append(_fmt(datetime.combine(start, datetime.min.time())))
        if end:
            conditions.append("b.start_at < ?")
            params.append(_fmt(datetime.combine(end, datetime.min.time())))
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        return await self._fetchall(BOOKING_SELECT + where + " ORDER BY b.start_at", tuple(params))

    async def cancel_booking(self, booking_id: int) -> bool:
        cur = await self._write(
            "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status = 'active'", (booking_id,)
        )
        return cur.rowcount > 0

    # ---------- Напоминания и отзывы ----------

    async def get_due_reminders(self, now: datetime, until: datetime) -> list[dict]:
        return await self._fetchall(
            BOOKING_SELECT
            + " WHERE b.status = 'active' AND b.reminded = 0 AND b.start_at > ? AND b.start_at <= ?",
            (_fmt(now), _fmt(until)),
        )

    async def mark_reminded(self, booking_id: int) -> None:
        await self._write("UPDATE bookings SET reminded = 1 WHERE id = ?", (booking_id,))

    async def get_due_review_requests(self, ended_before: datetime, ended_after: datetime) -> list[dict]:
        """Завершённые визиты, по которым ещё не просили оценку.
        ended_after не даёт засыпать клиентов просьбами о старых визитах, если бот долго был выключен."""
        return await self._fetchall(
            BOOKING_SELECT
            + " WHERE b.status = 'active' AND b.review_requested = 0 AND b.end_at <= ? AND b.end_at > ?",
            (_fmt(ended_before), _fmt(ended_after)),
        )

    async def mark_review_requested(self, booking_id: int) -> None:
        await self._write("UPDATE bookings SET review_requested = 1 WHERE id = ?", (booking_id,))

    async def save_rating(self, booking: dict, rating: int) -> None:
        await self._write(
            """INSERT INTO reviews (booking_id, user_id, master_id, rating, created_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(booking_id) DO UPDATE SET rating = excluded.rating""",
            (booking["id"], booking["user_id"], booking["master_id"], rating, _fmt(datetime.now())),
        )

    async def save_review_comment(self, booking_id: int, comment: str) -> None:
        await self._write("UPDATE reviews SET comment = ? WHERE booking_id = ?", (comment, booking_id))

    async def get_recent_reviews(self, limit: int = 5) -> list[dict]:
        return await self._fetchall(
            """SELECT r.*, m.name AS master_name, b.client_name
               FROM reviews r JOIN masters m ON m.id = r.master_id JOIN bookings b ON b.id = r.booking_id
               ORDER BY r.id DESC LIMIT ?""",
            (limit,),
        )
