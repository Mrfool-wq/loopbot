import discord
import aiosqlite
import io
import datetime
from discord import app_commands, ui
from discord.ext import commands
from database import get_db_connection

# --- CONFIGURATION ---
# REPLACE THIS WITH YOUR LOG CHANNEL ID
LOG_CHANNEL_ID = 1463548787673796699 

# --- HELPERS ---
async def create_transcript(channel):
    """Generates a text transcript of the channel history."""
    buffer = io.BytesIO()
    
    header = f"TRANSCRIPT FOR: {channel.name}\nDATE: {datetime.datetime.now()}\n---------------------------------\n\n"
    buffer.write(header.encode('utf-8'))

    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        content = f"[{timestamp}] {msg.author.name}: {msg.content}\n"
        # Add attachments urls if any
        if msg.attachments:
            content += f"    [Attachments: {', '.join([a.url for a in msg.attachments])}]\n"
        buffer.write(content.encode('utf-8'))

    buffer.seek(0)
    return discord.File(buffer, filename=f"{channel.name}-transcript.txt")

async def close_ticket_sequence(interaction: discord.Interaction, status: str):
    """Shared function to handle closing, transcribing, and deleting."""
    
    # 1. Notify we are working on it
    if not interaction.response.is_done():
        await interaction.response.send_message("Processing request...", ephemeral=True)

    # 2. Get User ID from DB to DM them
    user_id = None
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT user_id FROM applications WHERE channel_id = ?", (interaction.channel.id,))
        result = await cursor.fetchone()
        if result:
            user_id = result[0]
            # Remove from DB immediately so they can apply again if needed
            await db.execute("DELETE FROM applications WHERE channel_id = ?", (interaction.channel.id,))
            await db.commit()

    # 3. Generate Transcript
    transcript_file = await create_transcript(interaction.channel)
    
    # 4. Send to Log Channel
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        # Re-create file pointer for second send
        transcript_file.fp.seek(0) 
        await log_channel.send(f"Transcript for {status} application: {interaction.channel.name}", file=transcript_file)

    # 5. Send to User (DM)
    if user_id:
        user = interaction.guild.get_member(user_id)
        if user:
            try:
                transcript_file.fp.seek(0)
                await user.send(f"Your application was {status}. Here is the transcript.", file=transcript_file)
            except discord.Forbidden:
                pass # User has DMs off

    # 6. Delete Channel
    await interaction.channel.delete()


# --- 1. THE FORM (MODAL) ---
class ApplicationModal(ui.Modal, title="Staff Application"):
    name = ui.TextInput(
        label="Name / Nickname", 
        placeholder="What should we call you?", 
        max_length=50
    )
    age = ui.TextInput(
        label="Age", 
        placeholder="e.g. 18", 
        max_length=3
    )
    experience = ui.TextInput(
        label="Experience", 
        style=discord.TextStyle.paragraph, 
        placeholder="Have you been a mod before? List servers...", 
        max_length=1000, 
        required=False
    )
    reason = ui.TextInput(
        label="Why should we pick you?", 
        style=discord.TextStyle.paragraph, 
        placeholder="Tell us why you are a good fit...", 
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Application received. Creating your private ticket...", ephemeral=True)
        
        guild = interaction.guild
        user = interaction.user

        # --- PERMISSION SETUP ---
        mod_role = discord.utils.get(guild.roles, name="Staff team」")
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        if mod_role:
            # Grant view/send access, but DENY delete access (manage_channels=False)
            overwrites[mod_role] = discord.PermissionOverwrite(
                read_messages=True, 
                send_messages=True, 
                manage_channels=False 
            )

        channel_name = f"app-{user.name.lower().replace(' ', '-')}"
        try:
            app_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        except Exception as e:
            await interaction.followup.send(f"Error: Could not create channel. {e}", ephemeral=True)
            return

        # Save to DB
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO applications (channel_id, guild_id, user_id, app_type, status) VALUES (?, ?, ?, ?, ?)",
                (app_channel.id, guild.id, user.id, "Staff", "Open")
            )
            await db.commit()

        # Build Embed
        embed = discord.Embed(title=f"Application: {user.name}", color=discord.Color.blue())
        embed.add_field(name="Name", value=self.name.value, inline=True)
        embed.add_field(name="Age", value=self.age.value, inline=True)
        embed.add_field(name="Experience", value=self.experience.value if self.experience.value else "None", inline=False)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.set_footer(text=f"User ID: {user.id}")

        # Send to channel with Persistent View
        view = AdminAppView()
        ping_msg = mod_role.mention if mod_role else "Admins"
        await app_channel.send(content=f"{ping_msg}, a new application has arrived.", embed=embed, view=view)
        await app_channel.send(f"{user.mention} Welcome. Please wait for the staff to review your application.")


# --- 2. ADMIN CONTROL PANEL (PERSISTENT) ---
class AdminAppView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent

    @ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="app_accept")
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        has_role = discord.utils.get(interaction.user.roles, name="Staff team」")
        if not (has_role or interaction.user.guild_permissions.administrator):
             await interaction.response.send_message("You do not have permission.", ephemeral=True)
             return

        button.disabled = True
        self.children[1].disabled = True 
        
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Accepted by {interaction.user.name}")
        
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.channel.send(f"Congratulations! Your application was accepted by {interaction.user.mention}.")

    @ui.button(label="Reject", style=discord.ButtonStyle.red, custom_id="app_reject")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        has_role = discord.utils.get(interaction.user.roles, name="Staff team」")
        if not (has_role or interaction.user.guild_permissions.administrator):
             await interaction.response.send_message("You do not have permission.", ephemeral=True)
             return

        await interaction.channel.send("Application Rejected. Closing ticket...")
        await close_ticket_sequence(interaction, "REJECTED")

    @ui.button(label="Close", style=discord.ButtonStyle.grey, emoji="🔒", custom_id="app_close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        has_role = discord.utils.get(interaction.user.roles, name="Staff team」")
        if not (has_role or interaction.user.guild_permissions.administrator):
             await interaction.response.send_message("You do not have permission.", ephemeral=True)
             return

        await interaction.response.send_message("Closing ticket...")
        await close_ticket_sequence(interaction, "CLOSED")


# --- 3. TRIGGER VIEW (For Text Command) ---
class StartAppView(ui.View):
    def __init__(self):
        super().__init__(timeout=60) 

    @ui.button(label="Open Application Form", style=discord.ButtonStyle.blurple, emoji="📝")
    async def open_form(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ApplicationModal())


# --- 4. THE MAIN COG ---
class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_view(AdminAppView())

    @commands.hybrid_command(name="apply", description="Start a staff application process.")
    async def apply(self, ctx):
        # 1. Check Database for duplicate applications
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT channel_id FROM applications WHERE user_id = ?", (ctx.author.id,))
            existing = await cursor.fetchone()
            
            if existing:
                # CHECK IF CHANNEL ACTUALLY EXISTS
                # We do this INSIDE the 'async with' block so 'db' is still open if we need to delete
                channel = ctx.guild.get_channel(existing[0])
                
                if channel:
                    await ctx.send(f"You already have an open application here: {channel.mention}", ephemeral=True)
                    return
                else:
                    # Channel was deleted manually while bot was offline; Clean DB
                    # The connection 'db' is still open here, so this will work now!
                    await db.execute("DELETE FROM applications WHERE channel_id = ?", (existing[0],))
                    await db.commit()

        # 2. Handle Input
        if ctx.interaction:
            await ctx.interaction.response.send_modal(ApplicationModal())
        else:
            view = StartAppView()
            await ctx.send("To start your application, please click the button below:", view=view)

async def setup(bot):
    await bot.add_cog(Applications(bot))
