import re
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from database import get_db_connection

MAX_PANELS_PER_GUILD = 10
MAX_ROLES_PER_PANEL = 25  # Discord's limit for one dropdown / 5 rows of buttons
DEFAULT_COLOR = 0x5865F2

CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w{2,32}:(\d{17,20})>$")
URL_RE = re.compile(r"^https?://\S+$")
CLEAR_WORDS = {"none", "clear", "remove", "off"}

# Roles with any of these permissions can never be handed out by a self-role panel.
DANGEROUS_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
    "manage_messages", "kick_members", "ban_members", "moderate_members",
)

class PanelError(Exception):
    """A panel can't be drawn (for example its channel was deleted)."""


PANEL_COLUMNS = ["id", "guild_id", "channel_id", "message_id", "title", "description",
                 "image_url", "footer", "color", "style", "exclusive"]
OPTION_COLUMNS = ["role_id", "label", "emoji", "description"]


# ----------------------------------------------------------------------
# SMALL HELPERS
# ----------------------------------------------------------------------
def parse_color(text):
    """'FFAA00' or '#FFAA00' -> int. Returns None if invalid."""
    text = text.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return None
    return int(text, 16)


def clean_text(text):
    """Slash commands are single-line, so '\\n' typed by the admin becomes a real new line."""
    return text.replace("\\n", "\n")


def is_clear(value):
    return value is not None and value.strip().lower() in CLEAR_WORDS


def manage_problem(guild, role):
    """Why the bot can't give/remove this role at all (or None if it can)."""
    if role.is_default():
        return "@everyone can't be used"
    if role.managed:
        return "it is managed by a bot or integration"
    if guild.me.top_role <= role:
        return "it is higher than my highest role"
    return None


def safety_problem(role):
    """Why this role must not be self-assignable (or None if it's safe)."""
    bad = [p.replace("_", " ") for p in DANGEROUS_PERMISSIONS if getattr(role.permissions, p)]
    if bad:
        return "it has powerful permissions (" + ", ".join(bad) + ")"
    return None


def row_to_panel(row):
    panel = dict(zip(PANEL_COLUMNS, row))
    panel["exclusive"] = bool(panel["exclusive"])
    return panel


def row_to_option(row):
    return dict(zip(OPTION_COLUMNS, row))


# ----------------------------------------------------------------------
# COMPONENTS (persistent: they keep working after a bot restart)
# ----------------------------------------------------------------------
class RoleButton(discord.ui.Button):
    def __init__(self, cog, panel_id, option):
        super().__init__(
            label=option["label"][:80],
            emoji=option["emoji"] or None,
            style=discord.ButtonStyle.success,
            custom_id=f"selfrole:btn:{panel_id}:{option['role_id']}",
        )
        self.cog = cog
        self.panel_id = panel_id
        self.role_id = option["role_id"]

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_click(interaction, self.panel_id, [self.role_id])


class RoleMenu(discord.ui.Select):
    def __init__(self, cog, panel, options):
        select_options = [
            discord.SelectOption(
                label=o["label"][:100],
                value=str(o["role_id"]),
                description=o["description"] or None,
                emoji=o["emoji"] or None,
            )
            for o in options
        ]
        super().__init__(
            custom_id=f"selfrole:menu:{panel['id']}",
            placeholder="Choose your roles...",
            min_values=1,
            max_values=1 if panel["exclusive"] else len(select_options),
            options=select_options,
        )
        self.cog = cog
        self.panel_id = panel["id"]

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_click(interaction, self.panel_id, [int(v) for v in self.values], view=self.view)


# ----------------------------------------------------------------------
# THE COG
# ----------------------------------------------------------------------
class SelfRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        async with get_db_connection() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS selfrole_panels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    message_id INTEGER,
                    title TEXT,
                    description TEXT,
                    image_url TEXT,
                    footer TEXT,
                    color INTEGER,
                    style TEXT DEFAULT 'menu',
                    exclusive INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS selfrole_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    panel_id INTEGER,
                    role_id INTEGER,
                    label TEXT,
                    emoji TEXT,
                    description TEXT,
                    UNIQUE (panel_id, role_id)
                )
            """)
            await db.commit()

            cursor = await db.execute(f"SELECT {', '.join(PANEL_COLUMNS)} FROM selfrole_panels")
            panels = [row_to_panel(r) for r in await cursor.fetchall()]

        # Re-attach the dropdowns/buttons of every panel so they work after a restart.
        for panel in panels:
            options = await self.get_options(panel["id"])
            if options and panel["message_id"]:
                self.bot.add_view(self.build_view(panel, options), message_id=panel["message_id"])

    # ------------------------------------------------------------------
    # DATABASE HELPERS
    # ------------------------------------------------------------------
    async def get_panel(self, panel_id):
        async with get_db_connection() as db:
            cursor = await db.execute(f"SELECT {', '.join(PANEL_COLUMNS)} FROM selfrole_panels WHERE id = ?", (panel_id,))
            row = await cursor.fetchone()
        return row_to_panel(row) if row else None

    async def get_guild_panel(self, guild_id, panel_id):
        """The panel, but only if it belongs to this server."""
        panel = await self.get_panel(panel_id)
        return panel if panel and panel["guild_id"] == guild_id else None

    async def get_options(self, panel_id):
        async with get_db_connection() as db:
            cursor = await db.execute(
                f"SELECT {', '.join(OPTION_COLUMNS)} FROM selfrole_options WHERE panel_id = ? ORDER BY id",
                (panel_id,),
            )
            return [row_to_option(r) for r in await cursor.fetchall()]

    # ------------------------------------------------------------------
    # BUILDING THE PANEL MESSAGE
    # ------------------------------------------------------------------
    def build_embed(self, guild, panel, options):
        lines = []
        for option in options:
            role = guild.get_role(option["role_id"])
            emoji = f" {option['emoji']}" if option["emoji"] else ""
            lines.append(f"• {role.mention}{emoji}" if role else f"• ⚠️ *deleted role*{emoji}")

        parts = []
        if panel["description"]:
            parts.append(panel["description"])
        if lines:
            parts.append("\n".join(lines))
        else:
            parts.append("*No roles yet. Add some with `/selfrole add`.*")

        embed = discord.Embed(
            title=panel["title"],
            description="\n\n".join(parts),
            color=discord.Color(panel["color"] if panel["color"] is not None else DEFAULT_COLOR),
        )
        if panel["image_url"]:
            embed.set_image(url=panel["image_url"])
        if panel["footer"]:
            embed.set_footer(text=panel["footer"])
        return embed

    def build_view(self, panel, options):
        view = discord.ui.View(timeout=None)
        if panel["style"] == "buttons":
            for option in options:
                view.add_item(RoleButton(self, panel["id"], option))
        else:
            view.add_item(RoleMenu(self, panel, options))
        return view

    async def refresh_panel(self, panel_id):
        """
        Redraws the panel message from the database. If the message was deleted,
        a new one is posted in the same channel. Raises discord.HTTPException on failure.
        """
        panel = await self.get_panel(panel_id)
        guild = self.bot.get_guild(panel["guild_id"])
        channel = guild.get_channel(panel["channel_id"]) if guild else None
        if channel is None:
            raise PanelError("The panel's channel no longer exists.")

        options = await self.get_options(panel_id)
        embed = self.build_embed(guild, panel, options)
        view = self.build_view(panel, options) if options else None

        message = None
        try:
            message = await channel.fetch_message(panel["message_id"])
        except discord.NotFound:
            pass

        if message:
            await message.edit(embed=embed, view=view)
        else:
            message = await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            async with get_db_connection() as db:
                await db.execute("UPDATE selfrole_panels SET message_id = ? WHERE id = ?", (message.id, panel_id))
                await db.commit()
        return message

    # ------------------------------------------------------------------
    # WHEN A MEMBER USES THE PANEL
    # ------------------------------------------------------------------
    async def handle_click(self, interaction, panel_id, clicked_ids, view=None):
        guild = interaction.guild
        panel = await self.get_panel(panel_id)
        if guild is None or panel is None or panel["guild_id"] != guild.id:
            await interaction.response.send_message("❌ This role panel no longer exists.", ephemeral=True)
            return

        options = await self.get_options(panel_id)

        # Acknowledge fast. For dropdowns, re-sending the view also resets the selection.
        if view is not None:
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.defer(ephemeral=True)

        member = interaction.user
        panel_role_ids = {o["role_id"] for o in options}
        held = {r.id for r in member.roles}
        to_add, to_remove, problems = [], [], []

        for role_id in clicked_ids:
            if role_id not in panel_role_ids:
                continue  # an old button/option that was removed from the panel
            role = guild.get_role(role_id)
            if role is None:
                problems.append("A role in this panel no longer exists.")
                continue

            if role_id in held:
                # Removing is always allowed as long as the bot is able to do it.
                reason = manage_problem(guild, role)
                if reason:
                    problems.append(f"{role.mention}: I can't remove it ({reason}).")
                elif role not in to_remove:
                    to_remove.append(role)
            else:
                # Checked again NOW, because the role may have changed since the panel was made.
                reason = manage_problem(guild, role) or safety_problem(role)
                if reason:
                    problems.append(f"{role.mention}: I can't give it ({reason}).")
                    continue
                to_add.append(role)
                if panel["exclusive"]:
                    for other in options:
                        other_role = guild.get_role(other["role_id"])
                        if other_role and other_role.id != role_id and other_role.id in held and other_role not in to_remove:
                            if not manage_problem(guild, other_role):
                                to_remove.append(other_role)

        reason = f"Self-role panel #{panel_id}"
        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason=reason)
            if to_add:
                await member.add_roles(*to_add, reason=reason)
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ I couldn't update your roles. Please tell an admin to check that my role is above the panel's roles.",
                ephemeral=True,
            )
            return

        lines = [f"✅ You now have {role.mention}" for role in to_add]
        lines += [f"🗑️ Removed {role.mention}" for role in to_remove]
        lines += [f"⚠️ {problem}" for problem in problems]
        if not lines:
            lines = ["Nothing changed."]

        await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------------
    # CLEANUP LISTENERS
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        """When a role is deleted, drop it from every panel that offered it."""
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                SELECT DISTINCT o.panel_id FROM selfrole_options o
                JOIN selfrole_panels p ON p.id = o.panel_id
                WHERE o.role_id = ? AND p.guild_id = ?
                """,
                (role.id, role.guild.id),
            )
            panel_ids = [r[0] for r in await cursor.fetchall()]
            if not panel_ids:
                return
            await db.execute(
                "DELETE FROM selfrole_options WHERE role_id = ? AND panel_id IN (%s)" % ",".join("?" * len(panel_ids)),
                (role.id, *panel_ids),
            )
            await db.commit()

        for panel_id in panel_ids:
            try:
                await self.refresh_panel(panel_id)
            except (discord.HTTPException, PanelError):
                pass

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        async with get_db_connection() as db:
            await db.execute(
                "DELETE FROM selfrole_options WHERE panel_id IN (SELECT id FROM selfrole_panels WHERE guild_id = ?)",
                (guild.id,),
            )
            await db.execute("DELETE FROM selfrole_panels WHERE guild_id = ?", (guild.id,))
            await db.commit()

    # ------------------------------------------------------------------
    # COMMANDS
    # ------------------------------------------------------------------
    async def panel_choices(self, interaction: discord.Interaction, current: str):
        """Autocomplete list of this server's panels."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT id, title FROM selfrole_panels WHERE guild_id = ? ORDER BY id", (interaction.guild_id,))
            rows = await cursor.fetchall()
        choices = [
            app_commands.Choice(name=f"#{panel_id} {title}"[:100], value=panel_id)
            for panel_id, title in rows
            if current.lower() in f"#{panel_id} {title}".lower()
        ]
        return choices[:25]

    async def need_panel(self, ctx, panel_id):
        panel = await self.get_guild_panel(ctx.guild.id, panel_id)
        if panel is None:
            await ctx.send("❌ I couldn't find that panel. Use `/selfrole list` to see your panels.", ephemeral=True)
        return panel

    @commands.hybrid_group(name="selfrole", description="Set up self-assignable role panels.", invoke_without_command=True)
    @commands.guild_only()
    async def selfrole(self, ctx):
        await ctx.send(
            "**Self roles:**\n"
            "1. `/selfrole create` makes a panel in a channel\n"
            "2. `/selfrole add` puts a role on it\n"
            "Other commands: `list`, `edit`, `remove`, `refresh`, `delete`."
        )

    @selfrole.command(name="create", description="Create a self-role panel in a channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(
        channel="Where the panel will be posted",
        title="Panel title, e.g. Get your role",
        description="Intro text shown above the role list (use \\n for a new line)",
        image_url="Banner image link (must be a permanent link)",
        footer="Small text at the bottom, e.g. your server name",
        color="Embed color as hex, e.g. FFAA00",
        style="menu = dropdown, buttons = one button per role",
        exclusive="If true, members can hold only ONE role from this panel",
    )
    async def sr_create(self, ctx, channel: discord.TextChannel, title: str, description: str = None,
                        image_url: str = None, footer: str = None, color: str = None,
                        style: Literal["menu", "buttons"] = "menu", exclusive: bool = False):
        await ctx.defer(ephemeral=True)

        if len(title) > 256:
            return await ctx.send("❌ The title can be at most 256 characters.")
        if description and len(clean_text(description)) > 2000:
            return await ctx.send("❌ The description can be at most 2000 characters.")
        if footer and len(footer) > 200:
            return await ctx.send("❌ The footer can be at most 200 characters.")
        if image_url and not (URL_RE.match(image_url) and len(image_url) <= 500):
            return await ctx.send("❌ The image must be a link starting with `http://` or `https://`.")
        color_value = None
        if color:
            color_value = parse_color(color)
            if color_value is None:
                return await ctx.send("❌ Invalid color. Use hex like `FFAA00`.")

        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await ctx.send(f"❌ I need **Send Messages** and **Embed Links** in {channel.mention}.")

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM selfrole_panels WHERE guild_id = ?", (ctx.guild.id,))
            (count,) = await cursor.fetchone()
            if count >= MAX_PANELS_PER_GUILD:
                return await ctx.send(f"❌ This server already has {count} panels (max {MAX_PANELS_PER_GUILD}). Delete one first.")

            cursor = await db.execute(
                """
                INSERT INTO selfrole_panels (guild_id, channel_id, message_id, title, description, image_url, footer, color, style, exclusive)
                VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ctx.guild.id, channel.id, title, clean_text(description) if description else None,
                 image_url, footer, color_value, style, int(exclusive)),
            )
            await db.commit()
            panel_id = cursor.lastrowid

        try:
            await self.refresh_panel(panel_id)
        except (discord.HTTPException, PanelError) as e:
            async with get_db_connection() as db:
                await db.execute("DELETE FROM selfrole_panels WHERE id = ?", (panel_id,))
                await db.commit()
            return await ctx.send(f"❌ I couldn't post the panel: {e}")

        await ctx.send(
            f"✅ Panel **#{panel_id}** created in {channel.mention}.\n"
            f"Now add roles with `/selfrole add panel:{panel_id} role:@YourRole`."
        )

    @selfrole.command(name="add", description="Add a role to a panel.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @app_commands.describe(
        panel="The panel to add the role to",
        role="The role members will be able to pick",
        emoji="Optional emoji shown next to the role",
        label="Optional text for the button/option (defaults to the role name)",
        description="Optional small text under the option (dropdown style only)",
    )
    async def sr_add(self, ctx, panel: int, role: discord.Role, emoji: str = None, label: str = None, description: str = None):
        await ctx.defer(ephemeral=True)
        panel_data = await self.need_panel(ctx, panel)
        if panel_data is None:
            return

        problem = manage_problem(ctx.guild, role) or safety_problem(role)
        if problem:
            return await ctx.send(f"❌ {role.mention} can't be a self-role: {problem}.", allowed_mentions=discord.AllowedMentions.none())
        if ctx.author != ctx.guild.owner and ctx.author.top_role <= role:
            return await ctx.send("❌ You can't add a role that is higher than or equal to your own highest role.")

        if emoji:
            emoji = emoji.strip()
            custom = CUSTOM_EMOJI_RE.match(emoji)
            if custom:
                if self.bot.get_emoji(int(custom.group(1))) is None:
                    return await ctx.send("❌ I can't use that custom emoji. I must be in the server it belongs to.")
            elif emoji.isascii() or len(emoji) > 20 or " " in emoji:
                return await ctx.send("❌ That doesn't look like an emoji. Use a normal emoji (🎉) or a custom one from a server I'm in.")

        label_text = (label or role.name).strip()
        if not label_text or len(label_text) > 80:
            return await ctx.send("❌ The label must be between 1 and 80 characters.")
        if description and len(description) > 100:
            return await ctx.send("❌ The description can be at most 100 characters.")

        options = await self.get_options(panel)
        if any(o["role_id"] == role.id for o in options):
            return await ctx.send("⚠️ That role is already on this panel.")
        if len(options) >= MAX_ROLES_PER_PANEL:
            return await ctx.send(f"❌ A panel can hold at most {MAX_ROLES_PER_PANEL} roles.")

        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO selfrole_options (panel_id, role_id, label, emoji, description) VALUES (?, ?, ?, ?, ?)",
                (panel, role.id, label_text, emoji, description),
            )
            await db.commit()

        try:
            await self.refresh_panel(panel)
        except (discord.HTTPException, PanelError) as e:
            async with get_db_connection() as db:
                await db.execute("DELETE FROM selfrole_options WHERE panel_id = ? AND role_id = ?", (panel, role.id))
                await db.commit()
            return await ctx.send(f"❌ Discord rejected the update, so nothing was changed: {e}")

        await ctx.send(f"✅ Added {role.mention} to panel **#{panel}**.", allowed_mentions=discord.AllowedMentions.none())

    @selfrole.command(name="remove", description="Remove a role from a panel.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(panel="The panel to remove the role from", role="The role to remove")
    async def sr_remove(self, ctx, panel: int, role: discord.Role):
        await ctx.defer(ephemeral=True)
        if await self.need_panel(ctx, panel) is None:
            return

        async with get_db_connection() as db:
            cursor = await db.execute("DELETE FROM selfrole_options WHERE panel_id = ? AND role_id = ?", (panel, role.id))
            await db.commit()
            removed = cursor.rowcount

        if removed == 0:
            return await ctx.send("⚠️ That role isn't on this panel.")

        try:
            await self.refresh_panel(panel)
        except (discord.HTTPException, PanelError) as e:
            return await ctx.send(f"⚠️ Removed from the database, but I couldn't update the message: {e}")
        await ctx.send(f"🗑️ Removed {role.mention} from panel **#{panel}**.", allowed_mentions=discord.AllowedMentions.none())

    @selfrole.command(name="edit", description="Change a panel's look or behaviour. Use 'none' to clear a text field.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(
        panel="The panel to edit",
        title="New title",
        description="New intro text (use \\n for a new line, or 'none')",
        image_url="New banner image link (or 'none')",
        footer="New footer text (or 'none')",
        color="New hex color, e.g. FFAA00",
        style="menu = dropdown, buttons = one button per role",
        exclusive="If true, members can hold only ONE role from this panel",
    )
    async def sr_edit(self, ctx, panel: int, title: str = None, description: str = None, image_url: str = None,
                      footer: str = None, color: str = None, style: Literal["menu", "buttons"] = None,
                      exclusive: bool = None):
        await ctx.defer(ephemeral=True)
        if await self.need_panel(ctx, panel) is None:
            return

        updates = {}
        if title is not None:
            if not title.strip() or len(title) > 256:
                return await ctx.send("❌ The title must be between 1 and 256 characters.")
            updates["title"] = title
        if description is not None:
            if is_clear(description):
                updates["description"] = None
            elif len(clean_text(description)) > 2000:
                return await ctx.send("❌ The description can be at most 2000 characters.")
            else:
                updates["description"] = clean_text(description)
        if image_url is not None:
            if is_clear(image_url):
                updates["image_url"] = None
            elif URL_RE.match(image_url) and len(image_url) <= 500:
                updates["image_url"] = image_url
            else:
                return await ctx.send("❌ The image must be a link starting with `http://` or `https://`.")
        if footer is not None:
            if is_clear(footer):
                updates["footer"] = None
            elif len(footer) > 200:
                return await ctx.send("❌ The footer can be at most 200 characters.")
            else:
                updates["footer"] = footer
        if color is not None:
            value = parse_color(color)
            if value is None:
                return await ctx.send("❌ Invalid color. Use hex like `FFAA00`.")
            updates["color"] = value
        if style is not None:
            updates["style"] = style
        if exclusive is not None:
            updates["exclusive"] = int(exclusive)

        if not updates:
            return await ctx.send("❌ Tell me what to change (for example `title:` or `style:`).")

        columns = {"title", "description", "image_url", "footer", "color", "style", "exclusive"}
        assert set(updates) <= columns  # only known column names ever reach the SQL
        assignments = ", ".join(f"{name} = ?" for name in updates)
        async with get_db_connection() as db:
            await db.execute(f"UPDATE selfrole_panels SET {assignments} WHERE id = ?", (*updates.values(), panel))
            await db.commit()

        try:
            await self.refresh_panel(panel)
        except (discord.HTTPException, PanelError) as e:
            return await ctx.send(f"⚠️ Saved, but I couldn't update the message: {e}")
        await ctx.send(f"✅ Panel **#{panel}** updated.")

    @selfrole.command(name="list", description="Show this server's self-role panels.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def sr_list(self, ctx):
        await ctx.defer(ephemeral=True)
        async with get_db_connection() as db:
            cursor = await db.execute(
                f"""
                SELECT {', '.join('p.' + c for c in PANEL_COLUMNS)}, COUNT(o.id)
                FROM selfrole_panels p LEFT JOIN selfrole_options o ON o.panel_id = p.id
                WHERE p.guild_id = ? GROUP BY p.id ORDER BY p.id
                """,
                (ctx.guild.id,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return await ctx.send("📭 No panels yet. Create one with `/selfrole create`.")

        embed = discord.Embed(title="🎭 Self-role panels", color=discord.Color.blurple())
        for row in rows:
            panel = row_to_panel(row[:-1])
            link = f"https://discord.com/channels/{ctx.guild.id}/{panel['channel_id']}/{panel['message_id']}"
            mode = "one role only" if panel["exclusive"] else "multiple roles"
            embed.add_field(
                name=f"#{panel['id']} {panel['title']}"[:256],
                value=f"<#{panel['channel_id']}> • [Jump to panel]({link})\n{panel['style']} • {mode} • {row[-1]} role(s)",
                inline=False,
            )
        await ctx.send(embed=embed)

    @selfrole.command(name="refresh", description="Redraw a panel (re-posts it if the message was deleted).")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(panel="The panel to redraw")
    async def sr_refresh(self, ctx, panel: int):
        await ctx.defer(ephemeral=True)
        if await self.need_panel(ctx, panel) is None:
            return
        try:
            message = await self.refresh_panel(panel)
        except (discord.HTTPException, PanelError) as e:
            return await ctx.send(f"❌ Couldn't refresh the panel: {e}")
        await ctx.send(f"✅ Panel **#{panel}** refreshed: {message.jump_url}")

    @selfrole.command(name="delete", description="Delete a panel and its message.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(panel="The panel to delete")
    async def sr_delete(self, ctx, panel: int):
        await ctx.defer(ephemeral=True)
        panel_data = await self.need_panel(ctx, panel)
        if panel_data is None:
            return

        channel = ctx.guild.get_channel(panel_data["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(panel_data["message_id"])
                await message.delete()
            except discord.HTTPException:
                pass  # already gone, or I can't delete it: the panel is removed either way

        async with get_db_connection() as db:
            await db.execute("DELETE FROM selfrole_options WHERE panel_id = ?", (panel,))
            await db.execute("DELETE FROM selfrole_panels WHERE id = ?", (panel,))
            await db.commit()
        await ctx.send(f"🗑️ Panel **#{panel}** deleted.")

    # Panel autocomplete for every command that takes a panel
    @sr_add.autocomplete("panel")
    async def _ac_add(self, interaction: discord.Interaction, current: str):
        return await self.panel_choices(interaction, current)

    @sr_remove.autocomplete("panel")
    async def _ac_remove(self, interaction: discord.Interaction, current: str):
        return await self.panel_choices(interaction, current)

    @sr_edit.autocomplete("panel")
    async def _ac_edit(self, interaction: discord.Interaction, current: str):
        return await self.panel_choices(interaction, current)

    @sr_refresh.autocomplete("panel")
    async def _ac_refresh(self, interaction: discord.Interaction, current: str):
        return await self.panel_choices(interaction, current)

    @sr_delete.autocomplete("panel")
    async def _ac_delete(self, interaction: discord.Interaction, current: str):
        return await self.panel_choices(interaction, current)


async def setup(bot):
    await bot.add_cog(SelfRoles(bot))
