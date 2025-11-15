# Fake aiosqlite using PostgreSQL psycopg
import os
import psycopg
from psycopg.rows import tuple_row

DB_URL = os.getenv("POSTGRES_URL")

class FakeCursor:
    def __init__(self, cur):
        self.cur = cur

    async def execute(self, *args):
        await self.cur.execute(*args)
        return self

    async def fetchall(self):
        return await self.cur.fetchall()

    async def fetchone(self):
        return await self.cur.fetchone()

class FakeConnection:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, *args):
        async with self.conn.cursor(row_factory=tuple_row) as cur:
            await cur.execute(*args)
            return FakeCursor(cur)

    async def commit(self):
        await self.conn.commit()

    async def close(self):
        await self.conn.close()

async def connect(_):
    conn = await psycopg.AsyncConnection.connect(DB_URL)
    return FakeConnection(conn)
