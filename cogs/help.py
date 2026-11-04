import discord
from discord import app_commands
from discord.ext import commands
from database import get_db_connection

# Cog class name -> (emoji, label shown in the menu). Unknown cogs get a default.
CATEGORY_LABELS = {
    "Moderation": ("🛡️", "Moderation"),
    "Utility": ("🔧", "Utility"),
    "Fun": ("🎉", "Fun"),
    "Giveaway": ("🎁", "Giveaways"),
    "Announcement": ("📢", "Announcements"),
    "AutoReact": ("😀", "Auto Reactions"),
    "Invites": ("📨", "Invites"),
    "Socials": ("📺", "Social Alerts"),
    "Backup": ("💾", "Backups"),
    "Applications": ("📝", "Applications"),
    "Setup": ("⚙️", "Setup"),
}
HIDDEN_COGS = {"Help", "ErrorHandler"}
HOME_VALUE = "__home__"


def collect_commands(cog):
    """
    Returns {command name: description} for one cog, covering:
      - prefix/hybrid commands (including the subcommands of groups like /youtube)
      - slash-only commands (like /backup-create), shown with a leading '/'
    """
    entries = {}

    for command in cog.walk_commands():
        if command.hidden:
            continue
        # A group with subcommands is represented by its subcommands instead.
        if isinstance(command, commands.Group) and command.commands:
            continue
        entries[command.qualified_name] = command.description or command.short_doc or "No description"

    for app_cmd in cog.get_app_commands():
        subs = app_cmd.walk_commands() if isinstance(app_cmd, app_commands.Group) else [app_cmd]
        for sub in subs:
            if isinstance(sub, app_commands.Group):
                continue
            name = sub.qualified_name
            if name not in entries:
                entries[f"/{name}"] = sub.description or "No description"

    return entries


def format_commands(entries):
    """Builds the aligned text block shown inside a category page."""
    width = min(max(len(name) for name in entries) + 2, 22)
    lines = []
    for name, desc in sorted(entries.items(), key=lambda kv: kv[0].lstrip("/")):
        if len(desc) > 55:
            desc = desc[:52] + "..."
        lines.append(f"{name.ljust(width)}{desc}")
    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800].rsplit("\n", 1)[0] + "\n..."
    return f"```text\n{text}\n```"


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

        # Build one page per cog
        categories = []  # (cog_name, emoji, label, entries)
        for cog_name, cog in self.bot.cogs.items():
            if cog_name in HIDDEN_COGS:
                continue
            entries = collect_commands(cog)
            if not entries:
                continue
            emoji, label = CATEGORY_LABELS.get(cog_name, ("📁", cog_name))
            categories.append((cog_name, emoji, label, entries))

        categories.sort(key=lambda c: c[2])

        # Commands defined directly in main.py (not hidden ones like !sync)
        uncogged = {
            cmd.name: cmd.description or cmd.short_doc or "No description"
            for cmd in self.bot.commands
            if cmd.cog is None and not cmd.hidden
        }
        if uncogged:
            categories.append(("__system__", "💻", "System", uncogged))

        if not categories:
            return await ctx.send("No commands available.")

        total = sum(len(entries) for *_, entries in categories)
        home = discord.Embed(
            title="Bot Commands",
            description=(
                f"**Current Prefix:** `{prefix}`\n"
                f"Use `{prefix}command` or `/command`. Commands starting with `/` below are slash-only.\n\n"
                f"Pick a category from the menu to see its commands. **{total}** commands in total."
            ),
            color=discord.Color.green(),
        )
        home.add_field(
            name="Categories",
            value="\n".join(f"{emoji} **{label}**: {len(entries)}" for _, emoji, label, entries in categories),
            inline=False,
        )
        home.set_footer(text=f"Requested by {ctx.author.display_name}")

        pages = {}
        options = [discord.SelectOption(label="Home", value=HOME_VALUE, emoji="🏠")]
        for cog_name, emoji, label, entries in categories:
            page = discord.Embed(title=f"{emoji} {label}", description=format_commands(entries), color=discord.Color.green())
            page.set_footer(text=f"Prefix: {prefix}  •  Requested by {ctx.author.display_name}")
            pages[cog_name] = page
            options.append(discord.SelectOption(label=label, value=cog_name, emoji=emoji, description=f"{len(entries)} commands"))

        view = HelpView(ctx.author.id, home, pages, options)
        view.message = await ctx.send(embed=home, view=view)

async def setup(bot):
    await bot.add_cog(Help(bot))
