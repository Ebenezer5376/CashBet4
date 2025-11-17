# database.py
"""
Database helpers for Cash Bet4 + Pari Bet4 (PostgreSQL, psycopg3 async pool)

Usage:
    from database import conn_cm, init_db, init_channels_db, get_user, create_user, ...
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
"""

import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Any, List, Dict

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from datetime import datetime

# ------------ Configuration ------------
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URI")  # must be set in env
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required for database.py")

# Pool (async)
_pool: Optional[AsyncConnectionPool] = None


def _get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        # min_size/max_size can be adjusted
        _pool = AsyncConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)
    return _pool


@asynccontextmanager
async def conn_cm():
    """
    Async context manager that yields an AsyncConnection (psycopg connection).
    Use:
        async with conn_cm() as conn:
            async with conn.cursor() as cur:
                await cur.execute(...)
    """
    pool = _get_pool()
    async with pool.connection() as conn:
        # set row factory to dict for convenience
        conn.row_factory = dict_row
        yield conn


# ------------ Low-level helpers ------------
async def _fetchone(conn, query: str, params: tuple = ()):
    async with conn.cursor() as cur:
        await cur.execute(query, params)
        return await cur.fetchone()


async def _fetchall(conn, query: str, params: tuple = ()):
    async with conn.cursor() as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


async def _execute(conn, query: str, params: tuple = ()):
    async with conn.cursor() as cur:
        await cur.execute(query, params)


# ------------ Initialization & migrations ------------
async def init_channels_db():
    """
    Create or migrate the required_channels table.
    """
    async with conn_cm() as conn:
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS required_channels (
            id SERIAL PRIMARY KEY,
            label TEXT UNIQUE,
            username TEXT,
            url TEXT,
            public_username TEXT,
            private_link TEXT
        );
        """)
        # seed default rows if empty
        rows = await _fetchone(conn, "SELECT COUNT(*) AS cnt FROM required_channels;")
        if rows and rows["cnt"] == 0:
            base_labels = ["@CashBet4_Retrait"] + [f"@CashBet4_Pub{i}" for i in range(1, 8)]
            async with conn.cursor() as cur:
                for label in base_labels:
                    await cur.execute(
                        "INSERT INTO required_channels (label, username, url, public_username, private_link) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING;",
                        (label, None, None, None, None)
                    )
        await conn.commit()


async def init_db():
    """
    Create all main tables used by both bots and run migrations.
    """
    async with conn_cm() as conn:
        # users table
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            solde BIGINT DEFAULT 0,
            last_bonus TEXT,
            bonus_days INTEGER DEFAULT 0,
            cycle_end_date TEXT,
            check_passed BOOLEAN DEFAULT FALSE,
            welcome_bonus INTEGER DEFAULT 0,
            parrain TEXT,
            bonus_claimed BOOLEAN DEFAULT FALSE,
            bonus_message_id INTEGER,
            fake_count INTEGER DEFAULT 0,
            blocked_until TEXT,
            has_withdrawn BOOLEAN DEFAULT FALSE
        );
        """)

        # filleuls
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS filleuls (
            parrain_id TEXT,
            filleul_id TEXT,
            PRIMARY KEY (parrain_id, filleul_id)
        );
        """)

        # transactions
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            type TEXT,
            montant BIGINT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # banned_users
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id TEXT PRIMARY KEY,
            reason TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # codes mystere
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS codes_mystere (
            code TEXT PRIMARY KEY,
            created_at TIMESTAMP,
            expires_at TIMESTAMP,
            used_count INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 10
        );
        """)

        # usage
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS codes_mystere_usage (
            code TEXT,
            user_id TEXT,
            PRIMARY KEY (code, user_id)
        );
        """)

        # required_channels (if not already created by init_channels_db)
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS required_channels (
            id SERIAL PRIMARY KEY,
            label TEXT UNIQUE,
            username TEXT,
            url TEXT,
            public_username TEXT,
            private_link TEXT
        );
        """)

        # PariBet4: matches & bets basic schema (adapt if PariBet4 needs extra fields)
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS matches (
            id SERIAL PRIMARY KEY,
            ext_id TEXT,               -- external id if any (string)
            home_team TEXT,
            away_team TEXT,
            start_ts TIMESTAMP,
            status TEXT,
            metadata JSONB
        );
        """)
        await _execute(conn, """
        CREATE TABLE IF NOT EXISTS bets (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
            stake BIGINT,
            choice TEXT,
            odd NUMERIC,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        await conn.commit()

    # run lightweight migrations (add missing columns if needed)
    await ensure_user_columns()
    await ensure_channels_columns()


async def ensure_user_columns():
    """
    Add any new columns to users table if they don't exist (Postgres supports IF NOT EXISTS).
    """
    async with conn_cm() as conn:
        # Using ALTER TABLE ... ADD COLUMN IF NOT EXISTS for safe migrations
        await _execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS fake_count INTEGER DEFAULT 0;")
        await _execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_until TEXT DEFAULT NULL;")
        await _execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS has_withdrawn BOOLEAN DEFAULT FALSE;")
        await _execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_message_id INTEGER;")
        await conn.commit()


async def ensure_channels_columns():
    async with conn_cm() as conn:
        await _execute(conn, "ALTER TABLE required_channels ADD COLUMN IF NOT EXISTS public_username TEXT DEFAULT NULL;")
        await _execute(conn, "ALTER TABLE required_channels ADD COLUMN IF NOT EXISTS private_link TEXT DEFAULT NULL;")
        await conn.commit()


# ------------ High-level helpers (API similar to your sqlite functions) ------------
async def create_user(user_id: str, parrain: Optional[str] = None):
    """
    Create user if not exists. If parrain provided and not equal to user_id, set parrain if not set.
    """
    async with conn_cm() as conn:
        # Insert if not exists
        await _execute(conn, """
            INSERT INTO users (user_id, parrain) VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING;
        """, (str(user_id), parrain))
        # Set parrain if provided and parrain not already set
        if parrain:
            await _execute(conn, """
            UPDATE users
            SET parrain = COALESCE(users.parrain, %s)
            WHERE user_id = %s;
            """, (str(parrain), str(user_id)))
        await conn.commit()


async def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Returns user as dict (or None).
    Fields align with your bot expectations.
    """
    async with conn_cm() as conn:
        row = await _fetchone(conn, """
            SELECT user_id, solde, last_bonus, bonus_days, cycle_end_date,
                   check_passed, welcome_bonus, parrain, bonus_claimed,
                   bonus_message_id, fake_count, blocked_until, has_withdrawn
            FROM users
            WHERE user_id = %s
        """, (str(user_id),))
    if not row:
        return None
    # row is a dict because row_factory=dict_row
    return {
        "user_id": row.get("user_id"),
        "solde": row.get("solde") or 0,
        "last_bonus": row.get("last_bonus"),
        "bonus_days": row.get("bonus_days") or 0,
        "cycle_end_date": row.get("cycle_end_date"),
        "check_passed": bool(row.get("check_passed")),
        "welcome_bonus": row.get("welcome_bonus") or 0,
        "parrain": row.get("parrain"),
        "bonus_claimed": bool(row.get("bonus_claimed")),
        "bonus_message_id": row.get("bonus_message_id"),
        "fake_count": row.get("fake_count") or 0,
        "blocked_until": row.get("blocked_until"),
        "has_withdrawn": bool(row.get("has_withdrawn"))
    }


async def update_user_field(user_id: str, field: str, value: Any):
    """
    Update a single field for a user. Use with care (field must be a valid column).
    """
    # Basic whitelist to avoid SQL injection via field name
    allowed = {
        "solde", "last_bonus", "bonus_days", "cycle_end_date", "check_passed",
        "welcome_bonus", "parrain", "bonus_claimed", "bonus_message_id",
        "fake_count", "blocked_until", "has_withdrawn"
    }
    if field not in allowed:
        raise ValueError(f"Field '{field}' not allowed to update")

    async with conn_cm() as conn:
        await _execute(conn, f"UPDATE users SET {field} = %s WHERE user_id = %s;", (value, str(user_id)))
        await conn.commit()


async def add_transaction(user_id: str, type_op: str, montant: int, conn=None):
    """
    Insert a transaction; if conn provided, use it (atomic operations).
    """
    date_now = datetime.utcnow()
    close_conn = False
    if conn is None:
        close_conn = True
        conn_ctx = conn_cm()
        conn = await conn_ctx.__aenter__()  # manual enter
    try:
        await _execute(conn, """
            INSERT INTO transactions (user_id, type, montant, date)
            VALUES (%s, %s, %s, %s);
        """, (str(user_id), type_op, int(montant), date_now))
        if close_conn:
            await conn.commit()
    finally:
        if close_conn:
            await conn_ctx.__aexit__(None, None, None)


async def add_solde(user_id: str, montant: int, type_op="Bonus"):
    async with conn_cm() as conn:
        # update solde
        await _execute(conn, "UPDATE users SET solde = COALESCE(solde,0) + %s WHERE user_id = %s;", (int(montant), str(user_id)))
        # add transaction
        await _execute(conn, "INSERT INTO transactions (user_id, type, montant) VALUES (%s,%s,%s);", (str(user_id), type_op, int(montant)))
        await conn.commit()


async def remove_solde(user_id: str, montant: int, type_op="Retrait Support"):
    async with conn_cm() as conn:
        row = await _fetchone(conn, "SELECT solde FROM users WHERE user_id = %s FOR UPDATE;", (str(user_id),))
        if not row:
            return False, "Utilisateur introuvable"
        current = row.get("solde") or 0
        if montant > current:
            return False, "Montant supérieur au solde utilisateur"
        await _execute(conn, "UPDATE users SET solde = solde - %s WHERE user_id = %s;", (int(montant), str(user_id)))
        await _execute(conn, "INSERT INTO transactions (user_id, type, montant) VALUES (%s,%s,%s);", (str(user_id), type_op, -int(montant)))
        await conn.commit()
    return True, None


async def get_filleuls_count(user_id: str) -> int:
    async with conn_cm() as conn:
        row = await _fetchone(conn, "SELECT COUNT(*) AS cnt FROM filleuls WHERE parrain_id = %s;", (str(user_id),))
        return int(row["cnt"]) if row else 0


async def mark_user_withdrawn(user_id: str):
    async with conn_cm() as conn:
        await _execute(conn, "UPDATE users SET has_withdrawn = TRUE WHERE user_id = %s;", (str(user_id),))
        await conn.commit()


# ------------ Channels helpers (used heavily in Cash Bet4) ------------
async def get_required_channels_all() -> List[Dict[str, Any]]:
    async with conn_cm() as conn:
        rows = await _fetchall(conn, "SELECT id, label, username, url, public_username, private_link FROM required_channels ORDER BY id ASC;")
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "username": r["username"],
            "url": r["url"],
            "public_username": r.get("public_username"),
            "private_link": r.get("private_link")
        } for r in rows
    ]


async def set_channel_link_by_id(cid: int, new_value: str):
    usr = None
    url = None
    t = new_value.strip()
    if t.startswith("https://t.me/"):
        usr = t.split("https://t.me/")[-1].strip().lstrip("@").split("?")[0]
        url = f"https://t.me/{usr}"
    else:
        usr = t.lstrip("@")
        url = f"https://t.me/{usr}"
    async with conn_cm() as conn:
        await _execute(conn, "UPDATE required_channels SET username=%s, url=%s WHERE id=%s;", (usr, url, int(cid)))
        await conn.commit()


async def clear_channel_link_by_id(cid: int):
    async with conn_cm() as conn:
        await _execute(conn, "UPDATE required_channels SET username=NULL, url=NULL, public_username=NULL, private_link=NULL WHERE id=%s;", (int(cid),))
        await conn.commit()


# ------------ Close pool (for graceful shutdown) ------------
async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ------------ Convenience: run migrations on import if desired ------------
# You may call these explicitly from your bot during startup (recommended):
# await init_db(); await init_channels_db()
#
# Do NOT run automatically at import time to avoid surprising behavior.
#
# Example usage in your main.py startup sequence:
#   from database import init_db, init_channels_db
#   await init_db()
#   await init_channels_db()
#
# End of file