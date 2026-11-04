import re
import time

import discord
from discord.ext import commands, tasks

from database import get_db_connection

MAX_SCHEDULES_PER_GUILD = 10
MIN_INTERVAL_MINUTES = 5
MAX_MESSAGE_LENGTH = 2000  # Discord's message limit

INTERVAL_RE = re.compile(r"^(\d+)([mhdw])$")
UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}


def parse_interval(text):
    """Converts '30m', '1h', '1d', '1w' into minutes. Returns None if invalid."""
    match = INTERVAL_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    return int(match.group(1)) * UNIT_MINUTES[match.group(2)]


def next_run_after(next_run, interval_minutes, now):
    """
    Next scheduled time strictly after `now`, staying on the original schedule.
    If the bot was offline for several intervals it skips the missed ones
    instead of spamming them.
    """
    step = interval_minutes * 60
    missed = int((now - next_run) // step) + 1
    return next_run + missed * step


class Announcement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.auto_announcement_task.start()

    def cog_unload(self):
        self.auto_announcement_task.cancel()

    # ------------------------------------------------------------------
    # COMMANDS
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="announce", description="Send a public announcement (plain text + optional image).")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        channel="Select the channel where the announcement will appear.",
        message="Type the content of your announcement.",
        image="Optional: Upload an image to attach to the message."
    )
    async def announce(self, ctx, channel: discord.TextChannel, message: str, image: discord.Attachment = None):
        """
        Sends a message to a channel as the bot.
        Usage: /announce #general "Hello Everyone!" (optional image)
        """
        await ctx.defer(ephemeral=True)

        author_perms = channel.permissions_for(ctx.author)
        if not author_perms.send_messages:
            return await ctx.send("❌ You don't have permission to speak in that channel.", ephemeral=True)

        if not channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.send("❌ I don't have permission to speak in that channel.", ephemeral=True)

        if len(message) > MAX_MESSAGE_LENGTH:
            return await ctx.send(f"❌ Message is too long (max {MAX_MESSAGE_LENGTH} characters).", ephemeral=True)

        # The bot must not become a way around Discord permissions:
        # only people who can ping @everyone/roles themselves can make the bot do it.
        can_mass_mention = author_perms.mention_everyone
        mentions = discord.AllowedMentions(everyone=can_mass_mention, roles=can_mass_mention, users=True)

        try:
            if image:
                file = await image.to_file()
                await channel.send(content=message, file=file, allowed_mentions=mentions)
            else:
                await channel.send(content=message, allowed_mentions=mentions)

            await ctx.send(f"✅ Announcement sent to {channel.mention}.", ephemeral=True)

        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to speak in that channel.", ephemeral=True)
        except discord.HTTPException as e:
            await ctx.send(f"❌ Discord rejected the message: {e}", ephemeral=True)

    @commands.hybrid_command(name="autoannounce", description="Schedule a recurring message (e.g., 1h, 1d, 1w).")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        channel="Select the channel for the recurring message.",
        interval="Set the frequency (e.g., 30m, 1h, 12h, 1d, 1w).",
        message="Type the message to be repeated."
    )
    async def autoannounce(self, ctx, channel: discord.TextChannel, interval: str, *, message: str):
        """
        Sets a message to repeat automatically.
        Usage: /autoannounce #general 1h Don't forget to vote!
        """
        await ctx.defer()

        minutes = parse_interval(interval)
        if minutes is None:
            return await ctx.send("❌ Invalid time format. Please use: `30m` (minutes), `1h` (hours), `1d` (days), or `1w` (weeks).")

        if minutes < MIN_INTERVAL_MINUTES:
            return await ctx.send(f"⚠️ Minimum interval is **{MIN_INTERVAL_MINUTES} minutes** (5m) to prevent spam.")

        if len(message) > MAX_MESSAGE_LENGTH:
            return await ctx.send(f"❌ Message is too long (max {MAX_MESSAGE_LENGTH} characters).")

        author_perms = channel.permissions_for(ctx.author)
        if not author_perms.send_messages:
            return await ctx.send("❌ You don't have permission to speak in that channel.")
        if not channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.send("❌ I don't have permission to speak in that channel.")

        # Same rule as /announce: scheduled messages can't ping people the author couldn't ping.
        note = ""
        if not author_perms.mention_everyone:
            cleaned = discord.utils.escape_mentions(message)
            if cleaned != message:
                note = "\nℹ️ Mentions were disabled in this message because you don't have the **Mention Everyone** permission."
            message = cleaned

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM scheduled_msgs WHERE guild_id = ?", (ctx.guild.id,))
            (count,) = await cursor.fetchone()
            if count >= MAX_SCHEDULES_PER_GUILD:
                return await ctx.send(
                    f"❌ This server already has {count} scheduled messages (max {MAX_SCHEDULES_PER_GUILD}). "
                    "Use `/stopannouncements` to remove some."
                )

            next_run = time.time() + (minutes * 60)
            await db.execute(
                """
                INSERT INTO scheduled_msgs (guild_id, channel_id, message_content, interval_minutes, next_run)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ctx.guild.id, channel.id, message, minutes, next_run),
            )
            await db.commit()

        await ctx.send(f"✅ Setup! I will send that message in {channel.mention} every **{interval}**.{note}")

    @commands.hybrid_command(name="stopannouncements", description="Stops all auto-announcements in a specific channel.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        channel="Select the channel to disable auto-announcements in."
    )
    async def stopannouncements(self, ctx, channel: discord.TextChannel):
        await ctx.defer()
        async with get_db_connection() as db:
            cursor = await db.execute(
                "DELETE FROM scheduled_msgs WHERE channel_id = ? AND guild_id = ?",
                (channel.id, ctx.guild.id),
            )
            await db.commit()
            removed = cursor.rowcount

        if removed == 0:
            return await ctx.send(f"ℹ️ There were no scheduled messages in {channel.mention}.")
        await ctx.send(f"🛑 Stopped **{removed}** scheduled message(s) in {channel.mention}.")

    # ------------------------------------------------------------------
    # BACKGROUND TASK
    # ------------------------------------------------------------------
    @tasks.loop(minutes=1)
    async def auto_announcement_task(self):
        """Checks the database every minute for messages to send. Errors never stop the loop."""
        now = time.time()

        try:
            async with get_db_connection() as db:
                cursor = await db.execute(
                    "SELECT id, channel_id, message_content, interval_minutes, next_run "
                    "FROM scheduled_msgs WHERE next_run <= ?",
                    (now,),
                )
                due = await cursor.fetchall()
        except Exception as e:
            print(f"[Announcement] Could not read schedule: {e}")
            return

        for db_id, channel_id, content, interval_min, next_run in due:
            try:
                # Move the schedule forward FIRST so a slow send can never cause a double post.
                async with get_db_connection() as db:
                    await db.execute(
                        "UPDATE scheduled_msgs SET next_run = ? WHERE id = ?",
                        (next_run_after(next_run, interval_min, now), db_id),
                    )
                    await db.commit()

                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except discord.NotFound:
                        # The channel was deleted: remove the dead schedule.
                        async with get_db_connection() as db:
                            await db.execute("DELETE FROM scheduled_msgs WHERE id = ?", (db_id,))
                            await db.commit()
                        print(f"[Announcement] Channel {channel_id} no longer exists; removed schedule {db_id}.")
                        continue

                await channel.send(content=content)

            except discord.Forbidden:
                print(f"[Announcement] Missing permission to post in channel {channel_id} (schedule {db_id}).")
            except Exception as e:
                print(f"[Announcement] Failed to run schedule {db_id}: {e}")

    @auto_announcement_task.before_loop
    async def before_task(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Announcement(bot))
