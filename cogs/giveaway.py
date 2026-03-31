import discord
from discord.ext import commands, tasks
import asyncio
import random
import time
from database import get_db_connection

class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_giveaways.start()

    def convert_time(self, time_str):
        """Converts 10s, 1m, 1h, 1d to seconds."""
        unit = time_str[-1]
        if unit not in ['s', 'm', 'h', 'd']:
            return None
        try:
            val = int(time_str[:-1])
        except ValueError:
            return None

        if unit == 's': return val
        if unit == 'm': return val * 60
        if unit == 'h': return val * 3600
        if unit == 'd': return val * 86400
        return None

    @commands.hybrid_command(name="gstart", description="Start a giveaway.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        duration="How long should it last? (e.g., 30s, 10m, 1h, 1d)",
        winners="How many winners will be picked?",
        prize="What is the prize being given away?"
    )
    async def gstart(self, ctx, duration: str, winners: int, *, prize: str):
        """
        Usage: !gstart 10m 1 Nitro Classic
        Duration: s (seconds), m (minutes), h (hours), d (days)
        """
        await ctx.defer()
        
        seconds = self.convert_time(duration)
        if seconds is None:
            await ctx.send("❌ Invalid duration. Use formats like `10s`, `1m`, `2h`, `1d`.")
            return

        end_time = time.time() + seconds

        embed = discord.Embed(title="<a:gift_cool:1458662163190255708> GIVEAWAY <a:gift_cool:1458662163190255708>", description=f"**Prize:** {prize}\n**Winners:** {winners}\n**React with <a:giveaway:1461382837562507356> to enter!**", color=discord.Color.purple())
        embed.add_field(name="Ends In", value=f"<t:{int(end_time)}:R>")
        embed.set_footer(text=f"Hosted by {ctx.author.display_name}")

        msg = await ctx.send(embed=embed)
        await msg.add_reaction("<a:giveaway:1461382837562507356>")

        # Save to DB
        async with get_db_connection() as db:
            await db.execute("""
                INSERT INTO giveaways (message_id, channel_id, guild_id, prize, winners_count, end_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (msg.id, ctx.channel.id, ctx.guild.id, prize, winners, end_time))
            await db.commit()

    @commands.hybrid_command(name="gend", description="End a giveaway immediately.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        message_id="The ID of the giveaway message you want to end."
    )
    async def gend(self, ctx, message_id: str):
        await ctx.defer()
        try:
            msg_id = int(message_id)
        except ValueError:
            await ctx.send("❌ Invalid Message ID.")
            return

        async with get_db_connection() as db:
            # Get giveaway data
            cursor = await db.execute("SELECT * FROM giveaways WHERE message_id = ?", (msg_id,))
            data = await cursor.fetchone()
            
            if not data:
                await ctx.send("❌ Giveaway not found in database.")
                return

            # Force end time to now to trigger the loop immediately or handle logic here
            # For simplicity, let's call the end logic directly
            await self.end_giveaway_logic(data)
            
            # Remove from DB
            await db.execute("DELETE FROM giveaways WHERE message_id = ?", (msg_id,))
            await db.commit()
            
        await ctx.send("✅ Giveaway ended.", ephemeral=True)

    @commands.hybrid_command(name="greroll", description="Reroll a giveaway winner.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        channel="The channel where the giveaway is located.",
        message_id="The ID of the giveaway message to reroll."
    )
    async def greroll(self, ctx, channel: discord.TextChannel, message_id: str):
        await ctx.defer()
        try:
            msg = await channel.fetch_message(int(message_id))
        except:
            await ctx.send("❌ Couldn't find that message.")
            return

        users = []
        async for user in msg.reactions[0].users():
            if not user.bot:
                users.append(user)

        if not users:
            await ctx.send("❌ No valid entries to reroll.")
            return

        winner = random.choice(users)
        await ctx.send(f"<a:giveaway:1461382837562507356> The new winner is {winner.mention}!")

    async def end_giveaway_logic(self, data):
        """Handles the logic of picking a winner."""
        msg_id, channel_id, guild_id, prize, winners_count, _ = data
        
        channel = self.bot.get_channel(channel_id)
        if not channel: return # Channel deleted

        try:
            msg = await channel.fetch_message(msg_id)
        except:
            return # Message deleted

        users = []
        # reaction lookup
        reaction = None
        for r in msg.reactions:
            if str(r.emoji) == "<a:giveaway:1461382837562507356>":
                reaction = r
                break
        
        if not reaction:
            await channel.send(f"⚠️ Error: Reaction missing on giveaway for **{prize}**.")
            return

        async for user in reaction.users():
            if not user.bot:
                users.append(user)

        if len(users) == 0:
            await channel.send(f"<a:profile_01:1458763510098952339> No one entered the giveaway for **{prize}**.")
        else:
            # Pick unique winners
            if len(users) >= winners_count:
                winners = random.sample(users, winners_count)
            else:
                winners = users # Everyone wins if not enough entries
            
            winner_mentions = ", ".join([w.mention for w in winners])
            
            await channel.send(f"<a:giveaway:1461382837562507356> **CONGRATULATIONS!** <a:giveaway:1461382837562507356>\n{winner_mentions}\nYou won **{prize}**!")
            
            # Update original embed to say ENDED
            embed = msg.embeds[0]
            embed.description = f"**Prize:** {prize}\n**Winner(s):** {winner_mentions}\n**Ended**"
            embed.color = discord.Color.dark_grey()
            await msg.edit(embed=embed)

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        """Checks DB for ended giveaways."""
        current_time = time.time()
        
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT * FROM giveaways")
            giveaways = await cursor.fetchall()

            for data in giveaways:
                # data = (msg_id, chan_id, guild_id, prize, winners, end_time)
                end_time = data[5]
                msg_id = data[0]

                if current_time >= end_time:
                    await self.end_giveaway_logic(data)
                    await db.execute("DELETE FROM giveaways WHERE message_id = ?", (msg_id,))
            
            await db.commit()

    @check_giveaways.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Giveaway(bot))
