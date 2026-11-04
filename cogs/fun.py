import discord
from discord.ext import commands
import random

MIMIC_WEBHOOK_NAME = "Mimic"
MAX_MIMIC_LENGTH = 2000
MAX_POLL_LENGTH = 1000

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.webhook_cache = {}  # channel_id -> webhook (so we don't create one on every use)

    async def get_mimic_webhook(self, channel):
        """Reuses this bot's webhook in the channel, creating it only if needed."""
        cached = self.webhook_cache.get(channel.id)
        if cached:
            return cached

        for webhook in await channel.webhooks():
            # webhook.token is only present on webhooks the bot itself can send with
            if webhook.name == MIMIC_WEBHOOK_NAME and webhook.token and webhook.user == self.bot.user:
                self.webhook_cache[channel.id] = webhook
                return webhook

        webhook = await channel.create_webhook(name=MIMIC_WEBHOOK_NAME)
        self.webhook_cache[channel.id] = webhook
        return webhook

    @commands.hybrid_command(name="mimic", description="Impersonate another user via webhook.")
    @commands.guild_only()
    @commands.has_permissions(manage_webhooks=True)
    @commands.bot_has_permissions(manage_webhooks=True)
    async def mimic(self, ctx, member: discord.Member, *, text: str):
        if len(text) > MAX_MIMIC_LENGTH:
            return await ctx.send(f"❌ That message is too long (max {MAX_MIMIC_LENGTH} characters).", ephemeral=True)

        await ctx.defer(ephemeral=True)

        # Webhooks live on the parent channel; threads are targeted with thread=...
        thread = ctx.channel if isinstance(ctx.channel, discord.Thread) else None
        channel = ctx.channel.parent if thread else ctx.channel
        if channel is None or not hasattr(channel, "create_webhook"):
            return await ctx.send("❌ I can't use webhooks in this kind of channel.", ephemeral=True)

        kwargs = {"thread": thread} if thread else {}
        send_args = dict(
            content=text,
            username=member.display_name,
            avatar_url=member.display_avatar.url,
            # The impersonated text must never be able to ping @everyone, roles or users.
            allowed_mentions=discord.AllowedMentions.none(),
            **kwargs,
        )

        try:
            webhook = await self.get_mimic_webhook(channel)
            try:
                await webhook.send(**send_args)
            except discord.NotFound:
                # The webhook was deleted by someone: forget it, make a new one, retry once.
                self.webhook_cache.pop(channel.id, None)
                webhook = await self.get_mimic_webhook(channel)
                await webhook.send(**send_args)
        except discord.Forbidden:
            return await ctx.send("❌ I don't have permission to manage webhooks here.", ephemeral=True)
        except discord.HTTPException as e:
            return await ctx.send(f"❌ Couldn't send the message: {e}", ephemeral=True)

        # Only remove the command message AFTER it worked
        if ctx.interaction:
            await ctx.send("🎭 Done!", ephemeral=True)
        else:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        # Someone changed the webhooks in this channel: drop our cached copy to be safe.
        self.webhook_cache.pop(channel.id, None)

    @commands.hybrid_command(name="poll", description="Create a Yes/No poll.")
    async def poll(self, ctx, *, question: str):
        if len(question) > MAX_POLL_LENGTH:
            return await ctx.send(f"❌ Keep the question under {MAX_POLL_LENGTH} characters.", ephemeral=True)

        embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.gold())
        embed.set_footer(text=f"Asked by {ctx.author.display_name}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")

        if not ctx.interaction:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    @commands.hybrid_command(name="topic", description="Get a random chat topic.")
    async def topic(self, ctx):
        topics = [
            "What's a food combination that sounds gross but tastes amazing?",
            "If animals could talk, which one would be the rudest?",
            "What's the best Wi-Fi name you've seen?",
            "Is a hotdog a sandwich?",
        ]
        await ctx.send(f"🗣️ **Topic:** {random.choice(topics)}")

    @commands.hybrid_command(name="coinflip", description="Flip a coin.")
    async def coinflip(self, ctx):
        result = random.choice(["HEADS", "TAILS"])
        await ctx.send(f"🪙 It's **{result}**!")

    @commands.hybrid_command(name="avatar", description="Enlarge a user's avatar.")
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=f"{member.name}'s Avatar", color=discord.Color.teal())
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Fun(bot))
