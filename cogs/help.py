import discord
from discord.ext import commands
from database import get_db_connection

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="help", description="Shows the command menu.")
    async def help(self, ctx):
        # --- FIX: Logic to get the REAL prefix ---
        if ctx.prefix != "/":
            # 1. If user typed "!help" or "?help", ctx.prefix is ALREADY correct.
            # (The bot wouldn't have responded otherwise)
            display_prefix = ctx.prefix
        else:
            # 2. If user used Slash Command (/help), we must check the DB.
            # (Because the bot's RAM cache might be empty after a restart)
            async with get_db_connection() as db:
                cursor = await db.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (ctx.guild.id,))
                data = await cursor.fetchone()
                
            display_prefix = data[0] if data else "!"
        # ------------------------------------------

        embed = discord.Embed(
            title="Bot Commands",
            description=f"**Current Prefix:** `{display_prefix}`\nUse `{display_prefix}command` or `/command`",
            color=discord.Color.green() 
        )

        category_map = {
            "AutoReact": "👮 Admin",
            "Utility": "🔧 Utility",
            "Fun": "🎉 Fun",
            "Engagement": "🤝 Social",
            "System": "💻 System"
        }

        for cog_name, cog in self.bot.cogs.items():
            if cog_name in ["Help", "ErrorHandler"]:
                continue

            command_list = ""
            for command in cog.get_commands():
                # We check command.hidden so !sync (which is now hidden) won't show up
                if not command.hidden:
                    if command.description:
                        desc = command.description
                    elif command.short_doc:
                        desc = command.short_doc
                    else:
                        desc = "No description"
                    
                    command_list += f"{command.name.ljust(12)} - {desc}\n"
            
            if command_list:
                header = category_map.get(cog_name, f"📁 {cog_name}")
                embed.add_field(
                    name=header, 
                    value=f"```text\n{command_list}```", 
                    inline=False
                )

        # Handle "Orphan" commands in main.py (if any are not hidden)
        uncogged = [cmd for cmd in self.bot.commands if cmd.cog is None and not cmd.hidden]
        if uncogged:
            sys_list = ""
            for command in uncogged:
                desc = command.description if command.description else "No description"
                sys_list += f"{command.name.ljust(12)} - {desc}\n"
            
            embed.add_field(name="💻 System", value=f"```text\n{sys_list}```", inline=False)

        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Help(bot))
