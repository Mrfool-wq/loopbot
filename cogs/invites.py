import discord
from discord.ext import commands
from database import get_db_connection

class Invites(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.invite_cache = {} # {guild_id: {code: uses}}
        self.vanity_cache = {} # {guild_id: uses}
        
        # Start DB Check
        self.bot.loop.create_task(self.check_db_schema())

    async def update_caches(self, guild):
        """Helper to fetch and cache both normal and vanity invites."""
        try:
            self.invite_cache[guild.id] = {}
            invites = await guild.invites()
            for invite in invites:
                self.invite_cache[guild.id][invite.code] = invite.uses

            # Cache vanity URL uses if the server has one
            if "VANITY_URL" in guild.features and guild.vanity_url_code:
                try:
                    vanity = await guild.vanity_invite()
                    self.vanity_cache[guild.id] = vanity.uses
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            print(f"[Invites] Missing permissions to fetch invites for {guild.name}")

    async def check_db_schema(self):
        """Creates the necessary tables for the invite system."""
        await self.bot.wait_until_ready()
        async with get_db_connection() as db:
            # Table to store total counts per user
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_counts (
                    guild_id INTEGER,
                    user_id INTEGER,
                    normal INTEGER DEFAULT 0,
                    left INTEGER DEFAULT 0,
                    fake INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            
            # Table to link a joined member to their inviter
            # inviter_id will be 0 if joined via Vanity URL
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_tracker (
                    guild_id INTEGER,
                    member_id INTEGER,
                    inviter_id INTEGER,
                    code TEXT,
                    is_bot INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, member_id)
                )
            """)

            # Table for log channel settings
            await db.execute("""
                CREATE TABLE IF NOT EXISTS invite_settings (
                    guild_id INTEGER PRIMARY KEY,
                    log_channel_id INTEGER
                )
            """)
            
            await db.commit()
            print("[Invites] Database tables checked/created.")
            
        # Initialize Cache
        for guild in self.bot.guilds:
            await self.update_caches(guild)

    # --- HELPERS ---
    
    async def update_invite_count(self, guild_id, user_id, change_type, amount=1):
        async with get_db_connection() as db:
            await db.execute("""
                INSERT OR IGNORE INTO invite_counts (guild_id, user_id) 
                VALUES (?, ?)
            """, (guild_id, user_id))
            
            if change_type == 'normal':
                query = "UPDATE invite_counts SET normal = normal + ? WHERE guild_id = ? AND user_id = ?"
            elif change_type == 'left':
                query = "UPDATE invite_counts SET left = left + ? WHERE guild_id = ? AND user_id = ?"
            elif change_type == 'fake':
                query = "UPDATE invite_counts SET fake = fake + ? WHERE guild_id = ? AND user_id = ?"
            
            await db.execute(query, (amount, guild_id, user_id))
            await db.commit()

    async def get_log_channel(self, guild):
        """Fetches the log channel object if set."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT log_channel_id FROM invite_settings WHERE guild_id = ?", (guild.id,))
            data = await cursor.fetchone()
            
        if data:
            return guild.get_channel(data[0])
        return None

    async def get_invites_count(self, guild_id, user_id):
        """Returns the total 'real' invites for a user."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT normal, left, fake FROM invite_counts WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            data = await cursor.fetchone()
        
        if not data: return 0
        return data[0] - data[1] - data[2]

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if invite.guild.id not in self.invite_cache:
            self.invite_cache[invite.guild.id] = {}
        self.invite_cache[invite.guild.id][invite.code] = invite.uses

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        if invite.guild.id in self.invite_cache:
            self.invite_cache[invite.guild.id].pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        inviter = None
        code_used = None
        
        # 1. BOT JOIN LOGIC
        if member.bot:
            try:
                async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.bot_add):
                    if entry.target.id == member.id:
                        inviter = entry.user
                        code_used = "OAuth2"
                        break
            except discord.Forbidden:
                pass 
        
        # 2. USER JOIN LOGIC
        else:
            try:
                current_invites = await guild.invites()
                cached_invites = self.invite_cache.get(guild.id, {})
                
                # Check Normal Invites
                for invite in current_invites:
                    old_uses = cached_invites.get(invite.code, 0)
                    if invite.uses > old_uses:
                        inviter = invite.inviter
                        code_used = invite.code
                        self.invite_cache[guild.id][invite.code] = invite.uses
                        break
                
                # Check Vanity Invite if no normal invite was used
                if not code_used and "VANITY_URL" in guild.features and guild.vanity_url_code:
                    try:
                        vanity = await guild.vanity_invite()
                        old_vanity_uses = self.vanity_cache.get(guild.id, 0)
                        if vanity.uses > old_vanity_uses:
                            code_used = vanity.code
                            inviter = None # Vanity URLs don't belong to a specific user
                            self.vanity_cache[guild.id] = vanity.uses
                    except discord.Forbidden:
                        pass
                
                if not code_used:
                    # Failsafe: just sync cache if we completely missed it
                    self.invite_cache[guild.id] = {i.code: i.uses for i in current_invites}
                    
            except discord.Forbidden:
                pass

        # 3. SAVE & LOG
        if code_used:
            is_bot_int = 1 if member.bot else 0
            inviter_id = inviter.id if inviter else 0 # 0 denotes Vanity URL / Unknown
            
            async with get_db_connection() as db:
                await db.execute("""
                    INSERT OR REPLACE INTO invite_tracker (guild_id, member_id, inviter_id, code, is_bot)
                    VALUES (?, ?, ?, ?, ?)
                """, (guild.id, member.id, inviter_id, code_used, is_bot_int))
                await db.commit()
            
            # Add point only if it's a normal user AND there is an actual inviter
            if not member.bot and inviter:
                await self.update_invite_count(guild.id, inviter.id, 'normal', 1)

            # --- SEND LOG MESSAGE ---
            log_channel = await self.get_log_channel(guild)
            if log_channel:
                if member.bot:
                    embed = discord.Embed(description=f"**Bot Added**\n{member.mention} was added by {inviter.mention if inviter else 'Unknown'}", color=discord.Color.orange())
                    embed.set_author(name=member.name, icon_url=member.display_avatar.url)
                    embed.set_footer(text="System Log")
                else:
                    embed = discord.Embed(description=f"{member.mention} joined", color=discord.Color.green())
                    embed.set_author(name=f"Member Joined", icon_url=member.display_avatar.url)
                    
                    if inviter:
                        total_invites = await self.get_invites_count(guild.id, inviter.id)
                        embed.add_field(name="Invited By", value=inviter.mention, inline=True)
                        embed.add_field(name="Code", value=f"`{code_used}`", inline=True)
                        embed.add_field(name="Total Invites", value=f"{total_invites}", inline=True)
                    else:
                        embed.add_field(name="Invited By", value="Custom Server Link (Vanity)", inline=True)
                        embed.add_field(name="Code", value=f"`{code_used}`", inline=True)
                        
                    embed.set_footer(text=f"ID: {member.id}")
                
                await log_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """When a user leaves, find their inviter, deduct a point, and log it."""
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT inviter_id FROM invite_tracker WHERE guild_id = ? AND member_id = ?", (member.guild.id, member.id))
            data = await cursor.fetchone()
            
            if data:
                inviter_id = data[0]
                
                # Only deduct points if they were invited by a real user (not vanity)
                if inviter_id != 0:
                    await self.update_invite_count(member.guild.id, inviter_id, 'left', 1)
                
                # Clean up tracker
                await db.execute("DELETE FROM invite_tracker WHERE guild_id = ? AND member_id = ?", (member.guild.id, member.id))
                await db.commit()

                # --- SEND LOG MESSAGE ---
                log_channel = await self.get_log_channel(member.guild)
                if log_channel:
                    if inviter_id == 0:
                        inviter_text = "Custom Server Link (Vanity)"
                    else:
                        inviter = member.guild.get_member(inviter_id)
                        inviter_text = inviter.mention if inviter else "Unknown/Left"
                    
                    embed = discord.Embed(description=f"{member.mention} left", color=discord.Color.red())
                    embed.set_author(name=f"Member Left", icon_url=member.display_avatar.url)
                    embed.add_field(name="Invited By", value=inviter_text, inline=True)
                    embed.set_footer(text=f"ID: {member.id}")
                    await log_channel.send(embed=embed)

    # --- COMMANDS ---

    @commands.hybrid_command(name="setinvitelog", description="Set the channel where invite logs will be sent.")
    @commands.has_permissions(administrator=True)
    async def setinvitelog(self, ctx, channel: discord.TextChannel):
        async with get_db_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO invite_settings (guild_id, log_channel_id)
                VALUES (?, ?)
            """, (ctx.guild.id, channel.id))
            await db.commit()
        await ctx.send(f"Logs will now be sent to {channel.mention}")

    @commands.hybrid_command(name="invites", description="Check how many people you have invited.")
    async def invites(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT normal, left, fake FROM invite_counts WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
            data = await cursor.fetchone()
        
        if not data:
            normal, left, fake = 0, 0, 0
        else:
            normal, left, fake = data
            
        real_invites = normal - left - fake
        
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=f"Invites for {member.display_name}", icon_url=member.display_avatar.url)
        embed.add_field(name="Real", value=str(real_invites), inline=True)
        embed.add_field(name="Left", value=str(left), inline=True)
        embed.add_field(name="Fake", value=str(fake), inline=True)
        embed.set_footer(text=f"Total Joins: {normal}")
        
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="addinvites", description="Admin: Add bonus invites to a user.")
    @commands.has_permissions(administrator=True)
    async def addinvites(self, ctx, member: discord.Member, amount: int):
        await self.update_invite_count(ctx.guild.id, member.id, 'normal', amount)
        await ctx.send(f"Added **{amount}** invites to {member.mention}.")

async def setup(bot):
    await bot.add_cog(Invites(bot))
