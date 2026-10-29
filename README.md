# VCKeeper

A 24/7 Discord voice channel presence bot with auto-reconnect, chat triggers, and daily cloud renewal reminders built with `discord.py` and Flask.

## Features

- **24/7 Voice Channel Sitter:** Stays permanently connected to a designated voice channel (self-muted and deafened).
- **Race-Free Auto-Reconnect:** Gracefully handles transient voice gateway drops and Discord node rotations without session invalidation (4006) loops.
- **Auto Channel Recovery:** Automatically returns to the designated voice channel if moved by an administrator.
- **Targeted Chat Triggers:** Listens in a designated text channel for `hora?` / `Hora?` prompts, preserving input capitalization and supporting custom user pings.
- **Kerit Cloud Daily Heartbeat Reminder:**
  - Sends hourly DM reminders from **19:00 to 00:00 UTC+7** to renew free hosting leases before expiration.
  - Interactive persistent button (**"Yes, I've done the heartbeat"**) that confirms the day, updates the message, disables the button, and stops further reminders for the cycle.
  - State persistence (`reminder_state.json`) with atomic file writes so confirmations and reminder tracking survive bot restarts.
  - Optional `REMINDER_TEST=1` mode to test the reminder DM and button immediately on startup.
- **Keep-Alive Server:** Built-in lightweight Flask health check endpoint on `/` that auto-detects `PORT` or Pterodactyl's `SERVER_PORT` for cloud hosting platforms (Kerit Cloud, Koyeb, Render).

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Horali/VCKeeper.git
   cd VCKeeper
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   Copy `.env.example` to `.env`:
   ```env
   DISCORD_TOKEN=your_bot_token_here
   VOICE_CHANNEL_ID=your_voice_channel_id_here
   CHAT_CHANNEL_ID=your_chat_channel_id_here
   PORT=8000
   REMINDER_USER_ID=your_discord_user_id_here
   REMINDER_TEST=0
   ```

4. **Discord Developer Portal Settings:**
   - Under your bot application $\rightarrow$ **Bot** $\rightarrow$ **Privileged Gateway Intents**:
     - Enable **Message Content Intent** (required for `hora?` chat triggers).
     - Enable **Voice State Intent** (required for voice channel presence).

5. **Run the bot:**
   ```bash
   python bot.py
   ```

## Cloud Deployment (Kerit Cloud / Pterodactyl)

- Set **App py file** to `bot.py`.
- If using Git auto-update, point **Git Repo Address** to `https://github.com/Horali/VCKeeper`.
- Place your `.env` file in the server root via the panel File Manager.
- Ensure your assigned server port matches (the bot automatically detects `SERVER_PORT`).
