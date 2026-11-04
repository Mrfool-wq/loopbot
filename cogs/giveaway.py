import random
import re
import time

import discord
from discord.ext import commands, tasks

from database import get_db_connection

# --- CONFIGURATION (change emojis / limits here) ---
GIFT_EMOJI = "<a:gift_cool:1458662163190255708>"
GIVEAWAY_EMOJI = "<a:giveaway:1461382837562507356>"   # the emoji people react with
NO_ENTRIES_EMOJI = "<a:profile_01:1458763510098952339>"
FALLBACK_EMOJI = "🎉"  # used automatically if the bot can't use GIVEAWAY_EMOJI

MIN_SECONDS = 10
MAX_SECONDS = 30 * 86400  # 30 days
MAX_WINNERS = 20
MAX_PRIZE_LENGTH = 200

DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MENTION_RE = re.compile(r"<@!?(\d+)>")

# Prizes are typed by users, so never let them ping @everyone / roles through the bot.
WINNER_MENTIONS = discord.AllowedMentions(users=True, everyone=False, roles=False)
NO_MENTIONS = discord.AllowedMentions.none()


def parse_duration(text):
    """Converts '10s', '5m', '2h', '1d' into seconds. Returns None if invalid."""
    match = DURATION_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    return int(match.group(1)) * UNIT_SECONDS[match.group(2)]


class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------
    async def resolve_channel(self, channel_id):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden):
            return None

    @staticmethod
    def find_entry_reaction(msg):
        """Finds the entry reaction, whether it's the custom emoji or the fallback."""
        for reaction in msg.reactions:
            if str(reaction.emoji) in (GIVEAWAY_EMOJI, FALLBACK_EMOJI):
                return reaction
        return None

    @staticmethod
    async def collect_entrants(guild, reaction):
        """Everyone who reacted, excluding bots and people who left the server."""
        users = []
        async for user in reaction.users():
            if user.bot:
                continue
            if guild is not None and guild.get_member(user.id) is None:
                continue
            users.append(user)
        return users

    async def claim_giveaway(self, message_id):
        """Removes the giveaway from the DB. Only ONE caller can win this, which stops double endings."""
        async with get_db_connection() as db:
            cursor = await db.execute("DELETE FROM giveaways WHERE message_id = ?", (message_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def end_giveaway_logic(self, data):
        """Picks winners, announces them and updates the giveaway message."""
        msg_id, channel_id, guild_id, prize, winners_count, _ = data

        channel = await self.resolve_channel(channel_id)
        if channel is None:
            return  # channel deleted

        try:
            msg = await channel.fetch_message(msg_id)
        except discord.NotFound:
            return  # message deleted

        reaction = self.find_entry_reaction(msg)
        if reaction is None:
            await channel.send(f"⚠️ Error: the entry reaction is missing on the giveaway for **{prize}**.", allowed_mentions=NO_MENTIONS)
            return

        users = await self.collect_entrants(channel.guild, reaction)

        embed = msg.embeds[0] if msg.embeds else discord.Embed(title="GIVEAWAY")
        embed.color = discord.Color.dark_grey()
        embed.clear_fields()

        if not users:
            await channel.send(f"{NO_ENTRIES_EMOJI} No one entered the giveaway for **{prize}**.", allowed_mentions=NO_MENTIONS)
            embed.description = f"**Prize:** {prize}\n**Winner(s):** None\n**Ended**"
        else:
            winners = random.sample(users, min(winners_count, len(users)))
            winner_mentions = ", ".join(w.mention for w in winners)

            await channel.send(
                f"{GIVEAWAY_EMOJI} **CONGRATULATIONS!** {GIVEAWAY_EMOJI}\n{winner_mentions}\nYou won **{prize}**!",
                allowed_mentions=WINNER_MENTIONS,
            )
            embed.description = f"**Prize:** {prize}\n**Winner(s):** {winner_mentions}\n**Ended**"

        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------
    # COMMANDS
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="gstart", description="Start a giveaway.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        duration="How long should it last? (e.g., 30s, 10m, 1h, 1d)",
        winners="How many winners will be picked?",
        prize="What is the prize being given away?"
    )
    async def gstart(self, ctx, duration: str, winners: int, *, prize: str):
        """
        Usage: !gstart 10m 1 Nitro Classic
        Duration: s (seconds), m (minutes), h (hours), d (days)
        """
        await ctx.defer()

        seconds = parse_duration(duration)
        if seconds is None:
            return await ctx.send("❌ Invalid duration. Use formats like `10s`, `1m`, `2h`, `1d`.")
        if not MIN_SECONDS <= seconds <= MAX_SECONDS:
            return await ctx.send("❌ Duration must be between **10 seconds** and **30 days**.")
        if not 1 <= winners <= MAX_WINNERS:
            return await ctx.send(f"❌ Winners must be between **1** and **{MAX_WINNERS}**.")
        if len(prize) > MAX_PRIZE_LENGTH:
            return await ctx.send(f"❌ Prize is too long (max {MAX_PRIZE_LENGTH} characters).")

        perms = ctx.channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links and perms.add_reactions and perms.read_message_history):
            return await ctx.send("❌ In this channel I need: Send Messages, Embed Links, Add Reactions and Read Message History.")

        end_time = time.time() + seconds

        embed = discord.Embed(
            title=f"{GIFT_EMOJI} GIVEAWAY {GIFT_EMOJI}",
            description=f"**Prize:** {prize}\n**Winners:** {winners}\n**React with {GIVEAWAY_EMOJI} to enter!**",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Ends In", value=f"<t:{int(end_time)}:R>")
        embed.set_footer(text=f"Hosted by {ctx.author.display_name}")

        msg = await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

        try:
            await msg.add_reaction(GIVEAWAY_EMOJI)
        except discord.HTTPException:
            # The bot can't use the custom emoji (e.g. it isn't in that emoji's server): fall back.
            await msg.add_reaction(FALLBACK_EMOJI)
            embed.description = embed.description.replace(GIVEAWAY_EMOJI, FALLBACK_EMOJI)
            await msg.edit(embed=embed)

        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO giveaways (message_id, channel_id, guild_id, prize, winners_count, end_time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (msg.id, ctx.channel.id, ctx.guild.id, prize, winners, end_time),
            )
            await db.commit()

    @commands.hybrid_command(name="gend", description="End a giveaway immediately.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        message_id="The ID of the giveaway message you want to end."
    )
    async def gend(self, ctx, message_id: str):
        await ctx.defer()
        try:
            msg_id = int(message_id)
        except ValueError:
            return await ctx.send("❌ Invalid Message ID.")

        # Only giveaways that belong to THIS server can be ended here.
        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT message_id, channel_id, guild_id, prize, winners_count, end_time "
                "FROM giveaways WHERE message_id = ? AND guild_id = ?",
                (msg_id, ctx.guild.id),
            )
            data = await cursor.fetchone()

        if not data:
            return await ctx.send("❌ No active giveaway with that ID in this server.")

        if not await self.claim_giveaway(msg_id):
            return await ctx.send("⚠️ That giveaway was just ended.")

        try:
            await self.end_giveaway_logic(data)
        except Exception as e:
            print(f"[Giveaway] gend failed for {msg_id}: {e}")
            return await ctx.send("⚠️ The giveaway was closed, but I couldn't post the results. Check my permissions.")

        await ctx.send("✅ Giveaway ended.", ephemeral=True)

    @commands.hybrid_command(name="greroll", description="Reroll a giveaway winner.")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(
        channel="The channel where the giveaway is located.",
        message_id="The ID of the giveaway message to reroll."
    )
    async def greroll(self, ctx, channel: discord.TextChannel, message_id: str):
        await ctx.defer()

        try:
            msg_id = int(message_id)
        except ValueError:
            return await ctx.send("❌ Invalid Message ID.")

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT 1 FROM giveaways WHERE message_id = ?", (msg_id,))
            still_running = await cursor.fetchone()
        if still_running:
            return await ctx.send("⚠️ That giveaway is still running. Use `gend` to end it first.")

        try:
            msg = await channel.fetch_message(msg_id)
        except discord.HTTPException:
            return await ctx.send("❌ Couldn't find that message.")

        if msg.author.id != self.bot.user.id or not msg.embeds or "GIVEAWAY" not in (msg.embeds[0].title or ""):
            return await ctx.send("❌ That message isn't a giveaway created by me.")

        reaction = self.find_entry_reaction(msg)
        if reaction is None:
            return await ctx.send("❌ The entry reaction is missing on that giveaway.")

        # Don't re-pick people who already won (their mentions are in the ended embed).
        previous_winners = {int(uid) for uid in MENTION_RE.findall(msg.embeds[0].description or "")}

        users = [
            u for u in await self.collect_entrants(ctx.guild, reaction)
            if u.id not in previous_winners
        ]
        if not users:
            return await ctx.send("❌ No other valid entries to reroll.")

        winner = random.choice(users)
        await ctx.send(f"{GIVEAWAY_EMOJI} The new winner is {winner.mention}!", allowed_mentions=WINNER_MENTIONS)

    # ------------------------------------------------------------------
    # BACKGROUND TASK
    # ------------------------------------------------------------------
    @tasks.loop(seconds=15)
    async def check_giveaways(self):
        """Ends giveaways whose time is up. Errors are caught so the loop never dies."""
        try:
            async with get_db_connection() as db:
                cursor = await db.execute(
                    "SELECT message_id, channel_id, guild_id, prize, winners_count, end_time "
                    "FROM giveaways WHERE end_time <= ?",
                    (time.time(),),
                )
                due = await cursor.fetchall()
        except Exception as e:
            print(f"[Giveaway] Could not read giveaways: {e}")
            return

        for data in due:
            try:
                if await self.claim_giveaway(data[0]):
                    await self.end_giveaway_logic(data)
            except Exception as e:
                print(f"[Giveaway] Failed to end giveaway {data[0]}: {e}")

    @check_giveaways.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
