import discord
from discord.ext import commands
import random

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="mimic", description="Impersonate another user via webhook.")
    @commands.has_permissions(manage_webhooks=True)
    async def mimic(self, ctx, member: discord.Member, *, text: str):
        if ctx.interaction:
            await ctx.send("🎭 Mimicking...", ephemeral=True)
        else:
            await ctx.message.delete()
            
        webhook = await ctx.channel.create_webhook(name=member.display_name)
        await webhook.send(str(text), username=member.display_name, avatar_url=member.display_avatar.url)
        await webhook.delete()

    @commands.hybrid_command(name="poll", description="Create a Yes/No poll.")
    async def poll(self, ctx, *, question: str):
        if not ctx.interaction:
            await ctx.message.delete()
        embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.gold())
        embed.set_footer(text=f"Asked by {ctx.author.display_name}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")

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
