import discord
from discord.ext import commands
from discord import app_commands
from database import get_db_connection
import json
import uuid
import asyncio
import base64
import aiohttp
from datetime import datetime

# ------------------------------------------------------------------
# UI VIEW: TWO-ADMIN CONFIRMATION
# ------------------------------------------------------------------
class AdminConfirmView(discord.ui.View):
    def __init__(self, initiator: discord.Member, timeout=120):
        super().__init__(timeout=timeout)
        self.initiator = initiator
        self.confirmed = False
        self.confirmed_by = None

    @discord.ui.button(label="Confirm Action", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Check if the user clicking is an admin
        if not interaction.permissions.administrator:
            return await interaction.response.send_message("❌ You must be an Administrator to confirm.", ephemeral=True)
        
        # 2. Check if the user clicking is a DIFFERENT admin
        if interaction.user.id == self.initiator.id:
            return await interaction.response.send_message("❌ A *second* Administrator must confirm this action.", ephemeral=True)

        self.confirmed = True
        self.confirmed_by = interaction.user
        
        # Disable buttons after confirmation
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(content=f"✅ Action confirmed by {self.confirmed_by.mention}.", view=self)
        self.stop()
        
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only the initiator or another admin can cancel
        if interaction.user.id != self.initiator.id and not interaction.permissions.administrator:
            return await interaction.response.send_message("❌ You do not have permission to cancel this.", ephemeral=True)
            
        self.confirmed = False
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(content="🚫 Action cancelled.", view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        self.stop()


class Backup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_image_base64(self, url):
        """Downloads an image and converts to base64 for storage."""
        if not url: return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(url)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        return base64.b64encode(data).decode('utf-8')
        except:
            return None
        return None

    # ------------------------------------------------------------------
    # 1. CREATE BACKUP
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-create", description="Create a full backup (Roles, Channels, Emojis)")
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_create(self, interaction: discord.Interaction):
        await interaction.response.send_message("📦 **Starting backup...** This may take a moment.", ephemeral=True)
        guild = interaction.guild
        
        # Structure
        backup_data = {
            "name": guild.name,
            "date": str(datetime.now()),
            "roles": [],
            "categories": [],
            "text_channels": [],
            "voice_channels": [],
            "emojis": []
        }

        # A. Roles (Reverse order for hierarchy)
        # Skip managed roles (bot integrations) and @everyone
        for role in reversed(guild.roles):
            if not role.is_default() and not role.managed:
                backup_data["roles"].append({
                    "name": role.name,
                    "color": role.color.value,
                    "permissions": role.permissions.value,
                    "hoist": role.hoist,
                    "mentionable": role.mentionable
                })

        # B. Categories & Channels
        for category in guild.categories:
            cat_data = {
                "name": category.name,
                "position": category.position,
                "channels": []
            }
            for channel in category.channels:
                chan_data = {
                    "type": str(channel.type),
                    "name": channel.name,
                    "position": channel.position,
                    "topic": getattr(channel, "topic", None),
                    "nsfw": getattr(channel, "nsfw", False),
                    "bitrate": getattr(channel, "bitrate", None),
                    "user_limit": getattr(channel, "user_limit", None)
                }
                cat_data["channels"].append(chan_data)
            backup_data["categories"].append(cat_data)

        # C. Standalone Channels (No Category)
        for channel in guild.channels:
            if channel.category is None:
                if isinstance(channel, discord.TextChannel):
                    backup_data["text_channels"].append({"name": channel.name, "topic": channel.topic})
                elif isinstance(channel, discord.VoiceChannel):
                    backup_data["voice_channels"].append({"name": channel.name})

        # D. Emojis (Heavy Operation)
        for emoji in guild.emojis:
            img_data = await self.get_image_base64(emoji.url)
            if img_data:
                backup_data["emojis"].append({
                    "name": emoji.name,
                    "image": img_data
                })

        # Save to DB
        backup_id = str(uuid.uuid4())[:8]
        json_data = json.dumps(backup_data)
        
        async with get_db_connection() as db:
            await db.execute("INSERT INTO backups (backup_id, user_id, data) VALUES (?, ?, ?)", 
                             (backup_id, interaction.user.id, json_data))
            await db.commit()

        await interaction.followup.send(f"✅ **Backup Created!**\n🆔 ID: `{backup_id}`\n💾 Saved: {len(backup_data['roles'])} roles, {len(guild.channels)} channels.")

    # ------------------------------------------------------------------
    # 2. LOAD BACKUP (Requires 2 Admins)
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-load", description="⚠️ Load a backup. WIPES CURRENT SERVER.")
    @app_commands.describe(backup_id="The ID of the backup to load")
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_load(self, interaction: discord.Interaction, backup_id: str):
        # Verification
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT data FROM backups WHERE backup_id = ?", (backup_id,))
            row = await cursor.fetchone()
        
        if not row:
            return await interaction.response.send_message("❌ Backup ID not found.", ephemeral=True)

        # Initiate 2-Admin Verification View
        view = AdminConfirmView(initiator=interaction.user)
        await interaction.response.send_message(
            f"⚠️ **WARNING:** {interaction.user.mention} has requested to **LOAD BACKUP `{backup_id}`**.\n"
            "This will **WIPE THE CURRENT SERVER** (Delete all channels and roles).\n\n"
            "🛡️ **A second Administrator must click confirm to proceed.**",
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True)
        )
        
        # Wait for the view to complete (Confirm, Cancel, or Timeout)
        await view.wait()
        
        if not view.confirmed:
            return # Action was cancelled or timed out

        # Proceed with load logic
        await interaction.followup.send("⚠️ **LOADING BACKUP...**\nDeleting existing channels/roles... This will take time.")
        
        data = json.loads(row[0])
        guild = interaction.guild

        # A. NUKE SERVER (Delete Channels & Roles)
        for channel in guild.channels:
            try:
                await channel.delete()
                await asyncio.sleep(0.2) 
            except: pass
        
        for role in guild.roles:
            if not role.is_default() and not role.managed:
                try:
                    await role.delete()
                    await asyncio.sleep(0.2)
                except: pass

        # B. CREATE ROLES
        for role_data in data["roles"]:
            try:
                await guild.create_role(
                    name=role_data["name"],
                    color=discord.Color(role_data["color"]),
                    permissions=discord.Permissions(role_data["permissions"]),
                    hoist=role_data["hoist"],
                    mentionable=role_data["mentionable"]
                )
                await asyncio.sleep(0.5)
            except: pass

        # C. CREATE CATEGORIES & CHANNELS
        for cat_data in data["categories"]:
            try:
                category = await guild.create_category(name=cat_data["name"], position=cat_data["position"])
                
                for chan_data in cat_data["channels"]:
                    try:
                        if "text" in chan_data["type"]:
                            await guild.create_text_channel(
                                name=chan_data["name"], 
                                category=category,
                                topic=chan_data.get("topic"),
                                nsfw=chan_data.get("nsfw")
                            )
                        elif "voice" in chan_data["type"]:
                            await guild.create_voice_channel(
                                name=chan_data["name"],
                                category=category,
                                bitrate=chan_data.get("bitrate") if chan_data.get("bitrate") and chan_data.get("bitrate") <= 96000 else 64000,
                                user_limit=chan_data.get("user_limit")
                            )
                        await asyncio.sleep(0.5)
                    except: pass
            except: pass

        # D. RESTORE EMOJIS
        for emoji_data in data.get("emojis", []):
            try:
                if len(guild.emojis) < guild.emoji_limit:
                    img_bytes = base64.b64decode(emoji_data["image"])
                    await guild.create_custom_emoji(name=emoji_data["name"], image=img_bytes)
                    await asyncio.sleep(1.5)
            except: pass

        try:
            c = await guild.create_text_channel("backup-complete")
            await c.send(f"{interaction.user.mention} & {view.confirmed_by.mention} ✅ **Backup '{data['name']}' loaded successfully!**")
        except: pass

    # ------------------------------------------------------------------
    # 3. LIST BACKUPS
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-list", description="List all saved backups.")
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT backup_id, created_at, data FROM backups ORDER BY created_at DESC LIMIT 10")
            rows = await cursor.fetchall()

        if not rows:
            return await interaction.followup.send("📂 No backups found.")

        embed = discord.Embed(title="📂 Server Backups", color=discord.Color.blue())
        
        for row in rows:
            b_id = row[0]
            b_date = row[1]
            # Parse minimal data to get server name
            try:
                b_data = json.loads(row[2])
                server_name = b_data.get("name", "Unknown Server")
            except:
                server_name = "Corrupted Data"
                
            embed.add_field(
                name=f"🆔 `{b_id}`",
                value=f"**Server:** {server_name}\n**Date:** {b_date}",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # 4. DELETE BACKUP (Requires 2 Admins)
    # ------------------------------------------------------------------
    @app_commands.command(name="backup-delete", description="Delete a specific backup file.")
    @app_commands.describe(backup_id="The ID of the backup to delete")
    @app_commands.checks.has_permissions(administrator=True)
    async def backup_delete(self, interaction: discord.Interaction, backup_id: str):
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT 1 FROM backups WHERE backup_id = ?", (backup_id,))
            exists = await cursor.fetchone()
            
            if not exists:
                return await interaction.response.send_message(f"❌ Backup `{backup_id}` not found.", ephemeral=True)
            
        # Initiate 2-Admin Verification View
        view = AdminConfirmView(initiator=interaction.user)
        await interaction.response.send_message(
            f"🗑️ **WARNING:** {interaction.user.mention} wants to **DELETE** backup `{backup_id}`.\n"
            "🛡️ **A second Administrator must click confirm to proceed.**",
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True)
        )
        
        # Wait for the view to complete
        await view.wait()
        
        if not view.confirmed:
            return

        async with get_db_connection() as db:
            await db.execute("DELETE FROM backups WHERE backup_id = ?", (backup_id,))
            await db.commit()

        await interaction.followup.send(f"🗑️ Backup `{backup_id}` has been successfully deleted by {interaction.user.mention} and {view.confirmed_by.mention}.")

async def setup(bot):
    await bot.add_cog(Backup(bot))
