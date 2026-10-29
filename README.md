# Discord Voice Channel Bot

A 24/7 Discord voice channel presence bot built with `discord.py` and Flask.

## Features
- **24/7 Voice Channel Sitter:** Stays connected to a designated voice channel (self-muted and deafened) with auto-reconnection handling.
- **Race-Free Voice Reconnect:** Gracefully handles transient voice disconnects without collisions or session invalidation loops.
- **Auto Channel Recovery:** Moves back to the designated voice channel if moved by an administrator.
- **Targeted Chat Triggers:** Listens in a dedicated text channel for `hora?` / `Hora?` prompts and replies with dynamic case matching and custom user interactions.
- **Keep-Alive Server:** Built-in lightweight Flask health check endpoint on port 8000 for cloud hosting platforms (e.g. Kerit Cloud, Koyeb, Render).

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your variables:
   ```env
   DISCORD_TOKEN=your_token
   VOICE_CHANNEL_ID=your_vc_id
   CHAT_CHANNEL_ID=your_chat_channel_id
   PORT=8000
   ```

3. Enable **Message Content Intent** and **Voice State Intent** in the Discord Developer Portal under your application's **Bot** tab.

4. Run the bot:
   ```bash
   python bot.py
   ```
