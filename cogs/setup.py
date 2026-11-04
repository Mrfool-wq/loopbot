import discord
from discord.ext import commands

from database import get_db_connection
from guild_settings import LEGACY_STAFF_ROLE_NAME, ensure_table, get_settings, set_setting

DEFAULT_PREFIX = "!"
MAX_PREFIX_LENGTH = 5
VIEW_TIMEOUT = 300  # seconds of inactivity before the panel stops working

# key -> what the setting is and which channel permissions the bot needs there
CHANNEL_SETTINGS = {
    "application_log": {
        "label": "Application Logs",
        "emoji": "📝",
        "prompt": "Pick the channel where **application transcripts** will be posted.",
        "needs": ("send_messages", "attach_files"),
    },
    "mod_log": {
        "label": "Mod Logs",
        "emoji": "🛡️",
        "prompt": "Pick the channel where **moderation actions** (bans, mutes...) will be logged.",
        "needs": ("send_messages", "embed_links"),
    },
    "invite_log": {
        "label": "Invite Logs",
        "emoji": "📨",
        "prompt": "Pick the channel where **joins and leaves** (with who invited them) will be logged.",
        "needs": ("send_messages", "embed_links"),
    },
}


def perm_names(perms):
    return ", ".join(p.replace("_", " ").title() for p in perms)


def describe_channel(guild, channel_id, needs):
    """Text shown for a channel setting, with a warning if something is wrong with it."""
    if not channel_id:
        return "❌ Not set"
    channel = guild.get_channel(channel_id)
    if channel is None:
        return "⚠️ That channel was deleted. Pick a new one."
    perms = channel.permissions_for(guild.me)
    missing = [p for p in needs if not getattr(perms, p)]
    if missing:
        return f"{channel.mention}\n⚠️ I'm missing: **{perm_names(missing)}**"
    return channel.mention


def describe_role(guild, role_id):
    if role_id:
        role = guild.get_role(role_id)
        if role:
            return role.mention
        return "⚠️ That role was deleted. Pick a new one."
    legacy = discord.utils.get(guild.roles, name=LEGACY_STAFF_ROLE_NAME)
    if legacy:
        return f"{legacy.mention} *(found by name, pick it here to make it official)*"
    return "❌ Not set (only Administrators can review applications)"


# ----------------------------------------------------------------------
# VIEWS
# ----------------------------------------------------------------------
class OwnedView(discord.ui.View):
    """Base for the panel: only the administrator who ran /setup can use it."""

    def __init__(self, cog, author_id):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.author_id = author_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the person who ran `/setup` can use this panel.", ephemeral=True)
            return False
        if not interaction.permissions.administrator:
            await interaction.response.send_message("❌ You need the Administrator permission to change settings.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class MainView(OwnedView):
    @discord.ui.button(label="Staff Role", emoji="👥", style=discord.ButtonStyle.primary, row=0)
    async def staff_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="👥 Staff Role",
            description="Pick the role that can **accept, reject and close** applications.\nIt is also pinged when a new application arrives.",
            color=discord.Color.blurple(),
        )
        await self.cog.show_picker(interaction, self.author_id, "staff_role", embed)

    @discord.ui.button(label="Application Logs", emoji="📝", style=discord.ButtonStyle.secondary, row=0)
    async def application_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_channel_picker(interaction, self.author_id, "application_log")

    @discord.ui.button(label="Mod Logs", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def mod_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_channel_picker(interaction, self.author_id, "mod_log")

    @discord.ui.button(label="Invite Logs", emoji="📨", style=discord.ButtonStyle.secondary, row=0)
    async def invite_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_channel_picker(interaction, self.author_id, "invite_log")

    @discord.ui.button(label="Prefix", emoji="❗", style=discord.ButtonStyle.secondary, row=0)
    async def prefix(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = (await self.cog.read_all(interaction.guild))["prefix"]
        await interaction.response.send_modal(PrefixModal(self.cog, self.author_id, current))


class RolePicker(discord.ui.RoleSelect):
    def __init__(self, panel):
        super().__init__(placeholder="Select the staff role...", min_values=1, max_values=1, row=0)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ Pick a normal role (not @everyone and not a bot's role).", ephemeral=True)
            return
        await self.panel.cog.save(interaction.guild, "staff_role", role.id)
        await self.panel.cog.show_dashboard(interaction, self.panel.author_id)


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, panel, key):
        super().__init__(
            placeholder=f"Select the {CHANNEL_SETTINGS[key]['label'].lower()} channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=0,
        )
        self.panel = panel
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel = guild.get_channel(self.values[0].id)
        if channel is None:
            await interaction.response.send_message("❌ I can't see that channel.", ephemeral=True)
            return

        perms = channel.permissions_for(guild.me)
        missing = [p for p in CHANNEL_SETTINGS[self.key]["needs"] if not getattr(perms, p)]
        if missing:
            await interaction.response.send_message(
                f"❌ I need **{perm_names(missing)}** in {channel.mention} first. Fix that and pick it again.",
                ephemeral=True,
            )
            return

        await self.panel.cog.save(guild, self.key, channel.id)
        await self.panel.cog.show_dashboard(interaction, self.panel.author_id)


class PickerView(OwnedView):
    """Shows a role/channel dropdown plus Clear and Back buttons."""

    def __init__(self, cog, author_id, key):
        super().__init__(cog, author_id)
        self.key = key
        self.add_item(RolePicker(self) if key == "staff_role" else ChannelPicker(self, key))

    @discord.ui.button(label="Clear this setting", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.save(interaction.guild, self.key, None)
        await self.cog.show_dashboard(interaction, self.author_id)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_dashboard(interaction, self.author_id)


class PrefixModal(discord.ui.Modal, title="Change Prefix"):
    def __init__(self, cog, author_id, current):
        super().__init__()
        self.cog = cog
        self.author_id = author_id
        self.prefix_input = discord.ui.TextInput(
            label="New prefix (leave empty to reset)",
            default=current,
            max_length=MAX_PREFIX_LENGTH,
            required=False,
        )
        self.add_item(self.prefix_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id or not interaction.permissions.administrator:
            await interaction.response.send_message("❌ You can't change settings here.", ephemeral=True)
            return

        value = self.prefix_input.value or ""
        if value and not value.strip():
            await interaction.response.send_message("❌ The prefix can't be only spaces.", ephemeral=True)
            return

        await self.cog.save(interaction.guild, "prefix", value or None)  # empty = back to the default
        await self.cog.show_dashboard(interaction, self.author_id)


# ----------------------------------------------------------------------
# THE COG
# ----------------------------------------------------------------------
class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_table()
        # Same table the Invites cog creates, so /setup works even if that cog failed to load.
        async with get_db_connection() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_settings (
                    guild_id INTEGER PRIMARY KEY,
                    log_channel_id INTEGER
                )
            """)
            await db.commit()

    async def read_all(self, guild):
        """Reads every setting from wherever it is stored."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT channel_id FROM mod_logs WHERE guild_id = ?", (guild.id,))
            mod_row = await cursor.fetchone()
            cursor = await db.execute("SELECT log_channel_id FROM invite_settings WHERE guild_id = ?", (guild.id,))
            invite_row = await cursor.fetchone()
            cursor = await db.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (guild.id,))
            prefix_row = await cursor.fetchone()

        settings = await get_settings(guild.id)
        return {
            "prefix": prefix_row[0] if prefix_row else DEFAULT_PREFIX,
            "staff_role": settings["staff_role_id"],
            "application_log": settings["application_log_channel_id"],
            "mod_log": mod_row[0] if mod_row else None,
            "invite_log": invite_row[0] if invite_row else None,
        }

    async def save(self, guild, key, value):
        """Saves one setting. value=None clears it."""
        gid = guild.id

        if key == "staff_role":
            await set_setting(gid, "staff_role_id", value)
            return
        if key == "application_log":
            await set_setting(gid, "application_log_channel_id", value)
            return

        async with get_db_connection() as db:
            if key == "mod_log":
                if value is None:
                    await db.execute("DELETE FROM mod_logs WHERE guild_id = ?", (gid,))
                else:
                    await db.execute(
                        "INSERT INTO mod_logs (guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id = ?",
                        (gid, value, value),
                    )
            elif key == "invite_log":
                if value is None:
                    await db.execute("DELETE FROM invite_settings WHERE guild_id = ?", (gid,))
                else:
                    await db.execute(
                        "INSERT OR REPLACE INTO invite_settings (guild_id, log_channel_id) VALUES (?, ?)",
                        (gid, value),
                    )
            elif key == "prefix":
                if value is None:
                    await db.execute("DELETE FROM prefixes WHERE guild_id = ?", (gid,))
                else:
                    await db.execute("INSERT OR REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)", (gid, value))
            else:
                raise ValueError(f"Unknown setting: {key}")
            await db.commit()

        if key == "prefix":
            # Keep the bot's in-memory prefix cache in step with the database.
            if value is None:
                self.bot.prefix_cache.pop(gid, None)
            else:
                self.bot.prefix_cache[gid] = value

    async def build_embed(self, guild):
        data = await self.read_all(guild)
        embed = discord.Embed(
            title=f"⚙️ Setup: {guild.name}",
            description="Use the buttons below to change a setting. Changes apply immediately.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="❗ Prefix", value=f"`{data['prefix']}`", inline=False)
        embed.add_field(name="👥 Staff Role", value=describe_role(guild, data["staff_role"]), inline=False)
        for key, info in CHANNEL_SETTINGS.items():
            embed.add_field(
                name=f"{info['emoji']} {info['label']}",
                value=describe_channel(guild, data[key], info["needs"]),
                inline=False,
            )
        embed.set_footer(text="Only administrators can use this panel.")
        return embed

    async def show_dashboard(self, interaction, author_id):
        """Redraws the main panel by editing the message the interaction came from."""
        embed = await self.build_embed(interaction.guild)
        view = MainView(self, author_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    async def show_picker(self, interaction, author_id, key, embed):
        view = PickerView(self, author_id, key)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    async def show_channel_picker(self, interaction, author_id, key):
        info = CHANNEL_SETTINGS[key]
        embed = discord.Embed(
            title=f"{info['emoji']} {info['label']}",
            description=f"{info['prompt']}\n\nI need **{perm_names(info['needs'])}** in that channel.",
            color=discord.Color.blurple(),
        )
        await self.show_picker(interaction, author_id, key, embed)

    @commands.hybrid_command(name="setup", description="Configure the bot for this server (staff role, log channels, prefix).")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setup_command(self, ctx):
        embed = await self.build_embed(ctx.guild)
        view = MainView(self, ctx.author.id)
        view.message = await ctx.send(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Setup(bot))
