# Scrim wheel: plan

Horaya runs the fun-scrim team draw for Mobile Legends instead of a browser wheel. People join a lobby with a
button, then each `/scrim wheel` spins an animated wheel in Discord and drops one player into Blue or Red, until
both teams have 5.

Status: plan only. Nothing is built yet.

## What it does

1. `/scrim start` posts a lobby message with **Join** and **Leave** buttons. Each click updates the message's
   player list (`1. @Horali`, `2. @Sevielle`, ...). Any number can join.
2. `/scrim wheel` closes the lobby the first time it runs, then spins once and posts a **new message** with the
   wheel landing on one player. Pick 1 goes to Blue, pick 2 to Red, then Blue again, and so on.
3. `/scrim wheel mode:automatic` keeps spinning on its own, waiting 5 seconds after each reveal, until there are 10
   picks.
4. `/scrim remove member` takes someone out: out of the wheel if they haven't been picked yet, or out of their team
   if they have. If they were on a team, the next spin refills that slot.
5. When both teams have 5 (or nobody is left in the wheel), Horaya posts the final teams and pings the 10 players.
   Anyone left over is listed as the bench.
6. `/scrim end` cancels the scrim at any time and stops an automatic run. This one isn't in the request, but
   something has to close a scrim that never finishes.

## The animation: decision

**A real spinning wheel, rendered by the bot as a GIF**, like the browser wheel the server uses now. Each spin is
drawn fresh with the players still in the wheel and lands on the player who was already picked at random.

I tried it in a prototype on this PC: 12 names, 360 px, 15 fps, about 5 seconds of spin.

| | Result |
|---|---|
| Render time | 0.8 s here, probably 2-4 s on Wispbyte's shared CPU |
| File size | about 1.5 MB per spin (Discord's upload limit is 10 MB) |
| Look | Coloured slices with names reading outward, a pointer at the top in the team's colour, and a centre hub showing `BLUE / pick 1` |

A sample GIF from the prototype is in the scratchpad. The real one adds the highlight on the winning slice.

### Why not something else

| Option | Why not |
|---|---|
| Text slot reel (edit one message over and over: `▸ Alei ◂` ... `▸ Kaito ◂`) | Discord allows about 5 edits per 5 seconds in a channel, so it runs at roughly 1 frame a second and looks choppy. Kept as the fallback if rendering fails. |
| Emoji roulette (a row of emojis that "rolls") | Same edit limit, and it doesn't show who is in the draw. |
| MP4 video | Needs ffmpeg on the host (big, and slow on a free CPU). A GIF plays inline the same way. |
| Discord's own wheel / activities | Discord has no wheel; an Activity is a whole web app and would need its own hosting. |

### Keeping it hard to predict

- The winner is chosen first with `random.SystemRandom` (every player still in the wheel has the same chance). The
  animation is only drawn to land on them.
- The slice order is reshuffled every spin, so where a name sits says nothing.
- Every spin has a random start angle, 4-6 full turns, and a random length of 4.5-6 s.
- Ease-out curve: fast at first, then a long slow crawl, so the last few slices keep everyone guessing.
- About a third of spins stop a hair from the edge of the slice (a near miss), so the second-to-last name feels
  like it almost won.

### One pick, start to finish

1. The host runs `/scrim wheel`. Horaya acknowledges straight away ("Horaya is thinking...") because Discord
   needs an answer within 3 seconds, and renders the GIF in a background thread so voice is never blocked.
2. New message: `🎡 Pick 3/10 for 🔵 Blue - spinning...` with the GIF.
3. When the spin ends, Horaya edits that message: the GIF is replaced with a still image of the landed wheel
   (winning slice lit, others dimmed), plus a Blue or Red embed: `🔵 Sevielle joins Blue!`, their avatar, a short
   hype line, and `Blue 2/5 · Red 2/5 · 6 left in the wheel`.
4. The lobby message turns into a live board: Blue list, Red list, still-in-the-wheel list.

Swapping the GIF for a still image matters: some reports say Discord loops GIFs no matter what the file says. A
still image after the spin means nobody sees the wheel start spinning again. As a second guard, the GIF's last
frame is held for 10 minutes.

When only one player is left for the last slot, there's no spin: they go straight in with the reveal embed.

## Rules (defaults, change any)

- **One scrim per server** at a time. A second `/scrim start` points to the open one.
- **Who controls it:** only you can run `/scrim` commands. Anyone can Join or Leave while the lobby is open.
- **Lobby locks** on the first spin (matches "it will keep saving until i do /scrim wheel"). Leave is also closed
  then, so use `/scrim remove`.
- **Team order:** Blue, Red, Blue, ... After a removal, the next pick goes to the team that is short (Blue on a
  tie).
- **Fewer than 10 joined:** everyone gets picked, so 7 people make Blue 4, Red 3. At least 2 are needed to spin.
- **Names on the wheel:** the profile name (like explain uses), cut to 12 characters. If it has characters the
  built-in font can't draw (emoji, Japanese, fancy unicode), the wheel shows the username instead. Mentions in
  the messages still show the server nickname as usual.
- **Spam safety:** a second `/scrim wheel` while one is spinning, or during an automatic run, gets a private
  "the wheel is still spinning" reply instead of a double pick.

## Build

### Files

- `wheel.py`: draws the wheel GIF and the still result image with Pillow. No Discord code, so it can be tested
  offline.
- `scrim.py`: the `/scrim` commands, the Join/Leave buttons, the scrim state and the pick logic.
- `bot.py`: adds a command tree to `VCBot` and loads the scrim commands on the main bot only. Horaya 2 (backup)
  gets nothing new.
- `requirements.txt`: add `Pillow>=10.1,<13`. 10.1 is the first version with a resizable built-in font, so no
  font file is needed.
- `Dockerfile`: copy all `.py` files, not only `bot.py`.

### Discord details

- Slash commands come from discord.py's `app_commands`, synced at startup and limited to servers. Horaya's invite
  already covers them: the `bot` scope includes `applications.commands`.
- The Join/Leave buttons use fixed IDs and are registered at startup, so they keep working after a restart, the
  same way the heartbeat button does.
- Scrim state is saved to `scrim_state.json` (written the same safe way as `reminder_state.json`): the lobby
  message, host, players, Blue, Red, and the phase. After a restart the lobby and teams are still there. An
  automatic run doesn't resume; the host runs it again.
- Player lists in embeds use mentions, which show names without pinging. Only the final teams message pings.
- Messages per pick: 1 new, 1 edit, 1 lobby edit, about every 11 seconds in automatic mode. That's well under
  Discord's limits.
- No new intents. Buttons and slash commands don't need Message Content.

### Hosting (Wispbyte, main bot)

- Each spin uses about 30 MB of RAM while rendering. That fits next to explain's 210 MB in the 612 MB.
- Pillow is about 10 MB on disk.
- Check how Wispbyte installs packages, by startup command or a requirements setting, before deploying.
- Upload `wheel.py` and `scrim.py` along with `bot.py`.
- Automatic mode for 10 picks takes about 2 minutes: around 6 s per spin, plus the 5 s gap.

### Order of work

1. `wheel.py`: render the GIF and still image, with an offline script that saves samples and times them. Check
   the name fallback with emoji and Japanese names.
2. `scrim.py`: state, rules and pick order, tested offline with fake interactions (like the explain tests).
3. Wire it into `bot.py`, then try it in the second server Horaya joined before using it in the main one.
4. README section, then commit in small pieces and deploy to Wispbyte.

## Decided (answers from Hora)

1. Only Hora runs the `/scrim` commands (start, wheel, add, remove, end). Everyone else can only tap Join or
   Leave; their commands get "Sorry, i only serve rara".
2. Fewer than 10 is fine (uneven teams), handy for testing.
3. The final message pings all the picked players.
4. Added to this build:
   - `/scrim add member` puts someone in the wheel (also after the lobby closed).
   - `/scrim wheel mode:reroll` undoes the last pick, puts that player back in the wheel and spins again for the
     same team. Manual only: it is refused while an automatic run is going.
5. Later ideas, not in this build: swap two players, captains mode.
