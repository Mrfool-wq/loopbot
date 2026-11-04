import aiosqlite

DB_NAME = "bot_database.db"

def get_db_connection():
    """
    Returns the connection context manager.
    timeout=30 makes a connection WAIT (up to 30s) when another one is writing,
    instead of failing with 'database is locked' after the 5s default.
    """
    return aiosqlite.connect(DB_NAME, timeout=30)

async def init_db():
    async with get_db_connection() as db:
        # WAL mode lets readers and a writer work at the same time, which matters
        # because listeners, background loops and commands all use the database.
        # (This setting is stored in the database file, so it only needs setting once.)
        await db.execute("PRAGMA journal_mode=WAL")

        # 1. Reactions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                guild_id INTEGER,
                channel_id INTEGER,
                trigger_text TEXT,
                emoji TEXT
            )
        """)

        # 2. Ghost Notes
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ghost_notes (
                channel_id INTEGER PRIMARY KEY,
                note TEXT,
                author_name TEXT
            )
        """)

        # 3. Reputation
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reputation (
                user_id INTEGER PRIMARY KEY,
                points INTEGER DEFAULT 0
            )
        """)

        # 4. Prefixes
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prefixes (
                guild_id INTEGER PRIMARY KEY,
                prefix TEXT
            )
        """)

        # 5. Giveaways
        # Stores active giveaways so they survive bot restarts
        await db.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                guild_id INTEGER,
                prize TEXT,
                winners_count INTEGER,
                end_time REAL
            )
        """)

        # 6. Social Media Notifications
        # platform: 'youtube', 'twitch', 'twitter'
        # target_id: The channel ID or Username on that platform
        # last_id: The ID of the last video/tweet seen (to prevent duplicates)
        # target_name: Display name of the channel (the Socials cog also adds this
        #              column to older databases automatically)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS social_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                discord_channel_id INTEGER,
                platform TEXT,
                target_id TEXT,
                last_id TEXT,
                custom_message TEXT,
                target_name TEXT DEFAULT 'Unknown'
            )
        """)

        # 7. Scheduled Announcements
        # interval_minutes: How often to repeat (e.g., 10080 for weekly)
        # next_run: Timestamp of when it should send next
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_msgs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                channel_id INTEGER,
                message_content TEXT,
                interval_minutes INTEGER,
                next_run REAL
            )
        """)

        # 8. Applications
        # Stores the channel ID linked to the user so we can manage the "ticket"
        # status: 'Open', 'Closed', 'Accepted', 'Rejected'
        await db.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                user_id INTEGER,
                app_type TEXT,
                status TEXT
            )
        """)

        # 9. Backups
        # Stores server data as a JSON string
        # guild_id: the server the backup belongs to (the Backup cog also adds this
        #           column to older databases automatically)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS backups (
                backup_id TEXT PRIMARY KEY,
                user_id INTEGER,
                data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                guild_id INTEGER
            )
        """)

        # 10. Mod Logs
        # Stores the designated channel ID for moderation logs per server
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mod_logs (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER
            )
        """)

        # 11. AFK Users
        # Stores users who are currently AFK, their reason, and timestamp
        await db.execute("""
            CREATE TABLE IF NOT EXISTS afk_users (
                user_id INTEGER,
                guild_id INTEGER,
                reason TEXT,
                timestamp REAL,
                PRIMARY KEY (user_id, guild_id)
            )
        """)

        # 12. No Prefix Users
        # Stores user IDs that can use the bot without a prefix
        await db.execute("""
            CREATE TABLE IF NOT EXISTS noprefix_users (
                user_id INTEGER PRIMARY KEY
            )
        """)

        # --- INDEXES (speed up the lookups the bot does most often) ---
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reactions_guild ON reactions (guild_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_end ON giveaways (end_time)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_next ON scheduled_msgs (next_run)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_social_target ON social_subs (target_id)")

        await db.commit()

    # NOTE: the invite tables are created by cogs/invites.py itself.
    print("📂 Database tables checked/created.")
