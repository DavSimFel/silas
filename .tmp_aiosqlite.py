import asyncio, tempfile, aiosqlite

async def main():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db=f.name
    print('db', db, flush=True)
    async with aiosqlite.connect(db) as conn:
        print('connected', flush=True)
        await conn.execute('create table if not exists t (id integer)')
        await conn.commit()
        print('done', flush=True)

asyncio.run(main())
