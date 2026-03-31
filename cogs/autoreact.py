import discord
from discord.ext import commands
from database import get_db_connection

class AutoReact(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="setreact", description="Sets an auto-reaction. Leave channel empty for Global.")
    @commands.has_permissions(manage_channels=True)
    @discord.app_commands.describe(
        trigger="The word to watch for", 
        emoji="The emoji to react with",
        channel="Optional: Specific channel. Leave empty for whole server."
    )
    async def setreact(self, ctx, trigger: str, emoji: str, channel: discord.TextChannel = None):
        """
        Usage: 
        !setreact pizza 🍕          (Works in ALL channels)
        !setreact pizza 🍕 #general (Works ONLY in #general)
        """
        await ctx.defer()
        
        # If channel is None, it's Global (we save None/NULL in DB)
        channel_id = channel.id if channel else None
        location_text = channel.mention if channel else "**Every Channel**"

        async with get_db_connection() as db:
            # Check for duplicates
            # We look for NULL explicitly if global, or specific ID if local
            if channel_id is None:
                query = "SELECT * FROM reactions WHERE guild_id = ? AND channel_id IS NULL AND trigger_text = ?"
                params = (ctx.guild.id, trigger.lower())
            else:
                query = "SELECT * FROM reactions WHERE guild_id = ? AND channel_id = ? AND trigger_text = ?"
                params = (ctx.guild.id, channel_id, trigger.lower())

            cursor = await db.execute(query, params)
            if await cursor.fetchone():
                await ctx.send(f"⚠️ Trigger `{trigger}` already exists for {location_text}.")
                return

            await db.execute(
                "INSERT INTO reactions (guild_id, channel_id, trigger_text, emoji) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, channel_id, trigger.lower(), emoji)
            )
            await db.commit()
            
        await ctx.send(f"✅ Setup! When `{trigger}` is seen in {location_text}, I will react with {emoji}")

    @commands.hybrid_command(name="delreact", description="Removes an auto-reaction.")
    @commands.has_permissions(manage_channels=True)
    async def delreact(self, ctx, trigger: str, channel: discord.TextChannel = None):
        await ctx.defer()
        
        channel_id = channel.id if channel else None
        
        async with get_db_connection() as db:
            if channel_id is None:
                # Delete Global
                await db.execute(
                    "DELETE FROM reactions WHERE guild_id = ? AND channel_id IS NULL AND trigger_text = ?",
                    (ctx.guild.id, trigger.lower())
                )
            else:
                # Delete Local
                await db.execute(
                    "DELETE FROM reactions WHERE guild_id = ? AND channel_id = ? AND trigger_text = ?",
                    (ctx.guild.id, channel_id, trigger.lower())
                )
            await db.commit()
            
        await ctx.send(f"🗑️ Removed reaction trigger `{trigger}`.")

    @commands.hybrid_command(name="reactlist", description="Show all active reactions for this server.")
    async def reactlist(self, ctx):
        await ctx.defer()
        
        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT channel_id, trigger_text, emoji FROM reactions WHERE guild_id = ?", 
                (ctx.guild.id,)
            )
            rows = await cursor.fetchall()

        if not rows:
            await ctx.send("💤 No auto-reactions set for this server.")
            return

        embed = discord.Embed(title="Active Auto-Reactions", color=discord.Color.blue())
        
        global_list = ""
        local_list = ""

        for channel_id, trigger, emoji in rows:
            if channel_id is None:
                global_list += f"• `{trigger}` → {emoji}\n"
            else:
                # Using <#ID> creates a clickable channel link
                local_list += f"• <#{channel_id}>: `{trigger}` → {emoji}\n"

        if global_list:
            embed.add_field(name="🌍 Global (Every Channel)", value=global_list, inline=False)
        if local_list:
            embed.add_field(name="📍 Channel Specific", value=local_list, inline=False)
            
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return

        # Need to check for TWO types of triggers:
        # 1. Global triggers (channel_id is NULL)
        # 2. Local triggers (channel_id matches this channel)
        
        async with get_db_connection() as db:
            cursor = await db.execute("""
                SELECT trigger_text, emoji FROM reactions 
                WHERE guild_id = ? AND (channel_id IS NULL OR channel_id = ?)
            """, (message.guild.id, message.channel.id))
            rows = await cursor.fetchall()

        if not rows: return

        content = message.content.lower()
        for trigger, emoji in rows:
            if trigger in content:
                try: await message.add_reaction(emoji)
                except: pass

async def setup(bot):
    await bot.add_cog(AutoReact(bot))
