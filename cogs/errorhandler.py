import os
import sys
import traceback
import uuid

import discord
from discord import app_commands
from discord.ext import commands

IGNORE = object()  # sentinel: errors we deliberately say nothing about


def perm_names(perms):
    return ", ".join(p.replace("_", " ").title() for p in perms)


def make_embed(title, description, color):
    return discord.Embed(title=title, description=description, color=color)


class ErrorHandler(commands.Cog):
    """Central error handling for prefix commands, hybrid commands and pure slash commands."""

    def __init__(self, bot):
        self.bot = bot
        self._original_tree_error = None

    async def cog_load(self):
        # Pure slash commands (like /backup-*) never reach on_command_error;
        # their errors go to the command tree, so we hook it here.
        self._original_tree_error = self.bot.tree.on_error
        self.bot.tree.on_error = self.on_app_command_error

    async def cog_unload(self):
        if self._original_tree_error is not None:
            self.bot.tree.on_error = self._original_tree_error

    # ------------------------------------------------------------------
    # Translating errors into friendly messages
    # ------------------------------------------------------------------
    def describe(self, error):
        """Returns (title, description, color) for a known error, IGNORE, or None if unknown."""
        red = discord.Color.red()
        orange = discord.Color.orange()

        if isinstance(error, (commands.CommandNotFound, app_commands.CommandNotFound)):
            return IGNORE

        if isinstance(error, commands.NotOwner):
            return "🔒 Owner Only", "Only the bot owner can use this command.", red

        if isinstance(error, (commands.MissingPermissions, app_commands.MissingPermissions)):
            return "🚫 Access Denied", f"You need the **{perm_names(error.missing_permissions)}** permission.", red

        if isinstance(error, (commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
            return "🚫 I'm Missing Permissions", f"I need the **{perm_names(error.missing_permissions)}** permission to do that.", red

        if isinstance(error, discord.Forbidden):
            return "🚫 Missing Access", "I don't have permission to do that. Check my permissions and that my role is high enough.", red

        if isinstance(error, (commands.MissingRole, app_commands.MissingRole)):
            return "🚫 Access Denied", f"You need the **{error.missing_role}** role.", red

        if isinstance(error, (commands.MissingAnyRole, app_commands.MissingAnyRole)):
            roles = ", ".join(str(r) for r in error.missing_roles)
            return "🚫 Access Denied", f"You need one of these roles: **{roles}**.", red

        if isinstance(error, (commands.NoPrivateMessage, app_commands.NoPrivateMessage)):
            return "🏠 Server Only", "This command can only be used inside a server.", orange

        if isinstance(error, commands.MissingRequiredArgument):
            return "⚠️ Missing Input", f"You forgot to include: `{error.param.name}`", orange

        if isinstance(error, (commands.MemberNotFound, commands.UserNotFound, commands.ChannelNotFound, commands.RoleNotFound)):
            return "🔎 Not Found", f"I couldn't find `{getattr(error, 'argument', 'that')}`.", orange

        if isinstance(error, commands.BadArgument):
            return "⚠️ Invalid Input", str(error)[:300] or "One of your inputs is invalid.", orange

        if isinstance(error, commands.UserInputError):
            return "⚠️ Invalid Input", str(error)[:300] or "Check your command and try again.", orange

        if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
            return "⏳ Slow Down", f"Try again in **{round(error.retry_after, 1)}s**.", orange

        if isinstance(error, commands.MaxConcurrencyReached):
            return "⏳ Busy", "Too many people are using this command right now. Try again shortly.", orange

        if isinstance(error, commands.DisabledCommand):
            return "⛔ Disabled", "This command is currently disabled.", orange

        # Generic failed check (keep AFTER the specific check errors above)
        if isinstance(error, (commands.CheckFailure, app_commands.CheckFailure)):
            return "🚫 Not Allowed", "You can't use this command here.", red

        return None

    async def report_error(self, error, where, user, guild):
        """Logs an unexpected error and returns a short ID the user can quote."""
        error_id = uuid.uuid4().hex[:8]
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        print(f"⚠️ [{error_id}] Unhandled error in {where}:", file=sys.stderr)
        print(tb, file=sys.stderr)

        # Optional: also post it to a Discord channel (set ERROR_LOG_CHANNEL_ID in .env)
        raw = os.getenv("ERROR_LOG_CHANNEL_ID", "")
        if raw.isdigit():
            channel = self.bot.get_channel(int(raw))
            if channel:
                embed = discord.Embed(title=f"💥 Error {error_id}", color=discord.Color.dark_red())
                embed.add_field(name="Command", value=where, inline=True)
                embed.add_field(name="User", value=f"{user} ({user.id})", inline=True)
                embed.add_field(name="Server", value=guild.name if guild else "DM", inline=True)
                embed.description = f"```py\n{tb[-1800:]}\n```"
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

        return error_id

    @staticmethod
    def unwrap(error):
        """Gets the real cause out of CommandInvokeError / HybridCommandError wrappers."""
        for _ in range(3):
            inner = getattr(error, "original", None)
            if inner is None:
                break
            error = inner
        return error

    # ------------------------------------------------------------------
    # Prefix commands and hybrid commands (text AND slash use)
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Respect commands/cogs that have their own error handler.
        if ctx.command is not None and ctx.command.has_error_handler():
            return
        if ctx.cog is not None and ctx.cog.has_error_handler():
            return

        error = self.unwrap(error)
        result = self.describe(error)

        if result is IGNORE:
            return

        if result is None:
            where = f"{ctx.prefix}{ctx.command.qualified_name}" if ctx.command else "unknown command"
            error_id = await self.report_error(error, where, ctx.author, ctx.guild)
            result = (
                "💥 Something went wrong",
                f"An unexpected error occurred and was logged.\nError ID: `{error_id}`",
                discord.Color.dark_red(),
            )

        try:
            await ctx.send(embed=make_embed(*result), ephemeral=True)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------
    # Pure slash commands (e.g. /backup-create)
    # ------------------------------------------------------------------
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # Hybrid commands are already handled by on_command_error above.
        if type(interaction.command).__name__ == "HybridAppCommand":
            return

        error = self.unwrap(error)
        result = self.describe(error)

        if result is IGNORE:
            return

        if result is None:
            where = f"/{interaction.command.qualified_name}" if interaction.command else "interaction"
            error_id = await self.report_error(error, where, interaction.user, interaction.guild)
            result = (
                "💥 Something went wrong",
                f"An unexpected error occurred and was logged.\nError ID: `{error_id}`",
                discord.Color.dark_red(),
            )

        embed = make_embed(*result)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))
