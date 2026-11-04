import discord
from discord.ext import commands
import time
from database import get_db_connection

MAX_PURGE = 200
MAX_PREFIX_LENGTH = 5

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="setprefix", description="Change the bot prefix for this server.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setprefix(self, ctx, new_prefix: str):
        """Change the bot's prefix. Usage: !setprefix ?"""
        if not new_prefix.strip():
            await ctx.send("❌ The prefix can't be empty.")
            return

        if len(new_prefix) > MAX_PREFIX_LENGTH:
            await ctx.send(f"❌ Prefix is too long! Keep it under {MAX_PREFIX_LENGTH + 1} characters.")
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

        await ctx.send(f"✅ Prefix changed to `{new_prefix}`", allowed_mentions=discord.AllowedMentions.none())
        
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
    @commands.guild_only()
    async def serverinfo(self, ctx):
        guild = ctx.guild
        embed = discord.Embed(title=f"ℹ️ {guild.name}", color=discord.Color.blue())
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        # guild.owner can be None if the member isn't cached, so use the owner's ID instead
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "D"), inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="purge", description="Deletes messages (1-200).")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    @discord.app_commands.describe(amount=f"How many messages to delete (1-{MAX_PURGE})")
    async def purge(self, ctx, amount: int):
        if not 1 <= amount <= MAX_PURGE:
            await ctx.send(f"❌ Choose a number between 1 and {MAX_PURGE}.", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)

        if ctx.interaction:
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.send(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)
        else:
            # Text command: delete the messages BEFORE the command message, then the command itself,
            # so "!purge 5" removes 5 messages and not 4 plus the command.
            deleted = await ctx.channel.purge(limit=amount, before=ctx.message)
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=3)

    @commands.command(name="noprefix", aliases=["np"], description="Toggle no-prefix status for a user (Owner only).")
    @commands.is_owner()
    async def noprefix(self, ctx, user: discord.User):
        """Grants or removes no-prefix status for a specific user."""
        async with get_db_connection() as db:
            if user.id in self.bot.noprefix_cache:
                # User has it, so we remove it
                await db.execute("DELETE FROM noprefix_users WHERE user_id = ?", (user.id,))
                self.bot.noprefix_cache.remove(user.id)
                action = "removed from"
            else:
                # User doesn't have it, so we add it
                await db.execute("INSERT OR IGNORE INTO noprefix_users (user_id) VALUES (?)", (user.id,))
                self.bot.noprefix_cache.add(user.id)
                action = "added to"
            
            await db.commit()
            
        await ctx.send(f"✅ {user.mention} has been {action} the no-prefix list.")

    @commands.command(name="noprefixlist", aliases=["nplist", "npl"], description="List all users with no-prefix privileges (Owner only).")
    @commands.is_owner()
    async def noprefixlist(self, ctx):
        """Shows a list of all users who can use the bot without a prefix."""
        # Check if the cache is empty
        if not hasattr(self.bot, 'noprefix_cache') or not self.bot.noprefix_cache:
            await ctx.send("ℹ️ There are currently no users in the no-prefix list.")
            return

        # Format the list of users
        user_mentions = []
        for user_id in self.bot.noprefix_cache:
            user = self.bot.get_user(user_id)
            if user:
                user_mentions.append(f"• {user.mention} (`{user.name}`)")
            else:
                # Fallback if the bot can't find the user in its internal cache
                user_mentions.append(f"• <@{user_id}> (`{user_id}`)")

        # Create a clean embed
        embed = discord.Embed(
            title="✨ No-Prefix Users",
            description="\n".join(user_mentions),
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"Total users: {len(self.bot.noprefix_cache)}")
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Utility(bot))
