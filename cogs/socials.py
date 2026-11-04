import asyncio
import hashlib
import hmac
import os
import re
import xml.etree.ElementTree as ET

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks

from database import get_db_connection

# --- CONFIGURATION (set these in your .env file) ---
# WEBSUB_CALLBACK_URL = public https URL ending in /callback (e.g. your ngrok/domain URL)
# WEBSUB_PORT         = local port the web server listens on (default 8080)
# WEBSUB_SECRET       = optional; if missing, one is derived from DISCORD_TOKEN
TOPIC_BASE = "https://www.youtube.com/xml/feeds/videos.xml?channel_id="
HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")
VIDEO_ID_RE = re.compile(r"^[\w-]{11}$")
SAFE_MENTIONS = discord.AllowedMentions(everyone=False, users=False, roles=True)


def get_secret():
    """Shared secret used by YouTube's hub to sign notifications."""
    secret = os.getenv("WEBSUB_SECRET")
    if secret:
        return secret
    token = os.getenv("DISCORD_TOKEN")
    if token:
        return hmac.new(token.encode(), b"loopbot-websub", hashlib.sha256).hexdigest()
    return None


class Socials(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.callback_url = os.getenv("WEBSUB_CALLBACK_URL")
        self.port = int(os.getenv("WEBSUB_PORT", "8080"))
        self.secret = get_secret()
        self.session = None
        self.runner = None
        self._tasks = set()

        self.app = web.Application()
        self.app.router.add_get("/callback", self.web_handle_challenge)
        self.app.router.add_post("/callback", self.web_handle_notification)

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        await self.check_db_schema()

        if not self.callback_url:
            print("[WebSub] WEBSUB_CALLBACK_URL is not set in .env - YouTube notifications are disabled.")
            return
        if not self.secret:
            print("[WebSub] No secret available (set WEBSUB_SECRET or DISCORD_TOKEN) - YouTube notifications are disabled.")
            return

        await self.start_web_server()
        self.resubscribe_loop.start()

    async def cog_unload(self):
        self.resubscribe_loop.cancel()
        for task in list(self._tasks):
            task.cancel()
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

    async def check_db_schema(self):
        """Adds the 'target_name' column if it is missing."""
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
                    root = ET.fromstring(await resp.text())
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    title_element = root.find("atom:title", ns)
                    if title_element is not None:
                        return title_element.text
        except Exception as e:
            print(f"[YouTube] Error fetching name for {channel_id}: {e}")
        return None

    # --- WEB SERVER ---

    async def start_web_server(self):
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "0.0.0.0", self.port)
            await site.start()
            print(f"[WebSub] Listening on port {self.port}...")
        except Exception as e:
            print(f"[WebSub] Failed to start web server: {e}")

    def verify_signature(self, body, header):
        """Checks the X-Hub-Signature header (e.g. 'sha1=abc...') against our secret."""
        if not header or "=" not in header:
            return False
        algo, _, sent = header.partition("=")
        algo = algo.strip().lower()
        if algo not in ("sha1", "sha256", "sha384", "sha512"):
            return False
        expected = hmac.new(self.secret.encode(), body, getattr(hashlib, algo)).hexdigest()
        return hmac.compare_digest(expected, sent.strip().lower())

    async def web_handle_challenge(self, request):
        """YouTube's hub calls this to verify a subscribe/unsubscribe request."""
        q = request.query
        challenge = q.get("hub.challenge")
        mode = q.get("hub.mode")
        topic = q.get("hub.topic", "")

        if not challenge or mode not in ("subscribe", "unsubscribe") or not topic.startswith(TOPIC_BASE):
            return web.Response(status=400)

        if mode == "subscribe":
            channel_id = topic[len(TOPIC_BASE):]
            async with get_db_connection() as db:
                cursor = await db.execute(
                    "SELECT 1 FROM social_subs WHERE platform = 'youtube' AND target_id = ?", (channel_id,)
                )
                if not await cursor.fetchone():
                    return web.Response(status=404)

        print(f"[WebSub] Verified {mode} challenge.")
        return web.Response(text=challenge)

    async def web_handle_notification(self, request):
        body = await request.read()

        # Reject anything that was not signed by YouTube's hub.
        # We still answer 200 so the sender learns nothing.
        if not self.verify_signature(body, request.headers.get("X-Hub-Signature")):
            print("[WebSub] Ignored notification with an invalid signature.")
            return web.Response(status=200)

        try:
            root = ET.fromstring(body)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015",
            }
            entry = root.find("atom:entry", ns)
            if entry is None:
                return web.Response(status=200)

            def get_text(tag):
                found = entry.find(tag, ns)
                return found.text if found is not None else None

            video_id = get_text("yt:videoId")
            channel_id = get_text("yt:channelId")
            title = get_text("atom:title") or "New video"
            author = get_text("atom:author/atom:name") or "Unknown"

            if not video_id or not channel_id or not VIDEO_ID_RE.match(video_id):
                print("[WebSub] Ignored incomplete notification.")
                return web.Response(status=200)

            link = f"https://www.youtube.com/watch?v={video_id}"
            print(f"[WebSub] Notification: {author} - {title}")

            async with get_db_connection() as db:
                await db.execute("UPDATE social_subs SET target_name = ? WHERE target_id = ?", (author, channel_id))
                await db.commit()

            # Process in the background so the hub gets its 200 right away.
            task = asyncio.create_task(self.process_notification(channel_id, video_id, title, author, link))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        except Exception as e:
            print(f"[WebSub] Parsing error: {e}")

        return web.Response(status=200)

    # --- LOGIC ---

    async def process_notification(self, channel_id, video_id, title, author, link):
        try:
            async with get_db_connection() as db:
                cursor = await db.execute(
                    "SELECT id, discord_channel_id, custom_message FROM social_subs "
                    "WHERE target_id = ? AND platform = 'youtube'",
                    (channel_id,),
                )
                subs = await cursor.fetchall()

            if not subs:
                return

            # Give YouTube time to finish the thumbnail so Discord doesn't cache a broken preview.
            await asyncio.sleep(5)

            # Titles/names come from YouTube, so neutralise @everyone, @here and mentions.
            safe_title = discord.utils.escape_mentions(title)
            safe_author = discord.utils.escape_mentions(author)

            for db_id, discord_channel_id, template in subs:
                # Atomically "claim" this video so duplicates/retries never post twice.
                async with get_db_connection() as db:
                    cursor = await db.execute(
                        "UPDATE social_subs SET last_id = ? WHERE id = ? AND COALESCE(last_id, '') != ?",
                        (video_id, db_id, video_id),
                    )
                    await db.commit()
                    if cursor.rowcount == 0:
                        print(f"[WebSub] Duplicate ignored: {video_id}")
                        continue

                try:
                    target_id = int(discord_channel_id)
                    channel = self.bot.get_channel(target_id)
                    if channel is None:
                        try:
                            channel = await self.bot.fetch_channel(target_id)
                        except (discord.NotFound, discord.Forbidden):
                            continue

                    content = (
                        (template or "Hey! **{author}** uploaded: {link}")
                        .replace("{link}", link)
                        .replace("{author}", safe_author)
                        .replace("{title}", safe_title)
                    )
                    await channel.send(content, allowed_mentions=SAFE_MENTIONS)

                except Exception as e:
                    print(f"[WebSub] Send Error: {e}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[WebSub] process_notification failed: {e}")

    async def subscribe_to_hub(self, channel_id, mode="subscribe"):
        if not self.callback_url or not self.secret:
            return False

        data = {
            "hub.callback": self.callback_url,
            "hub.mode": mode,
            "hub.topic": f"{TOPIC_BASE}{channel_id}",
            "hub.verify": "sync",
        }
        if mode == "subscribe":
            data["hub.secret"] = self.secret
            data["hub.lease_seconds"] = "432000"  # 5 days; we refresh every 24h

        try:
            async with self.session.post(HUB_URL, data=data) as resp:
                if resp.status in (202, 204):
                    print(f"[WebSub] {mode} OK: {channel_id}")
                    return True
                print(f"[WebSub] {mode} failed ({resp.status}) for {channel_id}")
                return False
        except aiohttp.ClientError as e:
            print(f"[WebSub] {mode} error for {channel_id}: {e}")
            return False

    # --- COMMANDS ---

    @commands.hybrid_group(name="youtube", description="Manage YouTube notification subscriptions.")
    async def youtube(self, ctx):
        pass

    @youtube.command(name="add", description="Add a YouTube channel to receive notifications.")
    @app_commands.describe(
        channel_id="The YouTube Channel ID (starts with 'UC', e.g., UCkJ1...)",
        discord_channel="The text channel where notification messages will be posted",
        message="Optional custom message. Variables: {author}, {title}, {link}",
    )
    @commands.has_permissions(manage_webhooks=True)
    async def yt_add(self, ctx, channel_id: str, discord_channel: discord.TextChannel, *, message: str = "Hey! **{author}** uploaded: {link}"):
        await ctx.defer()

        if not CHANNEL_ID_RE.match(channel_id):
            await ctx.send("❌ That doesn't look like a valid channel ID. It must start with `UC` and be 24 characters long.")
            return

        if len(message) > 500:
            await ctx.send("❌ Custom message is too long (max 500 characters).")
            return

        if not discord_channel.permissions_for(ctx.guild.me).send_messages:
            await ctx.send(f"❌ I can't send messages in {discord_channel.mention}.")
            return

        channel_name = await self.fetch_channel_name(channel_id) or "Unknown Channel"

        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT 1 FROM social_subs WHERE guild_id = ? AND platform = 'youtube' AND target_id = ?",
                (ctx.guild.id, channel_id),
            )
            if await cursor.fetchone():
                await ctx.send(f"⚠️ Already subscribed to **{channel_name}** ({channel_id}).")
                return

            await db.execute(
                """
                INSERT INTO social_subs (guild_id, discord_channel_id, platform, target_id, last_id, custom_message, target_name)
                VALUES (?, ?, 'youtube', ?, '0', ?, ?)
                """,
                (ctx.guild.id, discord_channel.id, channel_id, message, channel_name),
            )
            await db.commit()

        if await self.subscribe_to_hub(channel_id):
            await ctx.send(f"✅ Subscribed to **{channel_name}**!")
        else:
            await ctx.send(f"⚠️ Saved **{channel_name}**, but the WebSub connection failed. Check the console.")

    @youtube.command(name="remove", description="Remove a YouTube channel subscription.")
    @app_commands.describe(channel_id="The YouTube Channel ID to unsubscribe from (e.g., UCkJ1...)")
    @commands.has_permissions(manage_webhooks=True)
    async def yt_remove(self, ctx, channel_id: str):
        await ctx.defer()

        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT target_name FROM social_subs WHERE guild_id = ? AND platform = 'youtube' AND target_id = ?",
                (ctx.guild.id, channel_id),
            )
            data = await cursor.fetchone()
            if not data:
                await ctx.send("❌ This server isn't subscribed to that channel.")
                return

            await db.execute(
                "DELETE FROM social_subs WHERE guild_id = ? AND platform = 'youtube' AND target_id = ?",
                (ctx.guild.id, channel_id),
            )
            await db.commit()

            # Only drop the hub subscription if NO other server still uses this channel.
            cursor = await db.execute(
                "SELECT COUNT(*) FROM social_subs WHERE platform = 'youtube' AND target_id = ?", (channel_id,)
            )
            (remaining,) = await cursor.fetchone()

        if remaining == 0:
            await self.subscribe_to_hub(channel_id, mode="unsubscribe")

        await ctx.send(f"🗑️ Unsubscribed from **{data[0] or channel_id}**.")

    @youtube.command(name="list", description="Display all active YouTube subscriptions.")
    async def yt_list(self, ctx):
        async with get_db_connection() as db:
            cursor = await db.execute(
                "SELECT target_name, target_id, discord_channel_id FROM social_subs WHERE guild_id = ? AND platform = 'youtube'",
                (ctx.guild.id,),
            )
            rows = await cursor.fetchall()

        if not rows:
            await ctx.send("📭 No subscriptions.")
            return

        text = "\n".join(f"• **{r[0]}** (`{r[1]}`) → <#{r[2]}>" for r in rows)
        embed = discord.Embed(title="YouTube Subscriptions", description=text[:4000], color=discord.Color.red())
        await ctx.send(embed=embed)

    # --- TASKS ---

    @tasks.loop(hours=24)
    async def resubscribe_loop(self):
        print("[WebSub] Refreshing subscriptions...")
        try:
            async with get_db_connection() as db:
                cursor = await db.execute("SELECT DISTINCT target_id FROM social_subs WHERE platform = 'youtube'")
                rows = await cursor.fetchall()
        except Exception as e:
            print(f"[WebSub] Could not read subscriptions: {e}")
            return

        for (target_id,) in rows:
            try:
                await self.subscribe_to_hub(target_id)
            except Exception as e:
                print(f"[WebSub] Resubscribe failed for {target_id}: {e}")
            await asyncio.sleep(1)

    @resubscribe_loop.before_loop
    async def before_resub(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Socials(bot))
