import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import aiohttp
import xml.etree.ElementTree as ET
from aiohttp import web
from database import get_db_connection
import asyncio  # <--- NEW: Added for the delay timer

# --- CONFIGURATION ---
# IMPORTANT: This must be accessible from the internet!
CALLBACK_URL = "https://unscreenable-larry-fumblingly.ngrok-free.dev/callback" 
PORT = 8080 

class Socials(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        
        # Web Server Setup
        self.app = web.Application()
        self.app.router.add_get('/callback', self.web_handle_challenge)
        self.app.router.add_post('/callback', self.web_handle_notification)
        self.runner = None
        self.site = None
        
        # Start the web server
        self.bot.loop.create_task(self.start_web_server())
        
        # Start DB Migration Check
        self.bot.loop.create_task(self.check_db_schema())
        
        # Start the re-subscription loop
        self.resubscribe_loop.start()

    async def cog_unload(self):
        self.resubscribe_loop.cancel()
        if self.runner:
            await self.runner.cleanup()
        await self.session.close()

    async def check_db_schema(self):
        """Checks if the database has the 'target_name' column and adds it if missing."""
        await self.bot.wait_until_ready()
        async with get_db_connection() as db:
            cursor = await db.execute("PRAGMA table_info(social_subs)")
            columns = [row[1] for row in await cursor.fetchall()]
            
            if "target_name" not in columns:
                print("[Database] Migrating social_subs table: Adding 'target_name' column...")
                await db.execute("ALTER TABLE social_subs ADD COLUMN target_name TEXT DEFAULT 'Unknown'")
                await db.commit()
                print("[Database] Migration complete.")

    async def fetch_channel_name(self, channel_id):
        """Fetches the YouTube channel name using the public RSS feed."""
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    root = ET.fromstring(text)
                    ns = {'atom': 'http://www.w3.org/2005/Atom'}
                    title_element = root.find('atom:title', ns)
                    if title_element is not None:
                        return title_element.text
                return None
        except Exception as e:
            print(f"[YouTube] Error fetching name for {channel_id}: {e}")
            return None

    # --- WEB SERVER HANDLERS ---

    async def start_web_server(self):
        """Starts the aiohttp web server to listen for YouTube pings."""
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, '0.0.0.0', PORT)
            await self.site.start()
            print(f"[WebSub] Listening on port {PORT}...")
        except Exception as e:
            print(f"[WebSub] Failed to start web server: {e}")

    async def web_handle_challenge(self, request):
        hub_challenge = request.query.get('hub.challenge')
        if hub_challenge:
            print(f"[WebSub] Verifying challenge: {hub_challenge}")
            return web.Response(text=hub_challenge)
        return web.Response(status=400)

    async def web_handle_notification(self, request):
        try:
            text = await request.text()
            root = ET.fromstring(text)
            
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'yt': 'http://www.youtube.com/xml/schemas/2015'
            }

            entry = root.find('atom:entry', ns)
            if entry is None:
                return web.Response(status=200)

            def get_text(element, tag_name, namespace_map):
                found = element.find(tag_name, namespace_map)
                return found.text if found is not None else None

            video_id = get_text(entry, 'yt:videoId', ns)
            channel_id = get_text(entry, 'yt:channelId', ns)
            title = get_text(entry, 'atom:title', ns)
            author = get_text(entry, 'atom:author/atom:name', ns)
            
            # Manually construct link to guarantee valid Discord preview
            link = f"https://www.youtube.com/watch?v={video_id}"

            if not video_id or not channel_id:
                print(f"[WebSub] Ignored incomplete notification.")
                return web.Response(status=200)

            print(f"[WebSub] Notification: {author} - {title}")

            async with get_db_connection() as db:
                 await db.execute("UPDATE social_subs SET target_name = ? WHERE target_id = ?", (author, channel_id))
                 await db.commit()

            await self.process_notification(channel_id, video_id, title, author, link)
            
            return web.Response(status=200)

        except Exception as e:
            print(f"[WebSub] Parsing Critical Error: {e}")
            return web.Response(status=200)

    # --- LOGIC ---

    async def process_notification(self, channel_id, video_id, title, author, link):
        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT id, discord_channel_id, last_id, custom_message FROM social_subs WHERE target_id = ? AND platform = 'youtube'",
                (channel_id,)
            )
            subs = await cursor.fetchall()

            if not subs:
                return
            
            # --- NEW: THUMBNAIL DELAY ---
            # Wait 5 seconds to ensure YouTube has finished processing the image
            # so Discord doesn't cache a broken "No Image" preview.
            await asyncio.sleep(5) 
            # ----------------------------

            for sub in subs:
                db_id, discord_channel_id, last_id, msg_template = sub

                if str(last_id) == str(video_id):
                    print(f"[WebSub] Duplicate ignored: {video_id}")
                    continue

                await db.execute("UPDATE social_subs SET last_id = ? WHERE id = ?", (video_id, db_id))
                
                try:
                    target_channel_id = int(discord_channel_id)
                    channel = self.bot.get_channel(target_channel_id)
                    
                    if channel is None:
                        try:
                            channel = await self.bot.fetch_channel(target_channel_id)
                        except (discord.NotFound, discord.Forbidden):
                            continue

                    if channel:
                        content = msg_template.replace("{link}", link).replace("{author}", author).replace("{title}", title)
                        await channel.send(content)

                except Exception as e:
                    print(f"[WebSub] Send Error: {e}")
            
            await db.commit()

    async def subscribe_to_hub(self, channel_id, mode='subscribe'):
        topic_url = f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
        hub_url = "https://pubsubhubbub.appspot.com/subscribe"
        
        data = {
            'hub.callback': CALLBACK_URL,
            'hub.mode': mode,
            'hub.topic': topic_url,
            'hub.verify': 'sync' 
        }

        async with self.session.post(hub_url, data=data) as resp:
            if resp.status == 204 or resp.status == 202:
                print(f"[WebSub] {mode} OK: {channel_id}")
                return True
            else:
                return False

    # --- COMMANDS ---

    @commands.hybrid_group(name="youtube", description="Manage YouTube notification subscriptions.")
    async def youtube(self, ctx):
        pass

    @youtube.command(name="add", description="Add a YouTube channel to receive notifications.")
    @app_commands.describe(
        channel_id="The YouTube Channel ID (Must start with 'UC', e.g., UCkJ1...)",
        discord_channel="The text channel where notification messages will be posted",
        message="Optional custom message. Variables: {author}, {title}, {link}"
    )
    @commands.has_permissions(manage_webhooks=True)
    async def yt_add(self, ctx, channel_id: str, discord_channel: discord.TextChannel, message: str = "Hey! **{author}** uploaded: {link}"):
        await ctx.defer()

        if not channel_id.startswith("UC"):
            await ctx.send("❌ IDs must start with `UC`.")
            return

        channel_name = await self.fetch_channel_name(channel_id)
        if not channel_name:
            channel_name = "Unknown Channel"

        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT * FROM social_subs WHERE guild_id = ? AND platform = 'youtube' AND target_id = ?",
                (ctx.guild.id, channel_id)
            )
            if await cursor.fetchone():
                await ctx.send(f"⚠️ Already subscribed to **{channel_name}** ({channel_id}).")
                return

            await db.execute("""
                INSERT INTO social_subs (guild_id, discord_channel_id, platform, target_id, last_id, custom_message, target_name)
                VALUES (?, ?, 'youtube', ?, '0', ?, ?)
            """, (ctx.guild.id, discord_channel.id, channel_id, message, channel_name))
            await db.commit()

        success = await self.subscribe_to_hub(channel_id)
        
        if success:
            await ctx.send(f"✅ Subscribed to **{channel_name}**!")
        else:
            await ctx.send(f"⚠️ Saved **{channel_name}**, but WebSub connection failed. Check console.")

    @youtube.command(name="remove", description="Remove a YouTube channel subscription.")
    @app_commands.describe(
        channel_id="The YouTube Channel ID to unsubscribe from (e.g., UCkJ1...)"
    )
    @commands.has_permissions(manage_webhooks=True)
    async def yt_remove(self, ctx, channel_id: str):
        await ctx.defer()
        await self.subscribe_to_hub(channel_id, mode='unsubscribe')

        async with get_db_connection() as db:
            cursor = await db.execute("SELECT target_name FROM social_subs WHERE guild_id = ? AND target_id = ?", (ctx.guild.id, channel_id))
            data = await cursor.fetchone()
            name = data[0] if data else channel_id

            await db.execute(
                "DELETE FROM social_subs WHERE guild_id = ? AND platform = 'youtube' AND target_id = ?",
                (ctx.guild.id, channel_id)
            )
            await db.commit()
        await ctx.send(f"🗑️ Unsubscribed from **{name}**.")

    @youtube.command(name="list", description="Display all active YouTube subscriptions.")
    async def yt_list(self, ctx):
        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT target_name, target_id, discord_channel_id FROM social_subs WHERE guild_id = ? AND platform = 'youtube'",
                (ctx.guild.id,)
            )
            rows = await cursor.fetchall()
        
        if not rows:
            await ctx.send("📭 No subscriptions.")
            return

        text = "\n".join([f"• **{row[0]}** (`{row[1]}`) → <#{row[2]}>" for row in rows])
        embed = discord.Embed(title="YouTube Subscriptions", description=text, color=discord.Color.red())
        await ctx.send(embed=embed)

    # --- TASKS ---

    @tasks.loop(hours=24) 
    async def resubscribe_loop(self):
        await self.bot.wait_until_ready()
        print("[WebSub] Refreshing subscriptions...")
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT DISTINCT target_id FROM social_subs WHERE platform = 'youtube'")
            rows = await cursor.fetchall()
            for row in rows:
                await self.subscribe_to_hub(row[0])

    @resubscribe_loop.before_loop
    async def before_resub(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Socials(bot))
