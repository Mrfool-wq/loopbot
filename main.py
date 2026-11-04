import discord
import os
import sys
import traceback
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
        base_prefix = "!" 
    else:
        # Check the BOT'S specific cache variable
        if message.guild.id in bot.prefix_cache:
            base_prefix = bot.prefix_cache[message.guild.id]
        else:
            # If not in RAM, check Database
            async with get_db_connection() as db:
                cursor = await db.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (message.guild.id,))
                data = await cursor.fetchone()
            
            # Update Cache
            if data:
                base_prefix = data[0]
                bot.prefix_cache[message.guild.id] = base_prefix
            else:
                bot.prefix_cache[message.guild.id] = "!" 
                base_prefix = "!"

    # --- NO PREFIX CHECK ---
    # If the user is in the no-prefix cache, allow "" (nothing) OR the base prefix
    if hasattr(bot, 'noprefix_cache') and message.author.id in bot.noprefix_cache:
        return ["", base_prefix]
    
    return base_prefix

# 4. Custom Bot Class
class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix_cache = {}  
        self.noprefix_cache = set()
        self.commands_synced = False  # makes sure we only auto-sync once per run

    async def setup_hook(self):
        await init_db()

        # Load No-Prefix users into RAM Cache on startup
        async with get_db_connection() as db:
            # We use a try/except here just in case the table hasn't been created on the very first run
            try:
                cursor = await db.execute("SELECT user_id FROM noprefix_users")
                rows = await cursor.fetchall()
                for row in rows:
                    self.noprefix_cache.add(row[0])
            except Exception as e:
                print(f"⚠️ Could not load noprefix cache (table might be empty or missing): {e}")
        
        # Load Cogs (one broken cog no longer stops the whole bot from starting)
        loaded, failed = 0, []
        for filename in sorted(os.listdir('./cogs')):
            if not filename.endswith('.py'):
                continue
            try:
                await self.load_extension(f'cogs.{filename[:-3]}')
                loaded += 1
                print(f"⚙️  Loaded extension: {filename}")
            except Exception:
                failed.append(filename)
                print(f"❌ Failed to load extension: {filename}", file=sys.stderr)
                traceback.print_exc()

        print(f"📦 Cogs loaded: {loaded}" + (f" | Failed: {', '.join(failed)}" if failed else ""))

bot = MyBot(command_prefix=get_prefix, intents=intents, help_command=None)

# 5. Startup Event
@bot.event
async def on_ready():
    print(f"🚀 {bot.user} is online!")
    print(f"🆔 ID: {bot.user.id}")

    # on_ready can fire again after a reconnect, so only sync the first time.
    if not bot.commands_synced:
        try:
            synced = await bot.tree.sync()
            bot.commands_synced = True
            print(f"✅ Auto-Synced {len(synced)} commands globally.")
        except Exception as e:
            print(f"❌ Error syncing commands: {e}")
    print("------")

# 6. Sync Command
@bot.command(hidden=True)
@commands.is_owner()
async def sync(ctx, target: str = None):
    try:
        if target == ".":
            msg = await ctx.send("🔄 Syncing locally...")
            synced = await bot.tree.sync(guild=ctx.guild)
            await msg.edit(content=f"✅ Synced {len(synced)} commands locally!")
        else:
            msg = await ctx.send("🌐 Syncing globally...")
            synced = await bot.tree.sync()
            await msg.edit(content=f"✅ Synced {len(synced)} commands globally!")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Sync failed: {e}")

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN not found.")
