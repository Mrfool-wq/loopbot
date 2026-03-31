import aiosqlite

DB_NAME = "bot_database.db"

def get_db_connection():
    """Returns the connection context manager."""
    return aiosqlite.connect(DB_NAME)

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # --- EXISTING TABLES ---
        
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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS social_subs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                discord_channel_id INTEGER,
                platform TEXT,
                target_id TEXT,
                last_id TEXT,
                custom_message TEXT
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

        # --- NEW TABLES ---

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

        # database.py (Add this to the 'init_db' function inside the 'NEW TABLES' section)

        # 9. Backups
        # Stores server data as a JSON string
        await db.execute("""
            CREATE TABLE IF NOT EXISTS backups (
                backup_id TEXT PRIMARY KEY,
                user_id INTEGER,
                data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.commit()
    print("📂 Database tables checked/created.")
