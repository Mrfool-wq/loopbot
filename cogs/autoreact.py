import re

import discord
from discord.ext import commands

from database import get_db_connection

MAX_TRIGGERS_PER_GUILD = 50
MAX_REACTIONS_PER_MESSAGE = 5
MAX_TRIGGER_LENGTH = 100
CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w{2,32}:(\d{17,20})>$")


def build_pattern(trigger):
    """
    Builds the regex for a trigger. By default it matches WHOLE words only,
    so 'kill' no longer fires inside 'skill'.
      kill*  -> also matches 'killed', 'killing' (open on the right)
      *kill  -> also matches 'overkill'          (open on the left)
    Returns None if the trigger is empty.
    """
    text = trigger
    start, end = r"(?<!\w)", r"(?!\w)"
    if text.startswith("*"):
        start, text = "", text[1:]
    if text.endswith("*"):
        end, text = "", text[:-1]
    if not text:
        return None
    return re.compile(start + re.escape(text) + end, re.IGNORECASE)


def add_chunked_field(embed, name, lines):
    """Adds lines to an embed, splitting into several fields to respect Discord's 1024-char limit."""
    chunk, first = "", True
    for line in lines:
        if len(chunk) + len(line) > 1000:
            embed.add_field(name=name if first else "\u200b", value=chunk, inline=False)
            chunk, first = "", False
        chunk += line
    if chunk:
        embed.add_field(name=name if first else "\u200b", value=chunk, inline=False)


class AutoReact(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}  # guild_id -> [(channel_id or None, compiled_pattern, emoji)]

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------
    async def get_rules(self, guild_id):
        """Returns this server's triggers from memory, loading them from the DB only once."""
        rules = self.cache.get(guild_id)
        if rules is None:
            async with get_db_connection() as db:
                cursor = await db.execute(
                    "SELECT channel_id, trigger_text, emoji FROM reactions WHERE guild_id = ?", (guild_id,)
                )
                rows = await cursor.fetchall()

            rules = []
            for channel_id, trigger, emoji in rows:
                pattern = build_pattern(trigger)
                if pattern is not None:
                    rules.append((channel_id, pattern, emoji))
            self.cache[guild_id] = rules
        return rules

    def invalidate(self, guild_id):
        self.cache.pop(guild_id, None)

    async def validate_emoji(self, ctx, emoji):
        """Returns an error message if the emoji can't be used, otherwise None."""
        match = CUSTOM_EMOJI_RE.match(emoji)
        if match:
            if self.bot.get_emoji(int(match.group(1))) is None:
                return "❌ I can't use that custom emoji. I must be in the server it belongs to."
            return None

        if emoji.isascii() or len(emoji) > 20 or " " in emoji:
            return "❌ That doesn't look like an emoji. Use a normal emoji (🍕) or a custom one from a server I'm in."

        # For text commands we can test it for real by reacting to the command message.
        if ctx.interaction is None and ctx.message is not None:
            try:
                await ctx.message.add_reaction(emoji)
            except discord.Forbidden:
                pass  # can't test here, accept it
            except discord.HTTPException:
                return "❌ Discord doesn't accept that as an emoji."
        return None

    # ------------------------------------------------------------------
    # COMMANDS
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="setreact", description="Sets an auto-reaction. Leave channel empty for Global.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @discord.app_commands.describe(
        trigger="The word to watch for. Whole words only; use kill* to also match killed, killing...",
        emoji="The emoji to react with",
        channel="Optional: Specific channel. Leave empty for whole server."
    )
    async def setreact(self, ctx, trigger: str, emoji: str, channel: discord.TextChannel = None):
        """
        Usage: 
        !setreact pizza 🍕          (Works in ALL channels)
        !setreact pizza 🍕 #general (Works ONLY in #general)
        !setreact kill* 💀          (Also matches killed, killing...)
        """
        await ctx.defer()

        trigger = trigger.strip().lower()
        emoji = emoji.strip()

        if build_pattern(trigger) is None:
            return await ctx.send("❌ The trigger can't be empty.")
        if len(trigger) > MAX_TRIGGER_LENGTH:
            return await ctx.send(f"❌ Trigger is too long (max {MAX_TRIGGER_LENGTH} characters).")

        error = await self.validate_emoji(ctx, emoji)
        if error:
            return await ctx.send(error)

        # If channel is None, it's Global (we save NULL in the DB)
        channel_id = channel.id if channel else None
        location_text = channel.mention if channel else "**Every Channel**"

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM reactions WHERE guild_id = ?", (ctx.guild.id,))
            (count,) = await cursor.fetchone()
            if count >= MAX_TRIGGERS_PER_GUILD:
                return await ctx.send(f"❌ This server already has {count} auto-reactions (max {MAX_TRIGGERS_PER_GUILD}). Remove some with `/delreact`.")

            if channel_id is None:
                query = "SELECT 1 FROM reactions WHERE guild_id = ? AND channel_id IS NULL AND trigger_text = ?"
                params = (ctx.guild.id, trigger)
            else:
                query = "SELECT 1 FROM reactions WHERE guild_id = ? AND channel_id = ? AND trigger_text = ?"
                params = (ctx.guild.id, channel_id, trigger)

            cursor = await db.execute(query, params)
            if await cursor.fetchone():
                return await ctx.send(f"⚠️ Trigger `{trigger}` already exists for {location_text}.")

            await db.execute(
                "INSERT INTO reactions (guild_id, channel_id, trigger_text, emoji) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, channel_id, trigger, emoji)
            )
            await db.commit()

        self.invalidate(ctx.guild.id)
        await ctx.send(f"✅ Setup! When `{trigger}` is seen in {location_text}, I will react with {emoji}")

    @commands.hybrid_command(name="delreact", description="Removes an auto-reaction.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @discord.app_commands.describe(
        trigger="The trigger to remove (exactly as shown in /reactlist)",
        channel="Optional: the channel it was set for. Leave empty for a global trigger."
    )
    async def delreact(self, ctx, trigger: str, channel: discord.TextChannel = None):
        await ctx.defer()

        trigger = trigger.strip().lower()
        channel_id = channel.id if channel else None

        async with get_db_connection() as db:
            if channel_id is None:
                cursor = await db.execute(
                    "DELETE FROM reactions WHERE guild_id = ? AND channel_id IS NULL AND trigger_text = ?",
                    (ctx.guild.id, trigger)
                )
            else:
                cursor = await db.execute(
                    "DELETE FROM reactions WHERE guild_id = ? AND channel_id = ? AND trigger_text = ?",
                    (ctx.guild.id, channel_id, trigger)
                )
            await db.commit()
            removed = cursor.rowcount

        if removed == 0:
            return await ctx.send(f"❌ No trigger `{trigger}` found there. Check `/reactlist` and the channel.")

        self.invalidate(ctx.guild.id)
        await ctx.send(f"🗑️ Removed reaction trigger `{trigger}`.")

    @commands.hybrid_command(name="reactlist", description="Show all active reactions for this server.")
    @commands.guild_only()
    async def reactlist(self, ctx):
        await ctx.defer()

        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT channel_id, trigger_text, emoji FROM reactions WHERE guild_id = ?",
                (ctx.guild.id,)
            )
            rows = await cursor.fetchall()

        if not rows:
            return await ctx.send("💤 No auto-reactions set for this server.")

        embed = discord.Embed(title="Active Auto-Reactions", color=discord.Color.blue())

        global_lines, local_lines = [], []
        for channel_id, trigger, emoji in rows:
            if channel_id is None:
                global_lines.append(f"• `{trigger}` → {emoji}\n")
            else:
                # <#ID> creates a clickable channel link
                local_lines.append(f"• <#{channel_id}>: `{trigger}` → {emoji}\n")

        add_chunked_field(embed, "🌍 Global (Every Channel)", global_lines)
        add_chunked_field(embed, "📍 Channel Specific", local_lines)

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # LISTENERS
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None or not message.content:
            return

        rules = await self.get_rules(message.guild.id)
        if not rules:
            return

        # Threads count as part of their parent channel
        channel_ids = {message.channel.id, getattr(message.channel, "parent_id", None)}

        emojis = []
        for channel_id, pattern, emoji in rules:
            if channel_id is not None and channel_id not in channel_ids:
                continue
            if emoji not in emojis and pattern.search(message.content):
                emojis.append(emoji)
                if len(emojis) >= MAX_REACTIONS_PER_MESSAGE:
                    break

        if not emojis:
            return

        perms = message.channel.permissions_for(message.guild.me)
        if not (perms.add_reactions and perms.read_message_history):
            return

        for emoji in emojis:
            try:
                await message.add_reaction(emoji)
            except (discord.Forbidden, discord.NotFound):
                return  # blocked by the user, or the message is gone: stop trying
            except discord.HTTPException:
                continue  # e.g. an emoji that no longer exists: skip it

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.invalidate(guild.id)


async def setup(bot):
    await bot.add_cog(AutoReact(bot))
