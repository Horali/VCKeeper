# Horaya Bot

Horaya is a Discord bot made for one server. She sits in a voice channel around the clock, runs MLBB scrim team draws
with a spinning wheel, explains slang on request, and sends daily hosting reminders. Built with `discord.py`, Pillow
and Flask.

A second copy, **Horaya 2**, runs on another host as a backup so the voice channel never empties.

## Features

### Voice presence
- **24/7 voice channel sitter:** stays connected to one voice channel.
- **Auto-reconnect:** handles voice gateway drops and Discord node rotations without getting stuck in
  session-invalidated (4006) loops.
- **Channel recovery:** goes back to her channel if someone moves her.

### Scrim wheel (owner runs it, anyone can join)
- `/scrim start` opens a lobby with **Join** and **Leave** buttons, and the player list updates live.
- `/scrim wheel` spins an animated wheel and drops one player into Blue or Red (Blue, Red, Blue...) until both
  teams have 5. The wheel spins inside the message under the team list, then stops on a still picture.
- `mode:automatic` keeps spinning on its own with 3 seconds between picks. `mode:pause` and `mode:continue` stop
  and resume it, and `mode:reroll` redoes the last pick.
- `/scrim add` and `/scrim remove` change who is in, and `/scrim end` closes the scrim.
- Only the final message pings the players, with the finished teams and anyone left on the bench.
- Fewer than 10 players is fine; the teams just come out uneven.
- Picks are fully random (`random.SystemRandom`). The scrim survives a restart (`scrim_state.json`).

### Explain (AI, owner only)
- Ping Horaya with the word `explain` while replying to a message, and she explains its slang or references in a few
  sentences.
- Put a word in quotes (`@Horaya explain "instalment"`) to explain just that word. Any other words after `explain`
  are a question about the message ("explain what they meant").
- Anyone else gets "Sorry, i only serve rara".
- Answers come from Claude Haiku through headless Claude Code on the owner's Claude plan, with no tools enabled.

### Chat trigger
- Listens in one text channel for `hora?` / `Hora?`, keeps the capitalization, and supports custom user pings.

### Daily hosting reminder
- Sends hourly DM reminders from **19:00 to 00:00 UTC+7** to renew the free hosting lease.
- A persistent **"Yes, I've done the heartbeat"** button confirms the day, disables itself and stops the reminders
  until the next cycle.
- State is saved to `reminder_state.json` with atomic writes, so it survives restarts.
- `REMINDER_TEST=1` sends a test reminder right away on startup.

### Keep-alive server
- A small Flask health check on `/` that picks up `PORT` or Pterodactyl's `SERVER_PORT`, for hosts like Kerit Cloud,
  Koyeb or Render.

## Running one bot or two

The same code runs both bots. `BOT_ROLE` picks the mode:

| `BOT_ROLE` | What runs | Needed env vars | Message Content Intent |
|---|---|---|---|
| `main` (default) | everything above | `DISCORD_TOKEN`, `VOICE_CHANNEL_ID`, `CHAT_CHANNEL_ID` | required |
| `backup` | voice presence only | `DISCORD_TOKEN`, `VOICE_CHANNEL_ID` | not needed |

Run the `backup` bot as a separate Discord application on a different host. If the main bot drops for a few
seconds, the backup keeps the voice channel from emptying, so the channel timer is not reset.

### Mic and speaker icons

`VOICE_MUTED=0` (default) joins with the mic and speaker icons on. `VOICE_MUTED=1` joins muted and deafened, and
Discord then sends the bot no channel audio at all. The bot never plays, records or processes audio in either
mode; it has no audio code.

### Explain feature setup

1. On your own PC, run `claude setup-token` and copy the token it prints (it uses your Claude plan's limits).
2. Put it in the host's `.env` as `CLAUDE_CODE_OAUTH_TOKEN=...` (main bot only).
3. Restart. On first start the bot downloads Claude Code into `.claude-bin/` (about 330 MB of disk).

Each answer runs one `claude -p` process that peaks at about 210 MB of RAM, so the main bot needs a host with
512 MB (a 256 MB host runs out of memory). The token can spend your plan, so keep it only in `.env`.

### CPU use

Free hosts may stop a server that uses too much CPU (Wispbyte's free plan stops it at about 15% average). Most of
Horaya's features cost almost nothing. The heavy parts are drawing the scrim wheel (about 0.25 seconds of CPU per
spin) and the explain feature (one Claude Code process per answer).

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Horali/Horaya-Bot.git
   cd Horaya-Bot
   ```

2. **Install dependencies** (Python 3.10+):
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables.** Copy `.env.example` to `.env`:
   ```env
   DISCORD_TOKEN=your_bot_token_here
   VOICE_CHANNEL_ID=your_voice_channel_id_here
   PORT=8000
   BOT_ROLE=main
   CHAT_CHANNEL_ID=your_chat_channel_id_here
   REMINDER_USER_ID=your_discord_user_id_here
   REMINDER_TEST=0
   ```
   For a voice-only backup bot, set `BOT_ROLE=backup` and leave out the `main`-only variables.

4. **Discord Developer Portal settings:**
   - Under your bot application → **Bot** → **Privileged Gateway Intents**, enable **Message Content Intent**
     (needed for `hora?` and `explain`, `main` role only).
   - Invite the bot with the `bot` scope. It includes slash commands, so `/scrim` shows up after the first start.

5. **Run the bot:**
   ```bash
   python bot.py
   ```

## Cloud deployment (Kerit Cloud, Wispbyte / Pterodactyl)

- Set **App py file** to `bot.py`.
- If using Git auto-update, point **Git Repo Address** to `https://github.com/Horali/Horaya-Bot`.
- Place your `.env` file in the server root via the panel File Manager.
- Make sure the assigned server port matches (the bot detects `SERVER_PORT` on its own).
