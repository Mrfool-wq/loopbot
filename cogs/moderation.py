import re
import time
from datetime import timedelta

import discord
from discord.ext import commands

from database import get_db_connection

TIME_RE = re.compile(r"(\d+)([mhd])")
HEX_RE = re.compile(r"[0-9a-fA-F]{6}")
MAX_REASON_LENGTH = 500
MAX_AFK_REASON = 200
MAX_AFK_NOTICES = 3  # how many "X is AFK" notices one message can trigger


def parse_time(time_str):
    """Parses '10m', '2h', '1d' into a timedelta. Returns None if the format is wrong."""
    match = TIME_RE.fullmatch(time_str.strip().lower())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    if amount > 100000:
        return None
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def box(text, color):
    return discord.Embed(description=text, color=color)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # (guild_id, user_id) -> (reason, timestamp, trigger_id)
        # Kept in memory so normal messages never have to touch the database.
        self.afk_cache = {}

    async def cog_load(self):
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT user_id, guild_id, reason, timestamp FROM afk_users")
            for user_id, guild_id, reason, timestamp in await cursor.fetchall():
                self.afk_cache[(guild_id, user_id)] = (reason, timestamp, 0)

    # ==========================================
    # HELPER FUNCTIONS
    # ==========================================

    async def send_mod_log(self, guild: discord.Guild, embed: discord.Embed):
        """Sends the log embed to the mod log channel. Never raises: the action already happened."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT channel_id FROM mod_logs WHERE guild_id = ?", (guild.id,))
            row = await cursor.fetchone()

        if row:
            channel = guild.get_channel(row[0])
            if channel:
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

    def parse_time(self, time_str: str):
        return parse_time(time_str)

    @staticmethod
    def log_embed(title, color, user, moderator, reason=None, extra=None):
        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
        if extra:
            embed.add_field(name=extra[0], value=extra[1], inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        if reason is not None:
            embed.add_field(name="Reason", value=reason, inline=False)
        return embed

    # ==========================================
    # SETUP & SETTINGS
    # ==========================================

    @commands.hybrid_command(name="setmodlog", description="Set the channel where moderation actions are logged.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setmodlog(self, ctx, channel: discord.TextChannel):
        await ctx.defer()

        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await ctx.send(embed=box(f"❌ I need **Send Messages** and **Embed Links** in {channel.mention}.", discord.Color.red()))

        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO mod_logs (guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id = ?",
                (ctx.guild.id, channel.id, channel.id)
            )
            await db.commit()

        await ctx.send(embed=box(f"✅ Moderation logs will now be sent to {channel.mention}.", discord.Color.green()))

    # ==========================================
    # STANDARD MODERATION (Ban / Unban)
    # ==========================================

    @commands.hybrid_command(name="ban", description="Ban a user from the server (use their ID to ban someone who already left).")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, user: discord.User, *, reason: str = "No reason provided"):
        await ctx.defer()

        if len(reason) > MAX_REASON_LENGTH:
            return await ctx.send(embed=box(f"❌ Reason is too long (max {MAX_REASON_LENGTH} characters).", discord.Color.red()))
        if user.id == ctx.author.id:
            return await ctx.send(embed=box("🚫 You can't ban yourself.", discord.Color.red()))
        if user.id == self.bot.user.id:
            return await ctx.send(embed=box("🚫 I'm not going to ban myself.", discord.Color.red()))
        if user.id == ctx.guild.owner_id:
            return await ctx.send(embed=box("🚫 You can't ban the server owner.", discord.Color.red()))

        # Role checks only apply if they're still in the server.
        member = ctx.guild.get_member(user.id)
        if member:
            if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
                return await ctx.send(embed=box("🚫 You cannot ban someone with an equal or higher role.", discord.Color.red()))
            if ctx.guild.me.top_role <= member.top_role:
                return await ctx.send(embed=box("🚫 I cannot ban this user because their role is higher than mine.", discord.Color.red()))

        try:
            await ctx.guild.fetch_ban(user)
            return await ctx.send(embed=box(f"⚠️ **{user.display_name}** is already banned.", discord.Color.orange()))
        except discord.NotFound:
            pass  # not banned yet: good

        try:
            await ctx.guild.ban(user, reason=f"{ctx.author}: {reason}"[:512])
        except discord.Forbidden:
            return await ctx.send(embed=box("❌ I don't have permission to ban that user.", discord.Color.red()))
        except discord.HTTPException as e:
            return await ctx.send(embed=box(f"❌ Failed to ban user: {e}", discord.Color.red()))

        await ctx.send(embed=box(f"🔨 **{user.display_name}** has been banned.\n**Reason:** {reason}", discord.Color.red()))
        await self.send_mod_log(ctx.guild, self.log_embed("Member Banned", discord.Color.red(), user, ctx.author, reason))

    @commands.hybrid_command(name="unban", description="Unban a user from the server using their ID.")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: str, *, reason: str = "No reason provided"):
        await ctx.defer()

        if len(reason) > MAX_REASON_LENGTH:
            return await ctx.send(embed=box(f"❌ Reason is too long (max {MAX_REASON_LENGTH} characters).", discord.Color.red()))

        try:
            target_id = int(user_id.strip())
        except ValueError:
            return await ctx.send(embed=box("❌ That isn't a valid User ID (it should be a long number).", discord.Color.red()))

        try:
            user = await self.bot.fetch_user(target_id)
        except discord.NotFound:
            return await ctx.send(embed=box("❌ User not found. Make sure you are using a valid User ID.", discord.Color.red()))

        try:
            await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}"[:512])
        except discord.NotFound:
            return await ctx.send(embed=box(f"⚠️ **{user.name}** isn't banned.", discord.Color.orange()))
        except discord.HTTPException:
            return await ctx.send(embed=box("❌ Failed to unban user.", discord.Color.red()))

        await ctx.send(embed=box(f"✅ **{user.name}** has been unbanned.\n**Reason:** {reason}", discord.Color.green()))
        await self.send_mod_log(ctx.guild, self.log_embed("Member Unbanned", discord.Color.green(), user, ctx.author, reason))

    # ==========================================
    # MUTES & TIMEOUTS
    # ==========================================

    @commands.hybrid_command(name="mute", description="Mute (timeout) a member. Use m, h, or d (e.g., 10m, 2h).")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
        await ctx.defer()

        parsed_duration = parse_time(duration)
        if parsed_duration is None:
            return await ctx.send(embed=box("❌ Invalid time format! Use `m` for minutes, `h` for hours, or `d` for days (e.g., `10m`, `2h`).", discord.Color.red()))
        if parsed_duration <= timedelta(0):
            return await ctx.send(embed=box("❌ The duration must be greater than zero.", discord.Color.red()))
        if parsed_duration > timedelta(days=28):
            return await ctx.send(embed=box("🚫 You cannot mute someone for more than 28 days due to Discord's limits.", discord.Color.red()))
        if len(reason) > MAX_REASON_LENGTH:
            return await ctx.send(embed=box(f"❌ Reason is too long (max {MAX_REASON_LENGTH} characters).", discord.Color.red()))

        if member.id == ctx.guild.owner_id or member.guild_permissions.administrator:
            return await ctx.send(embed=box("🚫 Administrators and the server owner can't be timed out.", discord.Color.red()))
        if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=box("🚫 You cannot mute someone with an equal or higher role.", discord.Color.red()))
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.send(embed=box("🚫 I cannot mute this user because their role is higher than mine.", discord.Color.red()))

        try:
            await member.timeout(discord.utils.utcnow() + parsed_duration, reason=f"{ctx.author}: {reason}"[:512])
        except discord.Forbidden:
            return await ctx.send(embed=box("❌ I don't have permission to mute that user.", discord.Color.red()))
        except discord.HTTPException as e:
            return await ctx.send(embed=box(f"❌ Failed to mute user: {e}", discord.Color.red()))

        await ctx.send(embed=box(f"🔇 **{member.display_name}** has been muted for **{duration}**.\n**Reason:** {reason}", discord.Color.orange()))
        await self.send_mod_log(
            ctx.guild,
            self.log_embed("Member Muted", discord.Color.orange(), member, ctx.author, reason, extra=("Duration", duration)),
        )

    @commands.hybrid_command(name="unmute", description="Remove a timeout from a member.")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        await ctx.defer()

        if len(reason) > MAX_REASON_LENGTH:
            return await ctx.send(embed=box(f"❌ Reason is too long (max {MAX_REASON_LENGTH} characters).", discord.Color.red()))
        if not member.is_timed_out():
            return await ctx.send(embed=box(f"⚠️ **{member.display_name}** isn't muted.", discord.Color.orange()))

        try:
            await member.timeout(None, reason=f"{ctx.author}: {reason}"[:512])
        except discord.Forbidden:
            return await ctx.send(embed=box("❌ I don't have permission to unmute that user.", discord.Color.red()))
        except discord.HTTPException as e:
            return await ctx.send(embed=box(f"❌ Failed to unmute user: {e}", discord.Color.red()))

        await ctx.send(embed=box(f"🔊 **{member.display_name}** has been unmuted.\n**Reason:** {reason}", discord.Color.green()))
        await self.send_mod_log(ctx.guild, self.log_embed("Member Unmuted", discord.Color.green(), member, ctx.author, reason))

    # ==========================================
    # UTILITY MODERATION (Nick, AFK)
    # ==========================================

    @commands.hybrid_command(name="nick", description="Change a member's nickname.")
    @commands.guild_only()
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nick(self, ctx, member: discord.Member, *, new_nickname: str = None):
        await ctx.defer()

        if new_nickname and len(new_nickname) > 32:
            return await ctx.send(embed=box("❌ Nicknames can be at most 32 characters.", discord.Color.red()))
        if member.id == ctx.guild.owner_id:
            return await ctx.send(embed=box("🚫 Discord doesn't let me change the server owner's nickname.", discord.Color.red()))
        if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=box("🚫 You cannot change the nickname of someone with an equal or higher role.", discord.Color.red()))
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.send(embed=box("🚫 I cannot change that user's nickname because their role is higher than mine.", discord.Color.red()))

        try:
            await member.edit(nick=new_nickname)
        except discord.HTTPException as e:
            return await ctx.send(embed=box(f"❌ Failed to change nickname: {e}", discord.Color.red()))

        if new_nickname:
            await ctx.send(embed=box(f"✅ Changed **{member.name}**'s nickname to `{new_nickname}`.", discord.Color.blue()))
        else:
            await ctx.send(embed=box(f"✅ Reset **{member.name}**'s nickname.", discord.Color.blue()))

    @commands.hybrid_command(name="afk", description="Set your status to AFK.")
    @commands.guild_only()
    async def afk(self, ctx, *, reason: str = "AFK"):
        await ctx.defer()

        if len(reason) > MAX_AFK_REASON:
            return await ctx.send(embed=box(f"❌ Keep your AFK reason under {MAX_AFK_REASON} characters.", discord.Color.red()))

        timestamp = time.time()
        # The ID of the message/interaction that ran this command. Any message with an ID
        # at or below it is "this command itself" and must NOT cancel the AFK we just set.
        trigger_id = ctx.interaction.id if ctx.interaction else ctx.message.id
        self.afk_cache[(ctx.guild.id, ctx.author.id)] = (reason, timestamp, trigger_id)

        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO afk_users (user_id, guild_id, reason, timestamp) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, guild_id) DO UPDATE SET reason = ?, timestamp = ?",
                (ctx.author.id, ctx.guild.id, reason, timestamp, reason, timestamp)
            )
            await db.commit()

        await ctx.send(embed=box(f"💤 I've set your AFK status to: **{reason}**", discord.Color.dark_grey()))

    # ==========================================
    # AFK MESSAGE LISTENER
    # ==========================================

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id

        # 1. Remove AFK if the user speaks (in-memory check: no database work for normal messages)
        key = (guild_id, message.author.id)
        entry = self.afk_cache.get(key)
        if entry and message.id > entry[2]:
            self.afk_cache.pop(key, None)
            async with get_db_connection() as db:
                await db.execute("DELETE FROM afk_users WHERE user_id = ? AND guild_id = ?", (message.author.id, guild_id))
                await db.commit()

            try:
                msg = await message.channel.send(embed=box(f"👋 Welcome back {message.author.mention}! I've removed your AFK status.", discord.Color.green()))
                await msg.delete(delay=5)
            except discord.HTTPException:
                pass

        # 2. Tell people when someone they mention is AFK
        if message.mentions:
            notices = 0
            for user in message.mentions:
                if user.id == message.author.id:
                    continue
                afk = self.afk_cache.get((guild_id, user.id))
                if not afk:
                    continue

                reason, timestamp, _ = afk
                try:
                    await message.channel.send(embed=box(f"💤 **{user.display_name}** is currently AFK: {reason} (<t:{int(timestamp)}:R>)", discord.Color.orange()))
                except discord.HTTPException:
                    return
                notices += 1
                if notices >= MAX_AFK_NOTICES:
                    break

    # ==========================================
    # ROLE COMMANDS
    # ==========================================

    @commands.hybrid_command(name="addrole", description="Assign a specific role to a server member.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def addrole(self, ctx, member: discord.Member, role: discord.Role):
        await ctx.defer()
        if role.is_default() or role.managed:
            return await ctx.send(embed=box("🚫 That role can't be assigned manually (it's @everyone or managed by a bot/integration).", discord.Color.red()))
        if ctx.author.top_role <= role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=box("🚫 You cannot give a role higher than or equal to your own.", discord.Color.red()))
        if ctx.guild.me.top_role <= role:
            return await ctx.send(embed=box("🚫 I cannot give this role (it is higher than my highest role).", discord.Color.red()))
        if role in member.roles:
            return await ctx.send(embed=box(f"⚠️ {member.mention} already has the role {role.mention}.", discord.Color.orange()))

        try:
            await member.add_roles(role, reason=f"Added by {ctx.author}")
        except discord.HTTPException as e:
            return await ctx.send(embed=box(f"❌ Failed to add the role: {e}", discord.Color.red()))
        await ctx.send(embed=box(f"✅ Added {role.mention} to {member.mention}.", discord.Color.green()))

    @commands.hybrid_command(name="removerole", description="Remove a specific role from a server member.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def removerole(self, ctx, member: discord.Member, role: discord.Role):
        await ctx.defer()
        if role.is_default() or role.managed:
            return await ctx.send(embed=box("🚫 That role can't be removed manually (it's @everyone or managed by a bot/integration).", discord.Color.red()))
        if ctx.author.top_role <= role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=box("🚫 You cannot remove a role higher than or equal to your own.", discord.Color.red()))
        if ctx.guild.me.top_role <= role:
            return await ctx.send(embed=box("🚫 I cannot remove this role (it is higher than my highest role).", discord.Color.red()))
        if role not in member.roles:
            return await ctx.send(embed=box(f"⚠️ {member.mention} does not have the role {role.mention}.", discord.Color.orange()))

        try:
            await member.remove_roles(role, reason=f"Removed by {ctx.author}")
        except discord.HTTPException as e:
            return await ctx.send(embed=box(f"❌ Failed to remove the role: {e}", discord.Color.red()))
        await ctx.send(embed=box(f"🗑️ Removed {role.mention} from {member.mention}.", discord.Color.red()))

    @commands.hybrid_command(name="roleedit", description="Modify role settings (Color, Hoist, Mentionable).")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.choices(action=[
        discord.app_commands.Choice(name="Toggle Mentionable", value="mention"),
        discord.app_commands.Choice(name="Toggle Hoist (Display Separate)", value="hoist"),
        discord.app_commands.Choice(name="Change Color", value="color")
    ])
    async def roleedit(self, ctx, role: discord.Role, action: str, value: str):
        await ctx.defer()
        if role.is_default() or role.managed:
            return await ctx.send(embed=box("🚫 That role can't be edited (it's @everyone or managed by a bot/integration).", discord.Color.red()))
        if ctx.author.top_role <= role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=box("🚫 You cannot edit a role higher than or equal to your own.", discord.Color.red()))
        if ctx.guild.me.top_role <= role:
            return await ctx.send(embed=box("🚫 I cannot edit this role (it is above me).", discord.Color.red()))

        action = action.lower()
        truthy = ["true", "yes", "on", "1"]

        try:
            if action == "mention":
                is_mentionable = value.lower() in truthy
                await role.edit(mentionable=is_mentionable)
                await ctx.send(embed=box(f"✅ Set **{role.name}** mentionable to: `{is_mentionable}`", discord.Color.blue()))
            elif action == "hoist":
                is_hoist = value.lower() in truthy
                await role.edit(hoist=is_hoist)
                await ctx.send(embed=box(f"✅ Set **{role.name}** to display separately: `{is_hoist}`", discord.Color.blue()))
            elif action == "color":
                hex_code = value.strip().lstrip("#")
                if not HEX_RE.fullmatch(hex_code):
                    return await ctx.send(embed=box("❌ Invalid Hex Color code (e.g., use FF0000 for red).", discord.Color.red()))
                new_color = discord.Color(int(hex_code, 16))
                await role.edit(color=new_color)
                await ctx.send(embed=box(f"✅ Changed **{role.name}** color to `#{hex_code.upper()}`", new_color))
            else:
                await ctx.send(embed=box("❌ Unknown action. Use `mention`, `hoist` or `color`.", discord.Color.red()))
        except discord.HTTPException as e:
            await ctx.send(embed=box(f"❌ Error editing role: {e}", discord.Color.red()))

    @commands.hybrid_command(name="roleinfo", description="View all roles assigned to a specific user.")
    @commands.guild_only()
    async def roleinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        roles = [role.mention for role in member.roles if role != ctx.guild.default_role]
        roles.reverse()

        role_list = ", ".join(roles) if roles else "No roles."
        if len(role_list) > 4000:
            role_list = role_list[:4000].rsplit(",", 1)[0] + ", …"
        embed = discord.Embed(title=f"🎭 Roles for {member.display_name}", description=role_list, color=member.color)
        embed.set_footer(text=f"Total: {len(roles)} Roles")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
