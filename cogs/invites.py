import asyncio
import time
from collections import defaultdict

import discord
from discord.ext import commands

from database import get_db_connection

# Someone who leaves within this many seconds of joining counts as a "fake" invite
# instead of a normal "left" one.
FAKE_LEAVE_SECONDS = 600

# Column names allowed in update_invite_count (never build SQL from anything else).
COUNT_COLUMNS = {"normal": "normal", "left": "left", "fake": "fake"}

# How long we remember an invite that was used up (and auto-deleted) by a join.
EXHAUSTED_MEMORY_SECONDS = 15


class Invites(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> {code: (uses, max_uses, inviter_id)}
        self.invite_cache = {}
        # guild_id -> uses of the vanity URL
        self.vanity_cache = {}
        # guild_id -> {code: (inviter_id, timestamp)} for single-use style invites Discord deleted
        self.recent_exhausted = {}
        # One lock per server so two people joining at once can't confuse each other
        self._locks = defaultdict(asyncio.Lock)
        self._init_task = None

    async def cog_load(self):
        await self.check_db_schema()
        self._init_task = asyncio.create_task(self.init_caches())

    async def cog_unload(self):
        if self._init_task:
            self._init_task.cancel()

    # ------------------------------------------------------------------
    # SETUP
    # ------------------------------------------------------------------
    async def check_db_schema(self):
        """Creates the necessary tables for the invite system."""
        async with get_db_connection() as db:
            # Total counts per user
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_counts (
                    guild_id INTEGER,
                    user_id INTEGER,
                    normal INTEGER DEFAULT 0,
                    left INTEGER DEFAULT 0,
                    fake INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

            # Links a joined member to their inviter (inviter_id is 0 for vanity URL joins)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_tracker (
                    guild_id INTEGER,
                    member_id INTEGER,
                    inviter_id INTEGER,
                    code TEXT,
                    is_bot INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, member_id)
                )
            """)

            # Log channel settings
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_settings (
                    guild_id INTEGER PRIMARY KEY,
                    log_channel_id INTEGER
                )
            """)

            await db.commit()
        print("[Invites] Database tables checked/created.")

    async def init_caches(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self.update_caches(guild)

    @staticmethod
    async def fetch_invite_snapshot(guild):
        """Returns {code: (uses, max_uses, inviter_id)} for every current invite. May raise Forbidden."""
        invites = await guild.invites()
        return {
            invite.code: (invite.uses or 0, invite.max_uses or 0, invite.inviter.id if invite.inviter else None)
            for invite in invites
        }

    async def update_caches(self, guild):
        """Fetches and caches both normal and vanity invites for a server."""
        self.invite_cache[guild.id] = {}
        try:
            self.invite_cache[guild.id] = await self.fetch_invite_snapshot(guild)
        except discord.HTTPException:
            print(f"[Invites] Missing permissions to fetch invites for {guild.name} (needs Manage Server)")

        if "VANITY_URL" in guild.features and guild.vanity_url_code:
            try:
                vanity = await guild.vanity_invite()
                if vanity:
                    self.vanity_cache[guild.id] = vanity.uses
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------
    async def update_invite_count(self, guild_id, user_id, change_type, amount=1):
        column = COUNT_COLUMNS[change_type]  # only whitelisted column names reach the SQL below
        async with get_db_connection() as db:
            await db.execute(
                "INSERT OR IGNORE INTO invite_counts (guild_id, user_id) VALUES (?, ?)",
                (guild_id, user_id),
            )
            await db.execute(
                f"UPDATE invite_counts SET {column} = {column} + ? WHERE guild_id = ? AND user_id = ?",
                (amount, guild_id, user_id),
            )
            await db.commit()

    async def get_log_channel(self, guild):
        """Fetches the log channel object if set."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT log_channel_id FROM invite_settings WHERE guild_id = ?", (guild.id,))
            data = await cursor.fetchone()

        if data:
            return guild.get_channel(data[0])
        return None

    async def get_invites_count(self, guild_id, user_id):
        """Returns the total 'real' invites for a user."""
        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT normal, left, fake FROM invite_counts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            data = await cursor.fetchone()

        if not data:
            return 0
        return data[0] - data[1] - data[2]

    async def find_used_invite(self, guild):
        """
        Works out which invite a new member used.
        Returns (code, inviter_id). inviter_id is None for the vanity URL.
        Returns (None, None) if it can't be determined.
        """
        cached = self.invite_cache.setdefault(guild.id, {})

        try:
            current = await self.fetch_invite_snapshot(guild)
        except discord.HTTPException:
            current = None

        if current is not None:
            # 1. A normal invite whose use count went up.
            for code, (uses, max_uses, inviter_id) in current.items():
                old_uses = cached.get(code, (0, 0, None))[0]
                if uses > old_uses:
                    cached[code] = (uses, max_uses, inviter_id)  # only update the one we matched
                    return code, inviter_id

            # 2. An invite that vanished because this join used up its last use.
            for code, (uses, max_uses, inviter_id) in list(cached.items()):
                if code not in current and max_uses and uses + 1 >= max_uses:
                    cached.pop(code, None)
                    return code, inviter_id

        # 3. Same as above, but Discord's "invite deleted" event reached us before the join.
        recent = self.recent_exhausted.get(guild.id, {})
        now = time.time()
        for code, (inviter_id, stamp) in sorted(recent.items(), key=lambda kv: kv[1][1], reverse=True):
            if now - stamp <= EXHAUSTED_MEMORY_SECONDS:
                recent.pop(code, None)
                return code, inviter_id
        for code in [c for c, (_, stamp) in recent.items() if now - stamp > EXHAUSTED_MEMORY_SECONDS]:
            recent.pop(code, None)

        # 4. The server's vanity URL.
        if "VANITY_URL" in guild.features and guild.vanity_url_code:
            try:
                vanity = await guild.vanity_invite()
                if vanity and vanity.uses > self.vanity_cache.get(guild.id, 0):
                    self.vanity_cache[guild.id] = vanity.uses
                    return vanity.code, None
            except discord.HTTPException:
                pass

        # Couldn't tell: resync the cache so the next join starts from a clean state.
        if current is not None:
            self.invite_cache[guild.id] = current
        return None, None

    # ------------------------------------------------------------------
    # EVENTS
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self.update_caches(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.invite_cache.pop(guild.id, None)
        self.vanity_cache.pop(guild.id, None)
        self.recent_exhausted.pop(guild.id, None)
        self._locks.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if invite.guild is None:
            return
        self.invite_cache.setdefault(invite.guild.id, {})[invite.code] = (
            invite.uses or 0,
            invite.max_uses or 0,
            invite.inviter.id if invite.inviter else None,
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        if invite.guild is None:
            return
        cached = self.invite_cache.get(invite.guild.id, {}).pop(invite.code, None)
        if cached:
            uses, max_uses, inviter_id = cached
            # If it was one use away from its limit, it was most likely used up by a join
            # (rather than deleted by a person), so remember it for the join handler.
            if max_uses and uses + 1 >= max_uses:
                self.recent_exhausted.setdefault(invite.guild.id, {})[invite.code] = (inviter_id, time.time())

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        inviter_id = None
        code_used = None

        if member.bot:
            # Bots are added through OAuth2: the audit log tells us who added it.
            try:
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                    if entry.target and entry.target.id == member.id:
                        inviter_id = entry.user.id
                        code_used = "OAuth2"
                        break
            except discord.HTTPException:
                pass
        else:
            async with self._locks[guild.id]:
                code_used, inviter_id = await self.find_used_invite(guild)

        if not code_used:
            return

        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO invite_tracker (guild_id, member_id, inviter_id, code, is_bot)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild.id, member.id, inviter_id or 0, code_used, 1 if member.bot else 0),
            )
            await db.commit()

        # Only real users with a real inviter add to the invite count.
        if not member.bot and inviter_id:
            await self.update_invite_count(guild.id, inviter_id, "normal", 1)

        log_channel = await self.get_log_channel(guild)
        if not log_channel:
            return

        inviter_text = f"<@{inviter_id}>" if inviter_id else None

        if member.bot:
            embed = discord.Embed(
                description=f"**Bot Added**\n{member.mention} was added by {inviter_text or 'Unknown'}",
                color=discord.Color.orange(),
            )
            embed.set_author(name=member.name, icon_url=member.display_avatar.url)
            embed.set_footer(text="System Log")
        else:
            embed = discord.Embed(description=f"{member.mention} joined", color=discord.Color.green())
            embed.set_author(name="Member Joined", icon_url=member.display_avatar.url)

            if inviter_id:
                total_invites = await self.get_invites_count(guild.id, inviter_id)
                embed.add_field(name="Invited By", value=inviter_text, inline=True)
                embed.add_field(name="Code", value=f"`{code_used}`", inline=True)
                embed.add_field(name="Total Invites", value=f"{total_invites}", inline=True)
            else:
                embed.add_field(name="Invited By", value="Custom Server Link (Vanity)", inline=True)
                embed.add_field(name="Code", value=f"`{code_used}`", inline=True)

            embed.set_footer(text=f"ID: {member.id}")

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """When a user leaves, find their inviter, adjust the count and log it."""
        guild = member.guild

        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT inviter_id, is_bot FROM invite_tracker WHERE guild_id = ? AND member_id = ?",
                (guild.id, member.id),
            )
            data = await cursor.fetchone()

        if not data:
            return
        inviter_id, is_bot = data

        # Bots never added to the inviter's count, so they must not subtract from it either.
        # Vanity joins (inviter_id 0) have nobody to adjust.
        if inviter_id != 0 and not is_bot:
            stayed = None
            if member.joined_at:
                stayed = (discord.utils.utcnow() - member.joined_at).total_seconds()
            column = "fake" if stayed is not None and stayed < FAKE_LEAVE_SECONDS else "left"
            await self.update_invite_count(guild.id, inviter_id, column, 1)

        async with get_db_connection() as db:
            await db.execute(
                "DELETE FROM invite_tracker WHERE guild_id = ? AND member_id = ?",
                (guild.id, member.id),
            )
            await db.commit()

        log_channel = await self.get_log_channel(guild)
        if not log_channel:
            return

        if inviter_id == 0:
            inviter_text = "Custom Server Link (Vanity)"
        else:
            inviter = guild.get_member(inviter_id)
            inviter_text = inviter.mention if inviter else "Unknown/Left"

        embed = discord.Embed(description=f"{member.mention} left", color=discord.Color.red())
        embed.set_author(name="Member Left", icon_url=member.display_avatar.url)
        embed.add_field(name="Invited By", value=inviter_text, inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------
    # COMMANDS
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="setinvitelog", description="Set the channel where invite logs will be sent.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setinvitelog(self, ctx, channel: discord.TextChannel):
        if not channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.send(f"❌ I can't send messages in {channel.mention}.")

        async with get_db_connection() as db:
            await db.execute(
                "INSERT OR REPLACE INTO invite_settings (guild_id, log_channel_id) VALUES (?, ?)",
                (ctx.guild.id, channel.id),
            )
            await db.commit()
        await ctx.send(f"Logs will now be sent to {channel.mention}")

    @commands.hybrid_command(name="invites", description="Check how many people you have invited.")
    @commands.guild_only()
    async def invites(self, ctx, member: discord.Member = None):
        member = member or ctx.author

        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT normal, left, fake FROM invite_counts WHERE guild_id = ? AND user_id = ?",
                (ctx.guild.id, member.id),
            )
            data = await cursor.fetchone()

        normal, left, fake = data if data else (0, 0, 0)
        real_invites = normal - left - fake

        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=f"Invites for {member.display_name}", icon_url=member.display_avatar.url)
        embed.add_field(name="Real", value=str(real_invites), inline=True)
        embed.add_field(name="Left", value=str(left), inline=True)
        embed.add_field(name="Fake", value=str(fake), inline=True)
        embed.set_footer(text=f"Total Joins: {normal}")

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="addinvites", description="Admin: Add (or remove, with a negative number) bonus invites for a user.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def addinvites(self, ctx, member: discord.Member, amount: int):
        if amount == 0 or abs(amount) > 100000:
            return await ctx.send("❌ Amount must be between -100000 and 100000, and not 0.")

        await self.update_invite_count(ctx.guild.id, member.id, "normal", amount)
        verb = "Added" if amount > 0 else "Removed"
        await ctx.send(
            f"{verb} **{abs(amount)}** invites {'to' if amount > 0 else 'from'} {member.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    await bot.add_cog(Invites(bot))
