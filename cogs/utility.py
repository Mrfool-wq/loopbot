import discord
from discord.ext import commands
import time
from database import get_db_connection

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="setprefix", description="Change the bot prefix for this server.")
    @commands.has_permissions(administrator=True)
    async def setprefix(self, ctx, new_prefix: str):
        """Change the bot's prefix. Usage: !setprefix ?"""
        if len(new_prefix) > 5:
            await ctx.send("❌ Prefix is too long! Keep it under 5 characters.")
            return

        # 1. Update Database
        async with get_db_connection() as db:
            await db.execute(
                "INSERT OR REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)",
                (ctx.guild.id, new_prefix)
            )
            await db.commit()

        # 2. Update RAM Cache DIRECTLY on the bot object
        # This fixes the issue where the cache wasn't updating for the main loop
        self.bot.prefix_cache[ctx.guild.id] = new_prefix

        await ctx.send(f"✅ Prefix changed to `{new_prefix}`")
        
        if ctx.interaction:
            await ctx.send("(Note: Slash commands `/` always work regardless of prefix)", ephemeral=True)

    @commands.hybrid_command(name="ping", description="Checks the bot's latency.")
    async def ping(self, ctx):
        start = time.time()
        msg = await ctx.send("🏓 Pinging...")
        end = time.time()
        latency = round(self.bot.latency * 1000)
        api = round((end - start) * 1000)
        await msg.edit(content=f"🏓 **Pong!**\nWebsocket: `{latency}ms`\nAPI: `{api}ms`")

    @commands.hybrid_command(name="serverinfo", description="Displays information about this server.")
    async def serverinfo(self, ctx):
        guild = ctx.guild
        embed = discord.Embed(title=f"ℹ️ {guild.name}", color=discord.Color.blue())
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="purge", description="Deletes messages.")
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx, amount: int):
        await ctx.defer(ephemeral=True)
        deleted = await ctx.channel.purge(limit=amount)
        await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=3)

async def setup(bot):
    await bot.add_cog(Utility(bot))
