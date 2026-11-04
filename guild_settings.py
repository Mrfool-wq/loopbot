"""
Per-server settings shared by several cogs (set with /setup, read by applications.py).

Settings that already had their own table (prefix, mod log channel, invite log channel)
are still stored there so /setmodlog, /setinvitelog and /setprefix keep working.
"""
from database import get_db_connection

# Servers that used the old hardcoded setup still have a role with this exact name.
# It is accepted as the staff role until a staff role is chosen in /setup.
LEGACY_STAFF_ROLE_NAME = "Staff team」"

# Only these column names can ever reach the SQL below.
FIELDS = ("staff_role_id", "application_log_channel_id")

_cache = {}  # guild_id -> {field: value}
_table_ready = False


async def ensure_table():
    """Creates the table if it doesn't exist yet (safe to call any number of times)."""
    global _table_ready
    if _table_ready:
        return
    async with get_db_connection() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                staff_role_id INTEGER,
                application_log_channel_id INTEGER
            )
        """)
        await db.commit()
    _table_ready = True


async def get_settings(guild_id):
    """Returns {'staff_role_id': int|None, 'application_log_channel_id': int|None}."""
    cached = _cache.get(guild_id)
    if cached is not None:
        return dict(cached)

    await ensure_table()
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT staff_role_id, application_log_channel_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()

    settings = {
        "staff_role_id": row[0] if row else None,
        "application_log_channel_id": row[1] if row else None,
    }
    _cache[guild_id] = settings
    return dict(settings)


async def set_setting(guild_id, field, value):
    """Saves one setting. Pass value=None to clear it."""
    if field not in FIELDS:
        raise ValueError(f"Unknown setting: {field}")

    await ensure_table()
    async with get_db_connection() as db:
        await db.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
        await db.execute(f"UPDATE guild_settings SET {field} = ? WHERE guild_id = ?", (value, guild_id))
        await db.commit()
    _cache.pop(guild_id, None)
