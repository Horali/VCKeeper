import asyncio
import datetime
import json
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
PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or "8000")

# Daily reminder config (UTC+7 timezone without requiring external tzdata)
REMINDER_USER_ID = int(os.environ.get("REMINDER_USER_ID", "371275541862678528"))
REMINDER_TEST = os.environ.get("REMINDER_TEST", "0").lower() in ("1", "true", "yes")
TZ_UTC7 = datetime.timezone(datetime.timedelta(hours=7))
REMINDER_HOURS = (19, 20, 21, 22, 23, 0)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminder_state.json")

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


def get_reminder_cycle_date(dt: datetime.datetime) -> str:
    """
    Returns the YYYY-MM-DD cycle date string for a UTC+7 datetime.
    The cycle begins at 19:00 UTC+7 and ends at 18:59 the next day.
    Hours 00:00 to 18:59 belong to the previous day's 19:00 cycle.
    """
    if dt.hour < 19:
        return (dt.date() - datetime.timedelta(days=1)).isoformat()
    return dt.date().isoformat()


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.exception("Failed to load reminder state file")
    return {"last_confirmed_date": None, "last_sent_cycle_date": None, "last_sent_hour": None}


def save_state(state: dict) -> None:
    tmp_file = f"{STATE_FILE}.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_file, STATE_FILE)
    except Exception:
        log.exception("Failed to atomically save reminder state")


class HeartbeatReminderView(discord.ui.View):
    """Persistent view with a button to confirm the Kerit Cloud heartbeat."""

    def __init__(self, bot: "VCBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Yes, I've done the heartbeat",
        style=discord.ButtonStyle.success,
        custom_id="kerit_heartbeat_confirm_btn",
    )
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != REMINDER_USER_ID:
            await interaction.response.send_message(
                "You are not authorized to confirm this reminder.", ephemeral=True
            )
            return

        now_utc7 = datetime.datetime.now(TZ_UTC7)
        cycle_date = get_reminder_cycle_date(now_utc7)

        # Mark cycle as confirmed in persistent state
        state = load_state()
        state["last_confirmed_date"] = cycle_date
        save_state(state)

        log.info(
            "Heartbeat confirmed by user %s for cycle %s (%s UTC+7)",
            interaction.user.id,
            cycle_date,
            now_utc7.strftime("%H:%M"),
        )

        button.disabled = True
        button.label = "Heartbeat Confirmed ✓"
        button.style = discord.ButtonStyle.secondary

        confirmed_text = (
            f"✅ **Heartbeat Confirmed!**\n"
            f"Marked as done for cycle **{cycle_date}** ({now_utc7.strftime('%H:%M')} UTC+7).\n"
            f"No more reminders will be sent for this cycle. The next cycle starts tomorrow at 19:00 UTC+7."
        )
        await interaction.response.edit_message(content=confirmed_text, view=self)


class VCBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.lock = asyncio.Lock()
        self.watchdog_started = False
        self.reminder_started = False

    async def setup_hook(self):
        # Register the persistent view so button clicks work even after bot restarts
        self.add_view(HeartbeatReminderView(self))

    async def on_ready(self):
        log.info("Logged in as %s", self.user)
        if not self.watchdog_started:
            self.watchdog_started = True
            self.loop.create_task(self.watchdog())
        if not self.reminder_started:
            self.reminder_started = True
            self.loop.create_task(self.reminder_loop())
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
            greeting = "Hora!" if text.startswith("H") else "hora!"

            # Custom response for Kihaha (ID: 528600966593773588) with a ping to miuveox (ID: 371275541862678528)
            if message.author.id == 528600966593773588:
                await message.reply(f"{greeting} Kihaha has called you! <@371275541862678528>")
            else:
                await message.reply(greeting)

    async def reminder_loop(self):
        """Hourly reminder loop waking up on the hour in UTC+7."""
        # Immediate test reminder if REMINDER_TEST is set
        if REMINDER_TEST:
            log.info("REMINDER_TEST is active: sending immediate test reminder...")
            await asyncio.sleep(5)
            await self.send_reminder(is_test=True)

        # Initial check on startup in case the bot restarted during a reminder hour
        try:
            await self.check_and_send_reminder()
        except Exception:
            log.exception("Error in initial reminder check")

        while not self.is_closed():
            now_utc7 = datetime.datetime.now(TZ_UTC7)
            # Sleep until 1 second past the start of the next hour
            seconds_until_next_hour = 3600 - (now_utc7.minute * 60 + now_utc7.second) + 1
            await asyncio.sleep(seconds_until_next_hour)

            try:
                await self.check_and_send_reminder()
            except Exception:
                log.exception("Error in hourly reminder check")

    async def check_and_send_reminder(self):
        """Checks if a reminder should be sent for the current UTC+7 hour."""
        now_utc7 = datetime.datetime.now(TZ_UTC7)
        hour = now_utc7.hour

        if hour not in REMINDER_HOURS:
            return

        cycle_date = get_reminder_cycle_date(now_utc7)
        state = load_state()

        # If already confirmed for this cycle, do not send
        if state.get("last_confirmed_date") == cycle_date:
            log.debug("Heartbeat already confirmed for cycle %s, skipping", cycle_date)
            return

        # If already sent for this exact cycle and hour, do not duplicate
        if state.get("last_sent_cycle_date") == cycle_date and state.get("last_sent_hour") == hour:
            log.debug("Reminder already sent for %s hour %d, skipping", cycle_date, hour)
            return

        await self.send_reminder(cycle_date=cycle_date, hour=hour)

    async def send_reminder(self, cycle_date: str = None, hour: int = None, is_test: bool = False):
        """Sends the reminder DM with link and persistent confirmation button."""
        now_utc7 = datetime.datetime.now(TZ_UTC7)
        if cycle_date is None:
            cycle_date = get_reminder_cycle_date(now_utc7)
        if hour is None:
            hour = now_utc7.hour

        try:
            user = self.get_user(REMINDER_USER_ID)
            if user is None:
                user = await self.fetch_user(REMINDER_USER_ID)
        except discord.NotFound:
            log.error("Reminder target user ID %s not found", REMINDER_USER_ID)
            return
        except discord.DiscordException:
            log.exception("Failed to fetch reminder target user %s", REMINDER_USER_ID)
            return

        time_str = now_utc7.strftime("%H:%M")
        header = "🧪 **[TEST REMINDER]**" if is_test else f"⏰ **Kerit Cloud Heartbeat Reminder ({time_str} UTC+7)**"

        msg_text = (
            f"{header}\n\n"
            f"It's time to do your daily heartbeat to keep your free server alive!\n\n"
            f"1. Open the dashboard: https://billing.kerit.cloud/dashboard\n"
            f"2. Click **Free Hosting** in the sidebar.\n"
            f"3. Click the **Heartbeat** button (solve the captcha).\n\n"
            f"Once you've done it, click the button below to stop reminders for today."
        )

        view = HeartbeatReminderView(self)
        try:
            await user.send(content=msg_text, view=view)
            log.info(
                "Sent heartbeat reminder to user %s for cycle %s at %s UTC+7 (test=%s)",
                REMINDER_USER_ID,
                cycle_date,
                time_str,
                is_test,
            )
            if not is_test:
                state = load_state()
                state["last_sent_cycle_date"] = cycle_date
                state["last_sent_hour"] = hour
                save_state(state)
        except discord.Forbidden:
            log.warning("Cannot send DM to user %s (DMs are closed or bot is blocked by user)", REMINDER_USER_ID)
        except discord.HTTPException:
            log.exception("Discord HTTP error sending reminder DM to %s", REMINDER_USER_ID)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    VCBot().run(TOKEN)
