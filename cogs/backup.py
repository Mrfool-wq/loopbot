import asyncio
import base64
import json
import uuid
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from database import get_db_connection

MAX_BACKUPS_PER_GUILD = 5

# A backup is visible to a server if it was made in that server.
# Old backups (made before servers were tracked) have guild_id NULL and
# stay visible only to the admin who created them.
ACCESS_SQL = "(guild_id = ? OR (guild_id IS NULL AND user_id = ?))"


# ------------------------------------------------------------------
# UI VIEW: TWO-ADMIN CONFIRMATION
# ------------------------------------------------------------------
class AdminConfirmView(discord.ui.View):
    def __init__(self, initiator: discord.Member, timeout=120):
        super().__init__(timeout=timeout)
        self.initiator = initiator
        self.confirmed = False
        self.cancelled = False
        self.confirmed_by = None

    @discord.ui.button(label="Confirm Action", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.permissions.administrator:
            return await interaction.response.send_message("❌ You must be an Administrator to confirm.", ephemeral=True)

        if interaction.user.id == self.initiator.id:
            return await interaction.response.send_message("❌ A *second* Administrator must confirm this action.", ephemeral=True)

        self.confirmed = True
        self.confirmed_by = interaction.user

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content=f"✅ Action confirmed by {self.confirmed_by.mention}.", view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.initiator.id and not interaction.permissions.administrator:
            return await interaction.response.send_message("❌ You do not have permission to cancel this.", ephemeral=True)

        self.cancelled = True
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content="🚫 Action cancelled.", view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class Backup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._busy = set()  # guild IDs currently loading a backup

    async def cog_load(self):
        """Adds the guild_id column to the backups table if it's missing."""
        async with get_db_connection() as db:
            cursor = await db.execute("PRAGMA table_info(backups)")
            columns = [row[1] for row in await cursor.fetchall()]
            if "guild_id" not in columns:
                print("[Database] Migrating backups table: adding 'guild_id' column...")
                await db.execute("ALTER TABLE backups ADD COLUMN guild_id INTEGER")
                await db.commit()

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------
    @staticmethod
    async def get_image_base64(session, url):
        """Downloads an image and converts it to base64 for storage."""
        if not url:
            return None
        try:
            async with session.get(str(url)) as resp:
                if resp.status == 200:
                    return base64.b64encode(await resp.read()).decode("utf-8")
        except Exception:
            return None
        return None

    @staticmethod
    def serialize_overwrites(channel):
        result = []
        for target, overwrite in channel.overwrites.items():
            allow, deny = overwrite.pair()
            entry = {"allow": allow.value, "deny": deny.value}
            if isinstance(target, discord.Role):
                if target.is_default():
                    entry["type"] = "everyone"
                else:
                    entry["type"] = "role"
                    entry["name"] = target.name
            elif isinstance(target, discord.Member):
                entry["type"] = "member"
                entry["id"] = target.id
            else:
                continue
            result.append(entry)
        return result

    def serialize_channel(self, channel):
        return {
            "type": str(channel.type),
            "name": channel.name,
            "position": channel.position,
            "topic": getattr(channel, "topic", None),
            "nsfw": getattr(channel, "nsfw", False),
            "slowmode_delay": getattr(channel, "slowmode_delay", 0),
            "bitrate": getattr(channel, "bitrate", None),
            "user_limit": getattr(channel, "user_limit", None),
            "overwrites": self.serialize_overwrites(channel),
        }

    @staticmethod
    def build_overwrites(guild, entries, role_map):
        """Turns stored overwrite entries back into a {target: PermissionOverwrite} dict."""
        overwrites = {}
        for entry in entries or []:
            overwrite = discord.PermissionOverwrite.from_pair(
                discord.Permissions(entry["allow"]), discord.Permissions(entry["deny"])
            )
            kind = entry.get("type")
            if kind == "everyone":
                overwrites[guild.default_role] = overwrite
            elif kind == "role":
                role = role_map.get(entry.get("name"))
                if role:
                    overwrites[role] = overwrite
            elif kind == "member":
                member = guild.get_member(entry.get("id"))
                if member:
                    overwrites[member] = overwrite
        return overwrites

    async def create_channel(self, guild, cdata, category, overwrites):
        """Creates one channel from stored data. Returns None for unsupported types."""
        kwargs = {"name": cdata["name"], "category": category}
        if overwrites:
            kwargs["overwrites"] = overwrites

        ctype = cdata.get("type", "text")
        if ctype in ("text", "news"):
            return await guild.create_text_channel(
                topic=cdata.get("topic"),
                nsfw=bool(cdata.get("nsfw")),
                slowmode_delay=cdata.get("slowmode_delay") or 0,
                **kwargs,
            )
        if ctype in ("voice", "stage_voice"):
            bitrate = cdata.get("bitrate") or 64000
            return await guild.create_voice_channel(
                bitrate=min(int(bitrate), int(guild.bitrate_limit)),
                user_limit=cdata.get("user_limit") or 0,
                **kwargs,
            )
        return None

    @staticmethod
    async def attempt(failures, label, coro):
        """Runs a coroutine; on failure records it instead of silently ignoring it."""
        try:
            return await coro
        except Exception as e:
            failures.append(f"{label} ({type(e).__name__})")
            return None

    # ------------------------------------------------------------------
    # 1. CREATE BACKUP
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-create", description="Create a full backup (Roles, Channels, Emojis)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_create(self, interaction: discord.Interaction):
        guild = interaction.guild

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM backups WHERE guild_id = ?", (guild.id,))
            (count,) = await cursor.fetchone()
        if count >= MAX_BACKUPS_PER_GUILD:
            return await interaction.response.send_message(
                f"❌ This server already has {count} backups (max {MAX_BACKUPS_PER_GUILD}). "
                "Delete one with `/backup-delete` first.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        backup_data = {
            "version": 2,
            "name": guild.name,
            "date": str(datetime.now()),
            "everyone_permissions": guild.default_role.permissions.value,
            "roles": [],
            "categories": [],
            "standalone_channels": [],
            "emojis": [],
        }

        # A. Roles (highest first, so they're recreated in the right order).
        # Skips @everyone and managed (bot/integration) roles.
        for role in reversed(guild.roles):
            if not role.is_default() and not role.managed:
                backup_data["roles"].append({
                    "name": role.name,
                    "color": role.color.value,
                    "permissions": role.permissions.value,
                    "hoist": role.hoist,
                    "mentionable": role.mentionable,
                })

        # B. Categories & their channels
        for category in guild.categories:
            backup_data["categories"].append({
                "name": category.name,
                "overwrites": self.serialize_overwrites(category),
                "channels": [self.serialize_channel(c) for c in category.channels],
            })

        # C. Channels that are not in any category
        for channel in sorted(guild.channels, key=lambda c: c.position):
            if channel.category is None and not isinstance(channel, discord.CategoryChannel):
                backup_data["standalone_channels"].append(self.serialize_channel(channel))

        # D. Emojis (heavy operation: one shared HTTP session)
        async with aiohttp.ClientSession() as session:
            for emoji in guild.emojis:
                img_data = await self.get_image_base64(session, emoji.url)
                if img_data:
                    backup_data["emojis"].append({"name": emoji.name, "image": img_data})

        backup_id = str(uuid.uuid4())[:8]
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO backups (backup_id, user_id, data, guild_id) VALUES (?, ?, ?, ?)",
                (backup_id, interaction.user.id, json.dumps(backup_data), guild.id),
            )
            await db.commit()

        await interaction.followup.send(
            f"✅ **Backup Created!**\n🆔 ID: `{backup_id}`\n"
            f"💾 Saved: {len(backup_data['roles'])} roles, {len(guild.channels)} channels, "
            f"{len(backup_data['emojis'])} emojis (with channel permissions).",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # 2. LOAD BACKUP (Requires 2 Admins)
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-load", description="⚠️ Load a backup. WIPES CURRENT SERVER.")
    @app_commands.describe(backup_id="The ID of the backup to load")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_load(self, interaction: discord.Interaction, backup_id: str):
        guild = interaction.guild

        if guild.id in self._busy:
            return await interaction.response.send_message("⏳ A backup is already being loaded in this server.", ephemeral=True)

        perms = guild.me.guild_permissions
        if not (perms.administrator or (perms.manage_channels and perms.manage_roles)):
            return await interaction.response.send_message(
                "❌ I need the **Manage Channels** and **Manage Roles** permissions (or Administrator) to load a backup.",
                ephemeral=True,
            )

        async with get_db_connection() as db:
            cursor = await db.execute(
                f"SELECT data FROM backups WHERE backup_id = ? AND {ACCESS_SQL}",
                (backup_id, guild.id, interaction.user.id),
            )
            row = await cursor.fetchone()

        if not row:
            return await interaction.response.send_message("❌ Backup ID not found for this server.", ephemeral=True)

        view = AdminConfirmView(initiator=interaction.user)
        await interaction.response.send_message(
            f"⚠️ **WARNING:** {interaction.user.mention} has requested to **LOAD BACKUP `{backup_id}`**.\n"
            "This will **WIPE THE CURRENT SERVER** (delete all channels and roles).\n"
            "Member role assignments and message history are **not** restored.\n\n"
            "🛡️ **A second Administrator must click confirm to proceed.**",
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        await view.wait()

        if not view.confirmed:
            if not view.cancelled:  # timed out: update the message so the buttons don't look live
                try:
                    await interaction.edit_original_response(content="⌛ Confirmation timed out. No changes were made.", view=view)
                except discord.HTTPException:
                    pass
            return

        if guild.id in self._busy:
            return
        self._busy.add(guild.id)

        try:
            await self.run_restore(interaction, guild, json.loads(row[0]), view)
        finally:
            self._busy.discard(guild.id)

    async def run_restore(self, interaction, guild, data, view):
        failures = []
        skipped = 0

        try:
            await interaction.followup.send("⚠️ **LOADING BACKUP...**\nDeleting existing channels/roles... This will take time.")
        except discord.HTTPException:
            pass

        # A. WIPE
        for channel in list(guild.channels):
            await self.attempt(failures, f"delete channel {channel.name}", channel.delete())
            await asyncio.sleep(0.2)

        for role in list(guild.roles):
            if not role.is_default() and not role.managed:
                await self.attempt(failures, f"delete role {role.name}", role.delete())
                await asyncio.sleep(0.2)

        # B. @everyone permissions
        if "everyone_permissions" in data:
            await self.attempt(
                failures, "@everyone permissions",
                guild.default_role.edit(permissions=discord.Permissions(data["everyone_permissions"])),
            )

        # C. ROLES (stored highest first; each new role lands at the bottom)
        role_map = {}
        for role_data in data.get("roles", []):
            role = await self.attempt(failures, f"role {role_data['name']}", guild.create_role(
                name=role_data["name"],
                color=discord.Color(role_data["color"]),
                permissions=discord.Permissions(role_data["permissions"]),
                hoist=role_data["hoist"],
                mentionable=role_data["mentionable"],
            ))
            if role:
                role_map[role_data["name"]] = role
            await asyncio.sleep(0.5)

        # D. CATEGORIES & CHANNELS
        for cat_data in data.get("categories", []):
            cat_ow = self.build_overwrites(guild, cat_data.get("overwrites"), role_map)
            cat_kwargs = {"overwrites": cat_ow} if cat_ow else {}
            category = await self.attempt(
                failures, f"category {cat_data['name']}",
                guild.create_category(name=cat_data["name"], **cat_kwargs),
            )
            await asyncio.sleep(0.5)

            for chan_data in cat_data.get("channels", []):
                ow = self.build_overwrites(guild, chan_data.get("overwrites"), role_map)
                result = await self.attempt(
                    failures, f"channel {chan_data['name']}",
                    self.create_channel(guild, chan_data, category, ow),
                )
                if result is None and chan_data.get("type") not in ("text", "news", "voice", "stage_voice"):
                    skipped += 1
                await asyncio.sleep(0.5)

        # E. CHANNELS WITHOUT A CATEGORY (old backups stored these as two plain lists)
        standalone = data.get("standalone_channels")
        if standalone is None:
            standalone = (
                [{"type": "text", "name": c["name"], "topic": c.get("topic")} for c in data.get("text_channels", [])]
                + [{"type": "voice", "name": c["name"]} for c in data.get("voice_channels", [])]
            )
        for chan_data in standalone:
            ow = self.build_overwrites(guild, chan_data.get("overwrites"), role_map)
            result = await self.attempt(
                failures, f"channel {chan_data['name']}",
                self.create_channel(guild, chan_data, None, ow),
            )
            if result is None and chan_data.get("type") not in ("text", "news", "voice", "stage_voice"):
                skipped += 1
            await asyncio.sleep(0.5)

        # F. EMOJIS
        for emoji_data in data.get("emojis", []):
            if len(guild.emojis) >= guild.emoji_limit:
                failures.append("emojis (server emoji limit reached)")
                break
            img_bytes = base64.b64decode(emoji_data["image"])
            await self.attempt(
                failures, f"emoji {emoji_data['name']}",
                guild.create_custom_emoji(name=emoji_data["name"], image=img_bytes),
            )
            await asyncio.sleep(1.5)

        # G. REPORT (posted in a channel: the interaction token may have expired by now)
        summary = f"{interaction.user.mention} & {view.confirmed_by.mention} ✅ **Backup '{data.get('name', 'Unknown')}' loaded.**"
        if skipped:
            summary += f"\nℹ️ {skipped} unsupported channel(s) (e.g. forums) were skipped."
        if failures:
            shown = "\n".join(f"• {f}" for f in failures[:10])
            more = f"\n…and {len(failures) - 10} more." if len(failures) > 10 else ""
            summary += f"\n⚠️ **{len(failures)} item(s) failed:**\n{shown}{more}"

        report_channel = await self.attempt(failures, "report channel", guild.create_text_channel("backup-complete"))
        if report_channel:
            await self.attempt(failures, "report message", report_channel.send(summary))

    # ------------------------------------------------------------------
    # 3. LIST BACKUPS
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-list", description="List this server's saved backups.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with get_db_connection() as db:
            cursor = await db.execute(
                f"""
                SELECT backup_id, created_at, user_id, guild_id, json_extract(data, '$.name')
                FROM backups WHERE {ACCESS_SQL}
                ORDER BY created_at DESC LIMIT 10
                """,
                (interaction.guild.id, interaction.user.id),
            )
            rows = await cursor.fetchall()

        if not rows:
            return await interaction.followup.send("📂 No backups found for this server.")

        embed = discord.Embed(title="📂 Server Backups", color=discord.Color.blue())
        for b_id, b_date, creator_id, b_guild_id, server_name in rows:
            legacy = " *(legacy)*" if b_guild_id is None else ""
            embed.add_field(
                name=f"🆔 `{b_id}`{legacy}",
                value=f"**Server:** {server_name or 'Unknown Server'}\n**Date:** {b_date}\n**By:** <@{creator_id}>",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # 4. DELETE BACKUP (Requires 2 Admins)
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-delete", description="Delete a specific backup.")
    @app_commands.describe(backup_id="The ID of the backup to delete")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_delete(self, interaction: discord.Interaction, backup_id: str):
        async with get_db_connection() as db:
            cursor = await db.execute(
                f"SELECT 1 FROM backups WHERE backup_id = ? AND {ACCESS_SQL}",
                (backup_id, interaction.guild.id, interaction.user.id),
            )
            exists = await cursor.fetchone()

        if not exists:
            return await interaction.response.send_message(f"❌ Backup `{backup_id}` not found for this server.", ephemeral=True)

        view = AdminConfirmView(initiator=interaction.user)
        await interaction.response.send_message(
            f"🗑️ **WARNING:** {interaction.user.mention} wants to **DELETE** backup `{backup_id}`.\n"
            "🛡️ **A second Administrator must click confirm to proceed.**",
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        await view.wait()

        if not view.confirmed:
            if not view.cancelled:
                try:
                    await interaction.edit_original_response(content="⌛ Confirmation timed out. Nothing was deleted.", view=view)
                except discord.HTTPException:
                    pass
            return

        async with get_db_connection() as db:
            await db.execute(
                f"DELETE FROM backups WHERE backup_id = ? AND {ACCESS_SQL}",
                (backup_id, interaction.guild.id, interaction.user.id),
            )
            await db.commit()

        await interaction.followup.send(
            f"🗑️ Backup `{backup_id}` has been deleted by {interaction.user.mention} and {view.confirmed_by.mention}."
        )


async def setup(bot):
    await bot.add_cog(Backup(bot))
