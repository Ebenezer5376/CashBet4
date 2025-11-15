import os
import psycopg
from psycopg.rows import dict_row

DB_URL = os.getenv("POSTGRES_URL")

async def get_conn():
    return await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row)

async def run_query(query, params=None, fetch=False, fetchone=False):
    if params is None:
        params = ()

    conn = await get_conn()
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params)

            if fetch:
                return await cur.fetchall()
            if fetchone:
                return await cur.fetchone()

            await conn.commit()
