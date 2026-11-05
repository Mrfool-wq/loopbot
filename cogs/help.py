from typing import NamedTuple, Optional

import discord
from discord.ext import commands

from database import get_db_connection

# ======================================================================
#  HOW TO EDIT THIS HELP MENU
#  The commands below are written by hand. When you add, rename or remove a
#  command in the bot, change the matching line here (nothing is detected
#  automatically).
#
#  Cmd(name, args, description, permission, kind)
#    name        the command name (no slash), e.g. "ban" or "selfrole add"
#    args        its arguments:  <required>  [optional]   ("" if none)
#    description what it does (keep it short)
#    permission  permission the user needs (None if everyone can use it)
#    kind        "both"   -> works as /name and as <prefix>name  (default)
#                "slash"  -> slash only (/name)
#                "prefix" -> prefix only (<prefix>name)
# ======================================================================


class Cmd(NamedTuple):
    name: str
    args: str
    description: str
    permission: Optional[str] = None
    kind: str = "both"


class Category(NamedTuple):
    emoji: str
    label: str
    note: str
    commands: list


CATEGORIES = [
    Category("🛡️", "Moderation", "", [
        Cmd("ban", "<user> [reason]", "Ban a member. Use their ID to ban someone who already left.", "Ban Members"),
        Cmd("unban", "<user_id> [reason]", "Unban a user using their ID.", "Ban Members"),
        Cmd("mute", "<member> <duration> [reason]", "Timeout a member. Duration like 10m, 2h or 1d (max 28 days).", "Moderate Members"),
        Cmd("unmute", "<member> [reason]", "Remove a member's timeout.", "Moderate Members"),
        Cmd("purge", "<amount>", "Delete the last 1-200 messages in this channel.", "Manage Messages"),
        Cmd("nick", "<member> [new_nickname]", "Change a member's nickname. Leave the nickname empty to reset it.", "Manage Nicknames"),
        Cmd("addrole", "<member> <role>", "Give a role to a member.", "Manage Roles"),
        Cmd("removerole", "<member> <role>", "Take a role away from a member.", "Manage Roles"),
        Cmd("roleedit", "<role> <action> <value>", "Edit a role. Action: mention or hoist (true/false), or color (hex like FF0000).", "Manage Roles"),
        Cmd("setmodlog", "<channel>", "Choose the channel where moderation actions are logged.", "Administrator"),
    ]),
    Category("🔧", "Utility", "", [
        Cmd("help", "", "Show this menu."),
        Cmd("setup", "", "Open the server settings panel: staff role, log channels and prefix.", "Administrator"),
        Cmd("setprefix", "<new_prefix>", "Change the bot's prefix (up to 5 characters).", "Administrator"),
        Cmd("ping", "", "Check the bot's response time."),
        Cmd("serverinfo", "", "Show information about this server."),
        Cmd("roleinfo", "[member]", "See every role a member has."),
        Cmd("afk", "[reason]", "Set yourself as AFK. The bot tells people who mention you, and clears it when you next talk."),
    ]),
    Category("🎉", "Fun", "", [
        Cmd("poll", "<question>", "Create a 👍 / 👎 poll."),
        Cmd("coinflip", "", "Flip a coin."),
        Cmd("topic", "", "Get a random chat topic."),
        Cmd("avatar", "[member]", "Show a member's avatar in full size."),
        Cmd("mimic", "<member> <text>", "Send a message that looks like it came from another member.", "Manage Webhooks"),
    ]),
    Category("🎁", "Giveaways", "", [
        Cmd("gstart", "<duration> <winners> <prize>", "Start a giveaway. Duration like 30s, 10m, 2h or 1d (10 seconds to 30 days), up to 20 winners.", "Manage Messages"),
        Cmd("gend", "<message_id>", "End a giveaway right now and pick the winners.", "Manage Messages"),
        Cmd("greroll", "<channel> <message_id>", "Pick a new winner for a finished giveaway.", "Manage Messages"),
    ]),
    Category("📢", "Announcements", "", [
        Cmd("announce", "<channel> <message> [image]", "Post a message in a channel as the bot, with an optional image.", "Manage Messages"),
        Cmd("autoannounce", "<channel> <interval> <message>", "Repeat a message automatically. Interval like 30m, 1h, 1d or 1w (minimum 5m).", "Manage Messages"),
        Cmd("stopannouncements", "<channel>", "Stop every repeating message in a channel.", "Manage Messages"),
    ]),
    Category("😀", "Auto Reactions", "The bot reacts to whole words. Add * to match word endings: `kill*` also matches killed and killing.", [
        Cmd("setreact", "<trigger> <emoji> [channel]", "React with an emoji when a word is said. Leave the channel empty to work everywhere.", "Manage Channels"),
        Cmd("delreact", "<trigger> [channel]", "Remove an auto-reaction.", "Manage Channels"),
        Cmd("reactlist", "", "List all auto-reactions in this server."),
    ]),
    Category("📨", "Invites", "", [
        Cmd("invites", "[member]", "See how many people you (or another member) invited."),
        Cmd("addinvites", "<member> <amount>", "Add bonus invites to a member. Use a negative number to remove.", "Administrator"),
        Cmd("setinvitelog", "<channel>", "Choose the channel where joins and leaves are logged.", "Administrator"),
    ]),
    Category("📺", "Social Alerts", "In the message, use {author}, {title} and {link}.", [
        Cmd("youtube add", "<channel_id> <discord_channel> [message]", "Post in a channel when a YouTube channel uploads. The ID starts with UC.", "Manage Webhooks"),
        Cmd("youtube remove", "<channel_id>", "Stop alerts for a YouTube channel.", "Manage Webhooks"),
        Cmd("youtube list", "", "Show this server's YouTube alerts."),
    ]),
    Category("🎭", "Self Roles", "Members pick their own roles from a panel. Use style `menu` (dropdown) or `buttons`.", [
        Cmd("selfrole create", "<channel> <title> [description] [image_url] [footer] [color] [style] [exclusive]", "Create a role panel in a channel.", "Manage Roles"),
        Cmd("selfrole add", "<panel> <role> [emoji] [label] [description]", "Add a role to a panel.", "Manage Roles"),
        Cmd("selfrole remove", "<panel> <role>", "Remove a role from a panel.", "Manage Roles"),
        Cmd("selfrole edit", "<panel> [title] [description] [image_url] [footer] [color] [style] [exclusive]", "Change a panel. Type none to clear a text field.", "Manage Roles"),
        Cmd("selfrole list", "", "Show this server's panels.", "Manage Roles"),
        Cmd("selfrole refresh", "<panel>", "Redraw a panel, or re-post it if its message was deleted.", "Manage Roles"),
        Cmd("selfrole delete", "<panel>", "Delete a panel and its message.", "Manage Roles"),
    ]),
    Category("💾", "Backups", "Slash commands only. Loading or deleting a backup needs a second Administrator to confirm.", [
        Cmd("backup-create", "", "Save roles, channels, permissions and emojis (max 5 per server).", "Administrator", "slash"),
        Cmd("backup-list", "", "Show this server's saved backups.", "Administrator", "slash"),
        Cmd("backup-load", "<backup_id>", "Restore a backup. This WIPES the current channels and roles.", "Administrator", "slash"),
        Cmd("backup-delete", "<backup_id>", "Delete a saved backup.", "Administrator", "slash"),
    ]),
    Category("📝", "Applications", "Staff review applications with the buttons in the ticket. Pick the staff role and log channel with /setup.", [
        Cmd("apply", "", "Start a staff application. A form opens, then a private channel is created for you."),
    ]),
]

# Only shown to the bot owner.
OWNER_CATEGORY = Category("👑", "Bot Owner", "Only you can see and use these.", [
    Cmd("noprefix", "<user>", "Let a user run commands without a prefix, or take it away (alias: np).", "Bot Owner", "prefix"),
    Cmd("noprefixlist", "", "List everyone with no-prefix access (aliases: nplist, npl).", "Bot Owner", "prefix"),
    Cmd("sync", "[.]", "Update the slash commands. Add a dot to update only this server.", "Bot Owner", "prefix"),
])

HOME_VALUE = "__home__"


# ======================================================================
#  FORMATTING
# ======================================================================
def format_command(prefix, cmd):
    """
    Two lines per command:
      **/ban** `<user> [reason]`
      Ban a member... *• Needs: Ban Members*
    """
    shown = f"{prefix}{cmd.name}" if cmd.kind == "prefix" else f"/{cmd.name}"
    head = f"**{shown}**" + (f" `{cmd.args}`" if cmd.args else "")
    tail = cmd.description
    if cmd.permission:
        tail += f" *• Needs: {cmd.permission}*"
    return f"{head}\n{tail}"


def build_page(prefix, category, requested_by):
    parts = []
    if category.note:
        parts.append(f"*{category.note}*")
    parts.extend(format_command(prefix, cmd) for cmd in category.commands)

    embed = discord.Embed(
        title=f"{category.emoji} {category.label}",
        description="\n\n".join(parts),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Prefix: {prefix}  •  Requested by {requested_by}")
    return embed


def build_home(prefix, categories, requested_by):
    total = sum(len(c.commands) for c in categories)
    embed = discord.Embed(
        title="📖 Bot Commands",
        description=(
            f"**Prefix:** `{prefix}`\n"
            f"Most commands work as **/command** or **{prefix}command**.\n"
            "`<like this>` is required and `[like this]` is optional.\n\n"
            "Pick a category from the menu below. 👇"
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name=f"Categories ({total} commands)",
        value="\n".join(f"{c.emoji} **{c.label}**: {len(c.commands)}" for c in categories),
        inline=False,
    )
    embed.set_footer(text=f"Requested by {requested_by}")
    return embed


# ======================================================================
#  MENU
# ======================================================================
class HelpSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Choose a category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        value = self.values[0]
        embed = view.home_embed if value == HOME_VALUE else view.pages[value]
        await interaction.response.edit_message(embed=embed, view=view)


class HelpView(discord.ui.View):
    def __init__(self, author_id, home_embed, pages, options):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.home_embed = home_embed
        self.pages = pages
        self.message = None
        self.add_item(HelpSelect(options))

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Run `/help` yourself to use this menu.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_display_prefix(self, ctx):
        """The server's real prefix (ctx.prefix is blank for no-prefix users and '/' for slash use)."""
        if ctx.guild is None:
            return "!"

        prefix = self.bot.prefix_cache.get(ctx.guild.id)
        if prefix:
            return prefix

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (ctx.guild.id,))
            data = await cursor.fetchone()

        prefix = data[0] if data else "!"
        self.bot.prefix_cache[ctx.guild.id] = prefix
        return prefix

    @commands.hybrid_command(name="help", description="Shows the command menu.")
    async def help(self, ctx):
        prefix = await self.get_display_prefix(ctx)
        name = ctx.author.display_name

        categories = list(CATEGORIES)
        if await self.bot.is_owner(ctx.author):
            categories.append(OWNER_CATEGORY)

        home = build_home(prefix, categories, name)

        pages = {}
        options = [discord.SelectOption(label="Home", value=HOME_VALUE, emoji="🏠")]
        for category in categories:
            pages[category.label] = build_page(prefix, category, name)
            options.append(discord.SelectOption(
                label=category.label,
                value=category.label,
                emoji=category.emoji,
                description=f"{len(category.commands)} commands",
            ))

        view = HelpView(ctx.author.id, home, pages, options)
        view.message = await ctx.send(embed=home, view=view)


async def setup(bot):
    await bot.add_cog(Help(bot))
