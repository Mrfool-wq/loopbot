import discord
from discord.ext import commands, tasks
import time
import re
from database import get_db_connection

class Announcement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.auto_announcement_task.start()

    def cog_unload(self):
        self.auto_announcement_task.cancel()

    def parse_time_to_minutes(self, time_str: str):
        """Converts strings like 1h, 1d, 1w into minutes."""
        time_str = time_str.lower()
        
        # Regex to capture the number and the unit
        match = re.match(r"(\d+)([mhdw])", time_str)
        if not match:
            return None
        
        value = int(match.group(1))
        unit = match.group(2)
        
        if unit == 'm': return value
        if unit == 'h': return value * 60
        if unit == 'd': return value * 1440
        if unit == 'w': return value * 10080
        
        return None

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
        
        try:
            # Check if user has permission in THAT specific channel
            if not channel.permissions_for(ctx.author).send_messages:
                await ctx.send("❌ You don't have permission to speak in that channel.", ephemeral=True)
                return

            if image:
                file = await image.to_file()
                await channel.send(content=message, file=file)
            else:
                await channel.send(content=message)
            
            await ctx.send(f"✅ Announcement sent to {channel.mention}.", ephemeral=True)
            
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to speak in that channel.", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Error: {e}", ephemeral=True)

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
        
        minutes = self.parse_time_to_minutes(interval)
        
        if minutes is None:
            await ctx.send("❌ Invalid time format. Please use: `30m` (minutes), `1h` (hours), `1d` (days), or `1w` (weeks).")
            return

        if minutes < 5:
            await ctx.send("⚠️ Minimum interval is **5 minutes** (5m) to prevent spam.")
            return

        next_run = time.time() + (minutes * 60)
        
        async with get_db_connection() as db:
            await db.execute("""
                INSERT INTO scheduled_msgs (guild_id, channel_id, message_content, interval_minutes, next_run)
                VALUES (?, ?, ?, ?, ?)
            """, (ctx.guild.id, channel.id, message, minutes, next_run))
            await db.commit()
            
        await ctx.send(f"✅ Setup! I will send that message in {channel.mention} every **{interval}**.")

    @commands.hybrid_command(name="stopannouncements", description="Stops all auto-announcements in a specific channel.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        channel="Select the channel to disable auto-announcements in."
    )
    async def stopannouncements(self, ctx, channel: discord.TextChannel):
        await ctx.defer()
        async with get_db_connection() as db:
            await db.execute("DELETE FROM scheduled_msgs WHERE channel_id = ?", (channel.id,))
            await db.commit()
        await ctx.send(f"🛑 Stopped all scheduled messages in {channel.mention}.")

    @tasks.loop(minutes=1)
    async def auto_announcement_task(self):
        """Checks database every minute for messages to send."""
        current_time = time.time()
        
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT id, channel_id, message_content, interval_minutes, next_run FROM scheduled_msgs")
            rows = await cursor.fetchall()
            
            for row in rows:
                db_id, channel_id, msg, interval_min, next_run = row
                
                if current_time >= next_run:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.send(content=msg)
                        except:
                            pass
                    
                    # Update next run time
                    new_next_run = current_time + (interval_min * 60)
                    await db.execute("UPDATE scheduled_msgs SET next_run = ? WHERE id = ?", (new_next_run, db_id))
            
            await db.commit()

    @auto_announcement_task.before_loop
    async def before_task(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Announcement(bot))
