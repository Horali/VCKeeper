import asyncio
import logging
import os
import threading

import discord
from flask import Flask

try:
    from dotenv import load_dotenv

    load_dotenv()  # lets panel hosts use a .env file instead of real env vars
except ImportError:
    pass

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["VOICE_CHANNEL_ID"])
CHAT_CHANNEL_ID = int(os.environ["CHAT_CHANNEL_ID"])
PORT = int(os.environ.get("PORT", "8000"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vcbot")

# Keep-alive web server (needed for panel-style cloud hosts)
app = Flask(__name__)


@app.route("/")
def index():
    return "OK"


def run_web():
    try:
        app.run(host="0.0.0.0", port=PORT)
    except OSError:
        log.warning("Web server could not bind to port %s, continuing without it", PORT)


class VCBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.lock = asyncio.Lock()
        self.watchdog_started = False

    async def on_ready(self):
        log.info("Logged in as %s", self.user)
        if not self.watchdog_started:
            self.watchdog_started = True
            self.loop.create_task(self.watchdog())
        try:
            await self.ensure_connected()
        except Exception:
            log.exception("Initial connect failed, watchdog will retry")

    async def watchdog(self):
        while not self.is_closed():
            await asyncio.sleep(20)
            try:
                await self.ensure_connected()
            except Exception:
                log.exception("Watchdog reconnect failed")

    async def ensure_connected(self):
        async with self.lock:
            channel = self.get_channel(CHANNEL_ID)
            if channel is None:
                try:
                    channel = await self.fetch_channel(CHANNEL_ID)
                except discord.DiscordException:
                    log.error("Cannot find voice channel %s", CHANNEL_ID)
                    return

            vc = channel.guild.voice_client

            # 1. Connected and in the correct channel -> all good
            if vc is not None and vc.is_connected():
                if vc.channel.id != CHANNEL_ID:
                    log.info("In wrong channel (%s), moving back to %s", vc.channel.name, channel.name)
                    await vc.move_to(channel)
                return

            # 2. Voice client exists but is temporarily reconnecting internally.
            # DO NOT call vc.disconnect(force=True) — let discord.py finish its handshake!
            # Forcing a disconnect here creates the double-session collision and 4006 storm.
            if vc is not None:
                log.debug("Voice client is currently reconnecting internally, waiting...")
                return

            # 3. No voice client exists in this guild at all -> clean connect
            log.info("Connecting to %s", channel.name)
            try:
                await channel.connect(
                    self_deaf=True, self_mute=True, reconnect=True, timeout=30
                )
            except discord.ClientException:
                log.debug("Voice connection already in progress")

    async def on_voice_state_update(self, member, before, after):
        if self.user is None or member.id != self.user.id:
            return

        # Case A: Bot was dragged to a different channel -> move back
        if after.channel is not None and after.channel.id != CHANNEL_ID:
            log.info("Bot was moved to %s, returning to target channel", after.channel.name)
            vc = member.guild.voice_client
            if vc is not None:
                try:
                    target_channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)
                    await vc.move_to(target_channel)
                except Exception:
                    log.exception("Failed to move back to target channel")
            return

        # Case B: Bot left the channel (kicked, server shutdown, etc.)
        if before.channel is not None and after.channel is None:
            log.info("Voice state left channel, waiting 5s for internal resolution...")
            # Give discord.py's internal reconnector time to resume or teardown
            await asyncio.sleep(5)
            try:
                await self.ensure_connected()
            except Exception:
                log.exception("Reconnect failed, watchdog will retry")

    async def on_message(self, message: discord.Message):
        # Ignore messages from bots (including itself)
        if message.author.bot:
            return
        if message.channel.id != CHAT_CHANNEL_ID:
            return

        text = message.content.strip()
        if text.lower() == "hora?":
            # Match the capitalization of the input (Hora! vs hora!)
            greeting = "Hora!" if text.startswith("H") else "hora!"

            # Custom response for Kihaha (ID: 528600966593773588) with a ping to miuveox (ID: 371275541862678528)
            if message.author.id == 528600966593773588:
                await message.reply(f"{greeting} Kihaha has called you! <@371275541862678528>")
            else:
                await message.reply(greeting)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    VCBot().run(TOKEN)
