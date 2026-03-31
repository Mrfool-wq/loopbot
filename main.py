import discord
import os
from discord.ext import commands
from dotenv import load_dotenv
from database import init_db, get_db_connection

# 1. Load Token
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# 2. Setup Permissions
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True 

# 3. Dynamic Prefix System
async def get_prefix(bot, message):
    if not message.guild:
        return "!" 

    # Check the BOT'S specific cache variable
    if message.guild.id in bot.prefix_cache:
        return bot.prefix_cache[message.guild.id]

    # If not in RAM, check Database
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (message.guild.id,))
        data = await cursor.fetchone()
    
    # Update Cache
    if data:
        prefix = data[0]
        bot.prefix_cache[message.guild.id] = prefix
        return prefix
    else:
        bot.prefix_cache[message.guild.id] = "!" 
        return "!"

# 4. Custom Bot Class
class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix_cache = {}  # <--- Initialize cache HERE attached to self

    async def setup_hook(self):
        await init_db()
        
        # Load Cogs
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                await self.load_extension(f'cogs.{filename[:-3]}')
                print(f"⚙️  Loaded extension: {filename}")

bot = MyBot(command_prefix=get_prefix, intents=intents, help_command=None)

# 5. Startup Event
@bot.event
async def on_ready():
    print(f"🚀 {bot.user} is online!")
    print(f"🆔 ID: {bot.user.id}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Auto-Synced {len(synced)} commands globally.")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")
    print("------")

# 6. Sync Command
@bot.command(hidden=True)
@commands.is_owner()
async def sync(ctx, target: str = None):
    if target == ".":
        msg = await ctx.send("🔄 Syncing locally...")
        synced = await bot.tree.sync(guild=ctx.guild)
        await msg.edit(content=f"✅ Synced {len(synced)} commands locally!")
    else:
        msg = await ctx.send("🌐 Syncing globally...")
        synced = await bot.tree.sync()
        await msg.edit(content=f"✅ Synced {len(synced)} commands globally!")

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN not found.")
