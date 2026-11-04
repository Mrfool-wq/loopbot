import datetime
import io

import discord
from discord import ui
from discord.ext import commands

from database import get_db_connection
from guild_settings import LEGACY_STAFF_ROLE_NAME, get_settings

# (guild_id, user_id) pairs whose application channel is being created right now.
# Stops a double-click from creating two tickets.
_creating = set()


# --- SETTINGS HELPERS (the staff role and log channel are chosen with /setup) ---
async def get_staff_role(guild):
    """The configured staff role (falls back to the old role name until /setup is used)."""
    settings = await get_settings(guild.id)
    role = guild.get_role(settings["staff_role_id"]) if settings["staff_role_id"] else None
    return role or discord.utils.get(guild.roles, name=LEGACY_STAFF_ROLE_NAME)


async def get_log_channel(guild):
    settings = await get_settings(guild.id)
    channel_id = settings["application_log_channel_id"]
    return guild.get_channel(channel_id) if channel_id else None


async def is_staff(interaction):
    """Administrators and members with the staff role can manage applications."""
    if interaction.user.guild_permissions.administrator:
        return True
    role = await get_staff_role(interaction.guild)
    return role is not None and role in interaction.user.roles


# --- HELPERS ---
async def build_transcript(channel):
    """Builds a text transcript of the channel. Returns (bytes, filename)."""
    lines = [f"TRANSCRIPT FOR: {channel.name}\nDATE: {datetime.datetime.now()}\n---------------------------------\n\n"]

    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{timestamp}] {msg.author.name}: {msg.content}\n")

        # The application answers live inside an embed, so they must be written out too.
        for embed in msg.embeds:
            lines.append(f"    [Embed] {embed.title or ''}\n")
            if embed.description:
                lines.append(f"      {embed.description}\n")
            for field in embed.fields:
                lines.append(f"      {field.name}: {field.value}\n")

        if msg.attachments:
            lines.append(f"    [Attachments: {', '.join(a.url for a in msg.attachments)}]\n")

    return "".join(lines).encode("utf-8"), f"{channel.name}-transcript.txt"


def make_file(data, filename):
    """A fresh File object each time (a File can't safely be sent twice)."""
    return discord.File(io.BytesIO(data), filename=filename)


async def find_open_application(guild, user_id):
    """
    Returns the user's open application channel IN THIS SERVER, or None.
    Records pointing at channels that no longer exist are cleaned up.
    """
    found = None
    dead = []

    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT channel_id FROM applications WHERE guild_id = ? AND user_id = ?",
            (guild.id, user_id),
        )
        for (channel_id,) in await cursor.fetchall():
            channel = guild.get_channel(channel_id)
            if channel is None:
                dead.append(channel_id)
            elif found is None:
                found = channel

        for channel_id in dead:
            await db.execute("DELETE FROM applications WHERE channel_id = ?", (channel_id,))
        if dead:
            await db.commit()

    return found


async def close_ticket_sequence(interaction: discord.Interaction, status: str):
    """Shared function to handle closing, transcribing, and deleting."""
    channel = interaction.channel
    guild = interaction.guild

    # 1. Notify we are working on it
    if not interaction.response.is_done():
        await interaction.response.send_message("Processing request...", ephemeral=True)

    # 2. Find the applicant
    user_id = None
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT user_id FROM applications WHERE channel_id = ?", (channel.id,))
        result = await cursor.fetchone()
        if result:
            user_id = result[0]

    # 3. Generate the transcript
    data, filename = await build_transcript(channel)

    # 4. Send it to the log channel
    log_channel = await get_log_channel(guild)
    if log_channel:
        try:
            await log_channel.send(f"Transcript for {status} application: {channel.name}", file=make_file(data, filename))
        except discord.HTTPException:
            await interaction.followup.send("⚠️ I couldn't post the transcript in the log channel. Check my permissions there.", ephemeral=True)
    else:
        await interaction.followup.send("⚠️ No application log channel is set, so no transcript was saved. Use `/setup` to choose one.", ephemeral=True)

    # 5. Send it to the applicant (DM)
    if user_id:
        user = guild.get_member(user_id)
        if user:
            try:
                await user.send(f"Your application was {status}. Here is the transcript.", file=make_file(data, filename))
            except discord.HTTPException:
                pass  # User has DMs off

    # 6. Delete the channel, then forget the record (so they can apply again)
    try:
        await channel.delete()
    except discord.HTTPException:
        await interaction.followup.send("⚠️ I couldn't delete this channel. Please delete it manually.", ephemeral=True)
        return

    async with get_db_connection() as db:
        await db.execute("DELETE FROM applications WHERE channel_id = ?", (channel.id,))
        await db.commit()


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
        age_text = self.age.value.strip()
        if not age_text.isdigit() or not 1 <= int(age_text) <= 120:
            await interaction.response.send_message("❌ Please enter your age as a number (e.g. 18) and submit the form again.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        user = interaction.user
        key = (guild.id, user.id)

        if key in _creating:
            await interaction.followup.send("⏳ Your application is already being created.", ephemeral=True)
            return
        _creating.add(key)

        try:
            # The check in /apply happened before the form was filled in, so check again.
            existing = await find_open_application(guild, user.id)
            if existing:
                await interaction.followup.send(f"You already have an open application here: {existing.mention}", ephemeral=True)
                return

            # --- PERMISSION SETUP ---
            mod_role = await get_staff_role(guild)

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
            except discord.HTTPException:
                await interaction.followup.send("❌ I couldn't create your application channel. Please tell an admin to check that I have the **Manage Channels** permission.", ephemeral=True)
                return

            # Save to DB
            async with get_db_connection() as db:
                await db.execute(
                    "INSERT INTO applications (channel_id, guild_id, user_id, app_type, status) VALUES (?, ?, ?, ?, ?)",
                    (app_channel.id, guild.id, user.id, "Staff", "Open")
                )
                await db.commit()
        finally:
            _creating.discard(key)

        # Build Embed
        embed = discord.Embed(title=f"Application: {user.name}", color=discord.Color.blue())
        embed.add_field(name="Name", value=self.name.value, inline=True)
        embed.add_field(name="Age", value=age_text, inline=True)
        embed.add_field(name="Experience", value=self.experience.value if self.experience.value else "None", inline=False)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.set_footer(text=f"User ID: {user.id}")

        # Send to channel with Persistent View
        view = AdminAppView()
        ping_msg = mod_role.mention if mod_role else "Admins"
        await app_channel.send(
            content=f"{ping_msg}, a new application has arrived.",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        await app_channel.send(
            f"{user.mention} Welcome. Please wait for the staff to review your application.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await interaction.followup.send(f"✅ Application received! Your private channel: {app_channel.mention}", ephemeral=True)


# --- 2. ADMIN CONTROL PANEL (PERSISTENT) ---
class AdminAppView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent

    async def _allowed(self, interaction: discord.Interaction):
        if await is_staff(interaction):
            return True
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return False

    @ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="app_accept")
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._allowed(interaction):
            return

        # Accepting means there is nothing left to reject.
        button.disabled = True
        for child in self.children:
            if getattr(child, "custom_id", None) == "app_reject":
                child.disabled = True

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Accepted by {interaction.user.name}")

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT user_id FROM applications WHERE channel_id = ?", (interaction.channel.id,))
            row = await cursor.fetchone()
            await db.execute("UPDATE applications SET status = 'Accepted' WHERE channel_id = ?", (interaction.channel.id,))
            await db.commit()

        await interaction.response.edit_message(embed=embed, view=self)

        applicant = f"<@{row[0]}> " if row else ""
        await interaction.channel.send(
            f"Congratulations {applicant}! Your application was accepted by {interaction.user.mention}.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @ui.button(label="Reject", style=discord.ButtonStyle.red, custom_id="app_reject")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._allowed(interaction):
            return

        await interaction.channel.send("Application Rejected. Closing ticket...")
        await close_ticket_sequence(interaction, "REJECTED")

    @ui.button(label="Close", style=discord.ButtonStyle.grey, emoji="🔒", custom_id="app_close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._allowed(interaction):
            return

        await interaction.response.send_message("Closing ticket...")
        await close_ticket_sequence(interaction, "CLOSED")


# --- 3. TRIGGER VIEW (For Text Command) ---
class StartAppView(ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=60) 
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This button is for the person who ran the command. Use `/apply` to start your own application.", ephemeral=True)
            return False
        return True

    @ui.button(label="Open Application Form", style=discord.ButtonStyle.blurple, emoji="📝")
    async def open_form(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ApplicationModal())


# --- 4. THE MAIN COG ---
class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Re-attach the Accept/Reject/Close buttons of tickets that were open before a restart.
        self.bot.add_view(AdminAppView())

    @commands.hybrid_command(name="apply", description="Start a staff application process.")
    @commands.guild_only()
    async def apply(self, ctx):
        # 1. Check for an existing application in THIS server
        existing = await find_open_application(ctx.guild, ctx.author.id)
        if existing:
            await ctx.send(f"You already have an open application here: {existing.mention}", ephemeral=True)
            return

        # 2. Handle Input
        if ctx.interaction:
            await ctx.interaction.response.send_modal(ApplicationModal())
        else:
            await ctx.send("To start your application, please click the button below:", view=StartAppView(ctx.author.id))

async def setup(bot):
    await bot.add_cog(Applications(bot))
