import discord
from discord.ext import commands
import sys
import traceback

class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Triggered when ANY command fails."""
        
        # If the command has its own specific error handler, let that handle it.
        if hasattr(ctx.command, 'on_error'):
            return

        # Unwrap the error (get the real cause)
        error = getattr(error, 'original', error)

        # 1. Ignore "Command Not Found" (Prevent console spam)
        if isinstance(error, commands.CommandNotFound):
            return

        # 2. Check for "Not Owner" (The fix you asked for)
        if isinstance(error, commands.NotOwner):
            embed = discord.Embed(
                description="❌ **Only the bot owner can use this command.**",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        # 3. Permission Check
        if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
            missing = [perm.replace('_', ' ').title() for perm in error.missing_permissions]
            embed = discord.Embed(
                title="🚫 Access Denied",
                description=f"You need the **{', '.join(missing)}** permission.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        # 4. Missing Arguments
        if isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="⚠️ Missing Input",
                description=f"You forgot to include: `{error.param.name}`",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        # 5. Cooldowns
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Chill! Try again in {round(error.retry_after, 1)}s.", ephemeral=True)
            return

        # 6. FATAL ERRORS (The Anti-Crash Logic)
        print(f"⚠️ IGNORING EXCEPTION IN COMMAND {ctx.command}:", file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
        
        embed = discord.Embed(
            title="💥 Error",
            description="Something went wrong internally. I have alerted the developer.",
            color=discord.Color.dark_red()
        )
        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))
