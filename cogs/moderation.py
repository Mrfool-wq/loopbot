import discord
from discord.ext import commands
import re
import time
from datetime import timedelta
from database import get_db_connection

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # HELPER FUNCTIONS
    # ==========================================

    async def send_mod_log(self, guild: discord.Guild, embed: discord.Embed):
        """Fetches the mod log channel from the database and sends the log embed."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT channel_id FROM mod_logs WHERE guild_id = ?", (guild.id,))
            row = await cursor.fetchone()
            
        if row:
            channel = guild.get_channel(row[0])
            if channel:
                await channel.send(embed=embed)

    def parse_time(self, time_str: str):
        """Parses a time string like '10m', '2h', '1d' into a timedelta."""
        match = re.match(r"(\d+)([mhd])", time_str.lower())
        if not match:
            return None
        amount, unit = int(match.group(1)), match.group(2)
        if unit == 'm':
            return timedelta(minutes=amount)
        elif unit == 'h':
            return timedelta(hours=amount)
        elif unit == 'd':
            return timedelta(days=amount)
        return None

    # ==========================================
    # SETUP & SETTINGS
    # ==========================================

    @commands.hybrid_command(name="setmodlog", description="Set the channel where moderation actions are logged.")
    @commands.has_permissions(administrator=True)
    async def setmodlog(self, ctx, channel: discord.TextChannel):
        await ctx.defer()
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO mod_logs (guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id = ?",
                (ctx.guild.id, channel.id, channel.id)
            )
            await db.commit()
            
        embed = discord.Embed(description=f"✅ Moderation logs will now be sent to {channel.mention}.", color=discord.Color.green())
        await ctx.send(embed=embed)

    # ==========================================
    # STANDARD MODERATION (Ban / Unban)
    # ==========================================

    @commands.hybrid_command(name="ban", description="Ban a member from the server.")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        await ctx.defer()
        
        if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=discord.Embed(description="🚫 You cannot ban someone with an equal or higher role.", color=discord.Color.red()))
        if ctx.guild.me.top_role <= member.top_role:
            return await ctx.send(embed=discord.Embed(description="🚫 I cannot ban this user because their role is higher than mine.", color=discord.Color.red()))

        try:
            await member.ban(reason=f"{ctx.author}: {reason}")
            
            # Channel Response Embed
            embed_response = discord.Embed(description=f"🔨 **{member.display_name}** has been banned.\n**Reason:** {reason}", color=discord.Color.red())
            await ctx.send(embed=embed_response)
            
            # Log Embed
            embed_log = discord.Embed(title="Member Banned", color=discord.Color.red())
            embed_log.add_field(name="User", value=f"{member.mention} ({member.id})", inline=True)
            embed_log.add_field(name="Moderator", value=ctx.author.mention, inline=True)
            embed_log.add_field(name="Reason", value=reason, inline=False)
            await self.send_mod_log(ctx.guild, embed_log)
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"❌ Failed to ban user: {e}", color=discord.Color.red()))

    @commands.hybrid_command(name="unban", description="Unban a user from the server using their ID.")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: str, *, reason: str = "No reason provided"):
        await ctx.defer()
        try:
            user = await self.bot.fetch_user(int(user_id))
            await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
            
            # Channel Response Embed
            embed_response = discord.Embed(description=f"✅ **{user.name}** has been unbanned.\n**Reason:** {reason}", color=discord.Color.green())
            await ctx.send(embed=embed_response)
            
            # Log Embed
            embed_log = discord.Embed(title="Member Unbanned", color=discord.Color.green())
            embed_log.add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
            embed_log.add_field(name="Moderator", value=ctx.author.mention, inline=True)
            embed_log.add_field(name="Reason", value=reason, inline=False)
            await self.send_mod_log(ctx.guild, embed_log)
        except discord.NotFound:
            await ctx.send(embed=discord.Embed(description="❌ User not found. Make sure you are using a valid User ID.", color=discord.Color.red()))
        except discord.HTTPException:
            await ctx.send(embed=discord.Embed(description="❌ Failed to unban user. They might not be banned.", color=discord.Color.red()))

    # ==========================================
    # MUTES & TIMEOUTS
    # ==========================================

    @commands.hybrid_command(name="mute", description="Mute (timeout) a member. Use m, h, or d (e.g., 10m, 2h).")
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
        await ctx.defer()
        
        parsed_duration = self.parse_time(duration)
        if not parsed_duration:
            return await ctx.send(embed=discord.Embed(description="❌ Invalid time format! Use `m` for minutes, `h` for hours, or `d` for days (e.g., `10m`, `2h`).", color=discord.Color.red()))
        
        if parsed_duration > timedelta(days=28):
            return await ctx.send(embed=discord.Embed(description="🚫 You cannot mute someone for more than 28 days due to Discord's limits.", color=discord.Color.red()))

        if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=discord.Embed(description="🚫 You cannot mute someone with an equal or higher role.", color=discord.Color.red()))

        try:
            await member.timeout(discord.utils.utcnow() + parsed_duration, reason=f"{ctx.author}: {reason}")
            
            # Channel Response Embed
            embed_response = discord.Embed(description=f"🔇 **{member.display_name}** has been muted for **{duration}**.\n**Reason:** {reason}", color=discord.Color.orange())
            await ctx.send(embed=embed_response)
            
            # Log Embed
            embed_log = discord.Embed(title="Member Muted", color=discord.Color.orange())
            embed_log.add_field(name="User", value=f"{member.mention} ({member.id})", inline=True)
            embed_log.add_field(name="Duration", value=duration, inline=True)
            embed_log.add_field(name="Moderator", value=ctx.author.mention, inline=True)
            embed_log.add_field(name="Reason", value=reason, inline=False)
            await self.send_mod_log(ctx.guild, embed_log)
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"❌ Failed to mute user: {e}", color=discord.Color.red()))

    @commands.hybrid_command(name="unmute", description="Remove a timeout from a member.")
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        await ctx.defer()
        try:
            await member.timeout(None, reason=f"{ctx.author}: {reason}")
            
            # Channel Response Embed
            embed_response = discord.Embed(description=f"🔊 **{member.display_name}** has been unmuted.\n**Reason:** {reason}", color=discord.Color.green())
            await ctx.send(embed=embed_response)
            
            # Log Embed
            embed_log = discord.Embed(title="Member Unmuted", color=discord.Color.green())
            embed_log.add_field(name="User", value=f"{member.mention} ({member.id})", inline=True)
            embed_log.add_field(name="Moderator", value=ctx.author.mention, inline=True)
            embed_log.add_field(name="Reason", value=reason, inline=False)
            await self.send_mod_log(ctx.guild, embed_log)
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"❌ Failed to unmute user: {e}", color=discord.Color.red()))

    # ==========================================
    # UTILITY MODERATION (Nick, AFK)
    # ==========================================

    @commands.hybrid_command(name="nick", description="Change a member's nickname.")
    @commands.has_permissions(manage_nicknames=True)
    async def nick(self, ctx, member: discord.Member, *, new_nickname: str = None):
        await ctx.defer()
        if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=discord.Embed(description="🚫 You cannot change the nickname of someone with an equal or higher role.", color=discord.Color.red()))

        try:
            await member.edit(nick=new_nickname)
            if new_nickname:
                await ctx.send(embed=discord.Embed(description=f"✅ Changed **{member.name}**'s nickname to `{new_nickname}`.", color=discord.Color.blue()))
            else:
                await ctx.send(embed=discord.Embed(description=f"✅ Reset **{member.name}**'s nickname.", color=discord.Color.blue()))
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"❌ Failed to change nickname: {e}", color=discord.Color.red()))

    @commands.hybrid_command(name="afk", description="Set your status to AFK.")
    async def afk(self, ctx, *, reason: str = "AFK"):
        await ctx.defer()
        timestamp = time.time()
        
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO afk_users (user_id, guild_id, reason, timestamp) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, guild_id) DO UPDATE SET reason = ?, timestamp = ?",
                (ctx.author.id, ctx.guild.id, reason, timestamp, reason, timestamp)
            )
            await db.commit()
            
        await ctx.send(embed=discord.Embed(description=f"💤 I've set your AFK status to: **{reason}**", color=discord.Color.dark_grey()))

    # ==========================================
    # AFK MESSAGE LISTENER
    # ==========================================

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        async with get_db_connection() as db:
            # 1. Remove AFK if the user speaks
            cursor = await db.execute("SELECT reason FROM afk_users WHERE user_id = ? AND guild_id = ?", (message.author.id, message.guild.id))
            if await cursor.fetchone():
                await db.execute("DELETE FROM afk_users WHERE user_id = ? AND guild_id = ?", (message.author.id, message.guild.id))
                await db.commit()
                
                embed = discord.Embed(description=f"👋 Welcome back {message.author.mention}! I've removed your AFK status.", color=discord.Color.green())
                msg = await message.channel.send(embed=embed)
                await msg.delete(delay=5)

            # 2. Check if a mentioned user is AFK
            if message.mentions:
                for user in message.mentions:
                    cursor = await db.execute("SELECT reason, timestamp FROM afk_users WHERE user_id = ? AND guild_id = ?", (user.id, message.guild.id))
                    row = await cursor.fetchone()
                    if row:
                        reason, timestamp = row
                        embed = discord.Embed(description=f"💤 **{user.display_name}** is currently AFK: {reason} (<t:{int(timestamp)}:R>)", color=discord.Color.orange())
                        await message.channel.send(embed=embed)

    # ==========================================
    # ROLE COMMANDS
    # ==========================================

    @commands.hybrid_command(name="addrole", description="Assign a specific role to a server member.")
    @commands.has_permissions(manage_roles=True)
    async def addrole(self, ctx, member: discord.Member, role: discord.Role):
        await ctx.defer()
        if ctx.author.top_role <= role:
            return await ctx.send(embed=discord.Embed(description="🚫 You cannot give a role higher than or equal to your own.", color=discord.Color.red()))
        if ctx.guild.me.top_role <= role:
            return await ctx.send(embed=discord.Embed(description="🚫 I cannot give this role (it is higher than my highest role).", color=discord.Color.red()))
        if role in member.roles:
            return await ctx.send(embed=discord.Embed(description=f"⚠️ {member.mention} already has the role {role.mention}.", color=discord.Color.orange()))
        
        await member.add_roles(role)
        await ctx.send(embed=discord.Embed(description=f"✅ Added {role.mention} to {member.mention}.", color=discord.Color.green()))

    @commands.hybrid_command(name="removerole", description="Remove a specific role from a server member.")
    @commands.has_permissions(manage_roles=True)
    async def removerole(self, ctx, member: discord.Member, role: discord.Role):
        await ctx.defer()
        if ctx.author.top_role <= role:
            return await ctx.send(embed=discord.Embed(description="🚫 You cannot remove a role higher than or equal to your own.", color=discord.Color.red()))
        if role not in member.roles:
            return await ctx.send(embed=discord.Embed(description=f"⚠️ {member.mention} does not have the role {role.mention}.", color=discord.Color.orange()))

        await member.remove_roles(role)
        await ctx.send(embed=discord.Embed(description=f"🗑️ Removed {role.mention} from {member.mention}.", color=discord.Color.red()))

    @commands.hybrid_command(name="roleedit", description="Modify role settings (Color, Hoist, Mentionable).")
    @commands.has_permissions(manage_roles=True)
    @discord.app_commands.choices(action=[
        discord.app_commands.Choice(name="Toggle Mentionable", value="mention"),
        discord.app_commands.Choice(name="Toggle Hoist (Display Separate)", value="hoist"),
        discord.app_commands.Choice(name="Change Color", value="color")
    ])
    async def roleedit(self, ctx, role: discord.Role, action: str, value: str):
        await ctx.defer()
        if ctx.guild.me.top_role <= role:
            return await ctx.send(embed=discord.Embed(description="🚫 I cannot edit this role (it is above me).", color=discord.Color.red()))

        try:
            if action == "mention":
                is_mentionable = value.lower() in ['true', 'yes', 'on', '1']
                await role.edit(mentionable=is_mentionable)
                await ctx.send(embed=discord.Embed(description=f"✅ Set **{role.name}** mentionable to: `{is_mentionable}`", color=discord.Color.blue()))
            elif action == "hoist":
                is_hoist = value.lower() in ['true', 'yes', 'on', '1']
                await role.edit(hoist=is_hoist)
                await ctx.send(embed=discord.Embed(description=f"✅ Set **{role.name}** to display separately: `{is_hoist}`", color=discord.Color.blue()))
            elif action == "color":
                hex_code = value.replace("#", "")
                try:
                    new_color = discord.Color(int(hex_code, 16))
                    await role.edit(color=new_color)
                    await ctx.send(embed=discord.Embed(description=f"✅ Changed **{role.name}** color to `#{hex_code}`", color=new_color))
                except ValueError:
                    await ctx.send(embed=discord.Embed(description="❌ Invalid Hex Color code (e.g., use FF0000 for red).", color=discord.Color.red()))
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"❌ Error editing role: {e}", color=discord.Color.red()))

    @commands.hybrid_command(name="roleinfo", description="View all roles assigned to a specific user.")
    async def roleinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        roles = [role.mention for role in member.roles if role != ctx.guild.default_role]
        roles.reverse()
        
        role_list = ", ".join(roles) if roles else "No roles."
        embed = discord.Embed(title=f"🎭 Roles for {member.display_name}", description=role_list, color=member.color)
        embed.set_footer(text=f"Total: {len(roles)} Roles")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
