"""/scrim: a Join/Leave lobby, then a spinning wheel that drops players into Blue and Red, 5 each.

Only the owner runs the commands; anyone can tap Join or Leave while the lobby is open. Main bot only.
"""
import asyncio
import io
import json
import logging
import os
import random

import discord
from discord import app_commands

import wheel

log = logging.getLogger("vcbot.scrim")

TEAM_SIZE = 5
AUTO_GAP = 3  # seconds between an automatic reveal and the next spin
TEAMS = ("blue", "red")
EMOJI = {"blue": "🔵", "red": "🔴"}
COLOR = {"blue": discord.Color(0x2F80ED), "red": discord.Color(0xEB4D4B)}
LOBBY_COLOR = discord.Color(0xF4B400)
NO = "Sorry, i only serve rara"
HYPE = [
    "The wheel has spoken.",
    "No refunds.",
    "Destiny, basically.",
    "Fresh off the wheel.",
    "Called it. (nobody called it)",
    "Lock in.",
    "Gacha pull secured.",
    "Ready for the land of dawn.",
]
rng = random.SystemRandom()  # who gets picked; the look of each spin uses a normal Random


def player_from(user) -> dict:
    name = getattr(user, "global_name", None) or user.name  # profile name, like explain uses
    return {"id": user.id, "name": name, "label": wheel.label(name, user.name),
            "avatar": str(user.display_avatar.url)}


class Scrim:
    def __init__(self, data: dict):
        self.guild_id: int = data["guild_id"]
        self.channel_id: int = data["channel_id"]
        self.lobby_id: int | None = data.get("lobby_id")
        self.phase: str = data.get("phase", "lobby")  # lobby -> picking -> done
        self.pool: list[dict] = data.get("pool", [])  # still in the wheel
        self.blue: list[dict] = data.get("blue", [])
        self.red: list[dict] = data.get("red", [])
        self.last: dict | None = data.get("last")  # {"id", "team"} of the newest pick, for reroll
        self.lock = asyncio.Lock()  # held while a spin is running
        self.auto: asyncio.Task | None = None
        self.pausing = False  # set by /scrim wheel mode:pause; the automatic draw stops before its next spin
        self.prepared: tuple | None = None  # (key, order, winner, render task): the next spin, drawn ahead of time
        self.prepare_timer: asyncio.Task | None = None

    def to_dict(self) -> dict:
        return {"guild_id": self.guild_id, "channel_id": self.channel_id, "lobby_id": self.lobby_id,
                "phase": self.phase, "pool": self.pool, "blue": self.blue, "red": self.red, "last": self.last}

    def team(self, name: str) -> list[dict]:
        return self.blue if name == "blue" else self.red

    def everyone(self) -> list[dict]:
        return self.pool + self.blue + self.red

    def find(self, user_id: int) -> tuple[str | None, dict | None]:
        for where in ("pool", "blue", "red"):
            for p in getattr(self, where):
                if p["id"] == user_id:
                    return where, p
        return None, None

    def picked(self) -> int:
        return len(self.blue) + len(self.red)

    def goal(self) -> int:
        return min(2 * TEAM_SIZE, len(self.everyone()))

    def next_team(self) -> str | None:
        """Blue, Red, Blue... After a removal, the short team first (Blue on a tie)."""
        if not self.pool:
            return None
        open_teams = [t for t in TEAMS if len(self.team(t)) < TEAM_SIZE]
        return min(open_teams, key=lambda t: len(self.team(t))) if open_teams else None

    def spin_key(self, team: str) -> tuple:
        """What a pre-drawn spin depends on: who's in the wheel, which team, which pick number."""
        return tuple(sorted(p["id"] for p in self.pool)), team, self.picked() + 1

    def auto_running(self) -> bool:
        return self.auto is not None and not self.auto.done()


def mentions(players: list[dict], numbered: bool = False) -> str:
    if not players:
        return "-"
    if numbered:
        return "\n".join(f"{i}. <@{p['id']}>" for i, p in enumerate(players, 1))
    return "\n".join(f"<@{p['id']}>" for p in players)


def teams_text(s: Scrim) -> str:
    def side(team):
        players = s.team(team)
        lines = "\n".join(f"{i}. <@{p['id']}>" for i, p in enumerate(players, 1)) or "-"
        return f"{EMOJI[team]} **{team.title()}** ({len(players)}/{TEAM_SIZE})\n{lines}"
    return f"{side('blue')}\n\n{side('red')}"


def board(s: Scrim) -> discord.Embed:
    if s.phase == "lobby":
        e = discord.Embed(title="⚔️ Fun scrim - who's in?", color=LOBBY_COLOR,
                          description="Tap **Join** to get in the wheel. It picks 10: 5 for 🔵 Blue, 5 for 🔴 Red.")
        e.add_field(name=f"Players ({len(s.pool)})", value=mentions(s.pool, numbered=True)[:1024], inline=False)
        return e
    title = "🏁 Teams are set" if s.phase == "done" else "🎡 Scrim draw in progress"
    e = discord.Embed(title=title, color=LOBBY_COLOR)
    e.add_field(name=f"🔵 Blue ({len(s.blue)}/{TEAM_SIZE})", value=mentions(s.blue, True)[:1024])
    e.add_field(name=f"🔴 Red ({len(s.red)}/{TEAM_SIZE})", value=mentions(s.red, True)[:1024])
    left = "Bench" if s.phase == "done" else "Still in the wheel"
    e.add_field(name=f"{left} ({len(s.pool)})", value=mentions(s.pool)[:1024], inline=False)
    return e


class LobbyView(discord.ui.View):
    """Join/Leave on the lobby message. Fixed ids so the buttons still work after a restart."""

    def __init__(self, manager: "ScrimManager"):
        super().__init__(timeout=None)
        self.manager = manager

    async def _scrim(self, interaction: discord.Interaction) -> Scrim | None:
        s = self.manager.scrims.get(interaction.guild_id)
        if s is None or s.lobby_id != interaction.message.id or s.phase != "lobby":
            await interaction.response.send_message("This scrim lobby is closed.", ephemeral=True)
            return None
        return s

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="⚔️", custom_id="scrim_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = await self._scrim(interaction)
        if s is None:
            return
        if s.find(interaction.user.id)[0]:
            await interaction.response.send_message("You're already in.", ephemeral=True)
            return
        s.pool.append(player_from(interaction.user))
        self.manager.save()
        self.manager.prepare_soon(s)
        await interaction.response.edit_message(embed=board(s), view=self)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, custom_id="scrim_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = await self._scrim(interaction)
        if s is None:
            return
        if not s.find(interaction.user.id)[0]:
            await interaction.response.send_message("You're not in this one.", ephemeral=True)
            return
        s.pool = [p for p in s.pool if p["id"] != interaction.user.id]
        self.manager.save()
        self.manager.prepare_soon(s)
        await interaction.response.edit_message(embed=board(s), view=self)


class ScrimManager:
    def __init__(self, client: discord.Client, owner_id: int, state_file: str):
        self.client = client
        self.owner_id = owner_id
        self.state_file = state_file
        self.scrims: dict[int, Scrim] = {}
        self.view = LobbyView(self)
        self.load()

    # --- state ---
    def load(self) -> None:
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                for data in json.load(f).values():
                    self.scrims[data["guild_id"]] = Scrim(data)
        except Exception:
            log.exception("Failed to load scrim state")

    def save(self) -> None:
        tmp = f"{self.state_file}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({str(g): s.to_dict() for g, s in self.scrims.items()}, f, indent=2)
            os.replace(tmp, self.state_file)
        except Exception:
            log.exception("Failed to save scrim state")

    # --- helpers ---
    async def owner_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(NO, ephemeral=True)
        return False

    async def active(self, interaction: discord.Interaction) -> Scrim | None:
        s = self.scrims.get(interaction.guild_id)
        if s is None:
            await interaction.response.send_message("No scrim is open. Start one with `/scrim start`.", ephemeral=True)
        return s

    def channel(self, s: Scrim):
        return self.client.get_channel(s.channel_id)

    async def refresh_board(self, s: Scrim) -> None:
        ch = self.channel(s)
        if ch is None or s.lobby_id is None:
            return
        try:
            view = self.view if s.phase == "lobby" else None
            await ch.get_partial_message(s.lobby_id).edit(embed=board(s), view=view)
        except discord.HTTPException:
            log.exception("Couldn't update the scrim board")

    # --- commands ---
    async def start(self, interaction: discord.Interaction) -> None:
        if not await self.owner_only(interaction):
            return
        old = self.scrims.get(interaction.guild_id)
        if old and old.phase != "done":
            await interaction.response.send_message(
                "A scrim is already open. Finish it, or `/scrim end` it first.", ephemeral=True)
            return
        s = Scrim({"guild_id": interaction.guild_id, "channel_id": interaction.channel_id})
        await interaction.response.send_message(embed=board(s), view=self.view)
        s.lobby_id = (await interaction.original_response()).id
        self.scrims[s.guild_id] = s
        self.save()

    async def add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await self.owner_only(interaction):
            return
        s = await self.active(interaction)
        if s is None:
            return
        if member.bot:
            await interaction.response.send_message("Bots can't play.", ephemeral=True)
            return
        if s.find(member.id)[0]:
            await interaction.response.send_message(f"{member.mention} is already in.", ephemeral=True)
            return
        if s.lock.locked():
            await interaction.response.send_message("Wait for the wheel to stop first.", ephemeral=True)
            return
        s.pool.append(player_from(member))
        if s.phase == "done":
            s.phase = "picking"  # teams weren't full: the new player can still be picked
        if s.phase == "picking" and s.next_team() is None:
            s.phase = "done"  # teams were full already: they go on the bench
        self.save()
        self.prepare_soon(s)
        where = "the bench" if s.phase == "done" else "the wheel"
        await interaction.response.send_message(f"Added {member.mention} to {where}.",
                                                allowed_mentions=discord.AllowedMentions.none())
        await self.refresh_board(s)

    async def remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await self.owner_only(interaction):
            return
        s = await self.active(interaction)
        if s is None:
            return
        if s.lock.locked():
            await interaction.response.send_message("Wait for the wheel to stop first.", ephemeral=True)
            return
        where, _ = s.find(member.id)
        if where is None:
            await interaction.response.send_message(f"{member.mention} isn't in this scrim.", ephemeral=True)
            return
        setattr(s, where, [p for p in getattr(s, where) if p["id"] != member.id])
        if s.last and s.last["id"] == member.id:
            s.last = None
        if s.phase == "done" and s.next_team():
            s.phase = "picking"  # a slot opened up and someone is still on the bench
        self.save()
        self.prepare_soon(s)
        place = {"pool": "the wheel", "blue": "🔵 Blue", "red": "🔴 Red"}[where]
        extra = " The next spin fills the spot." if where != "pool" and s.next_team() else ""
        await interaction.response.send_message(f"Removed {member.mention} from {place}.{extra}",
                                                allowed_mentions=discord.AllowedMentions.none())
        await self.refresh_board(s)

    async def end(self, interaction: discord.Interaction) -> None:
        if not await self.owner_only(interaction):
            return
        s = await self.active(interaction)
        if s is None:
            return
        if s.auto_running():
            s.auto.cancel()
        del self.scrims[s.guild_id]
        self.save()
        await interaction.response.send_message("Scrim ended.")
        ch = self.channel(s)
        if ch and s.lobby_id:
            try:
                e = board(s)
                e.title = "Scrim ended"
                await ch.get_partial_message(s.lobby_id).edit(embed=e, view=None)
            except discord.HTTPException:
                pass

    async def wheel(self, interaction: discord.Interaction, mode: str) -> None:
        if not await self.owner_only(interaction):
            return
        s = await self.active(interaction)
        if s is None:
            return
        if mode == "pause":
            if not s.auto_running():
                await interaction.response.send_message("No automatic draw is running.", ephemeral=True)
            elif s.pausing:
                await interaction.response.send_message("It's already pausing.", ephemeral=True)
            else:
                s.pausing = True  # the draw stops before its next spin; one already spinning finishes first
                await interaction.response.send_message(
                    "⏸️ Automatic draw paused. Spin yourself with `/scrim wheel`, or resume with "
                    "`/scrim wheel mode:continue`.")
            return
        if s.auto_running():
            await interaction.response.send_message(
                "An automatic draw is running. `/scrim wheel mode:pause` pauses it, `/scrim end` stops it.",
                ephemeral=True)
            return
        if s.lock.locked():
            await interaction.response.send_message("The wheel is still spinning.", ephemeral=True)
            return

        team = None
        if mode == "reroll":
            if not s.last:
                await interaction.response.send_message("There's no pick to reroll.", ephemeral=True)
                return
            team = s.last["team"]
            where, player = s.find(s.last["id"])
            if where != team:
                await interaction.response.send_message("The last pick isn't on a team anymore.", ephemeral=True)
                return
            s.team(team).remove(player)
            s.pool.append(player)  # back in the wheel: the reroll can land on them again
            s.last = None
            s.phase = "picking"
            self.save()
        elif s.next_team() is None:
            msg = "Both teams are full." if s.picked() else "Nobody has joined yet."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        if s.phase == "lobby":
            if len(s.pool) < 2:
                await interaction.response.send_message("At least 2 players need to join first.", ephemeral=True)
                return
            s.phase = "picking"  # lobby closes on the first spin
            self.save()
            await self.refresh_board(s)

        if mode in ("automatic", "continue"):
            word = "Resuming the automatic draw" if mode == "continue" else "🎡 Automatic draw"
            await interaction.response.send_message(
                f"{word}: spinning until the teams are full, {AUTO_GAP}s between picks. "
                f"`/scrim wheel mode:pause` pauses it.")
            s.pausing = False
            # picks go out as replies to this command (followups), which may carry the GIF even where Horaya
            # has no Attach Files permission; plain channel messages can't
            s.auto = asyncio.create_task(self.auto_draw(s, interaction.followup.send))
            return

        await interaction.response.defer(thinking=True)
        try:
            await self.spin(s, interaction.followup.send, team)
        except Exception:
            log.exception("Scrim spin failed")
            await interaction.followup.send("The wheel broke, try again.", ephemeral=True)

    async def auto_draw(self, s: Scrim, send) -> None:
        try:
            while not s.pausing and s.next_team() is not None and self.scrims.get(s.guild_id) is s:
                await self.spin(s, send)
                if s.next_team() is not None and not s.pausing:
                    await asyncio.sleep(AUTO_GAP)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Automatic scrim draw failed")
            try:
                await send("The automatic draw broke. Run `/scrim wheel` to carry on.")
            except discord.HTTPException:
                pass
        finally:
            s.pausing = False

    def prepare(self, s: Scrim) -> None:
        """Draw the next spin in the background now, so `/scrim wheel` doesn't sit on "thinking" while it renders."""
        team = s.next_team()
        if team is None or len(s.pool) < 2 or s.phase == "done":
            return
        key = s.spin_key(team)
        if s.prepared and s.prepared[0] == key:
            return
        order = s.pool[:]
        rng.shuffle(order)  # where a name sits on the wheel says nothing
        winner = rng.randrange(len(order))  # chosen now from exactly this wheel, so it's just as random
        task = asyncio.create_task(asyncio.to_thread(
            wheel.render_spin, [p["label"] for p in order], winner, team, key[2]))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())  # a failed draw is handled at spin time
        s.prepared = (key, order, winner, task)

    def prepare_soon(self, s: Scrim, delay: float = 3) -> None:
        """Prepare once things settle (people often join a few at a time)."""
        if s.prepare_timer and not s.prepare_timer.done():
            s.prepare_timer.cancel()

        async def later():
            await asyncio.sleep(delay)
            if self.scrims.get(s.guild_id) is s and not s.lock.locked():
                self.prepare(s)
        s.prepare_timer = asyncio.create_task(later())

    async def spin(self, s: Scrim, send, team: str | None = None) -> None:
        """One pick: post the spinning wheel, then reveal who it landed on and update the board."""
        async with s.lock:
            team = team or s.next_team()
            key = s.spin_key(team)
            ready = s.prepared if s.prepared and s.prepared[0] == key else None
            s.prepared = None
            if ready:  # drawn ahead for this exact wheel
                _, order, winner, render = ready
            else:
                order = s.pool[:]
                rng.shuffle(order)
                winner = rng.randrange(len(order))
                render = None
            player = order[winner]
            s.pool.remove(player)
            s.team(team).append(player)
            s.last = {"id": player["id"], "team": team}
            pick_no = s.picked()
            done = s.next_team() is None
            if done:
                s.phase = "done"
            self.save()

            head = f"{EMOJI[team]} Pick {pick_no}/{s.goal()} for **{team.title()}**"
            reveal = discord.Embed(color=COLOR[team], description=f"{EMOJI[team]} <@{player['id']}> joins "
                                   f"**{team.title()}**! {rng.choice(HYPE)}\n\n{teams_text(s)}")
            reveal.set_thumbnail(url=player["avatar"])
            reveal.set_footer(text=f"{len(s.pool)} left in the wheel")
            quiet = discord.AllowedMentions.none()  # names in pick messages never ping; only the final one does
            if len(order) == 1:
                await send(content=f"{head}: last one left, no spin needed.", embed=reveal, allowed_mentions=quiet)
            else:
                try:
                    if render is None:
                        render = asyncio.to_thread(wheel.render_spin, [p["label"] for p in order], winner, team,
                                                   pick_no)
                    gif, _, seconds = await render
                except Exception:
                    log.exception("Wheel render failed, revealing without it")
                    await send(content=f"{head}: 🎲 rolling...", embed=reveal, allowed_mentions=quiet)
                else:
                    msg = await send(content=f"{head} - spinning...", file=discord.File(io.BytesIO(gif), "wheel.gif"))
                    if not done:
                        self.prepare(s)  # draw the next spin while this one plays
                    # the GIF only starts once phones have downloaded it, so give them a moment before the reveal
                    await asyncio.sleep(seconds + 1.5)
                    # the GIF stays as it is: its last frame (winner lit) is held for 10 minutes. Swapping it for
                    # a still image made Discord flash the old GIF back for a moment.
                    await msg.edit(content=head, embed=reveal, allowed_mentions=quiet)
        if self.scrims.get(s.guild_id) is not s:
            return  # ended while it was spinning
        await self.refresh_board(s)
        if done:
            await self.final(s, send)
        else:
            self.prepare(s)  # no-op if it's already drawn for this wheel

    async def final(self, s: Scrim, send) -> None:
        """The only message that pings: both teams listed, everyone picked gets a ping."""
        text = f"🏁 **Final teams** - GLHF!\n\n{teams_text(s)}"
        if s.pool:
            text += "\n\n🪑 **Bench**: " + " ".join(f"<@{p['id']}>" for p in s.pool)
        picked = [discord.Object(p["id"]) for p in s.blue + s.red]
        await send(content=text[:2000],
                   allowed_mentions=discord.AllowedMentions(users=picked, roles=False, everyone=False))


def setup(client: discord.Client, tree: app_commands.CommandTree, owner_id: int, state_file: str) -> ScrimManager:
    manager = ScrimManager(client, owner_id, state_file)
    client.add_view(manager.view)
    group = app_commands.Group(name="scrim", description="Fun scrim team draw", guild_only=True)

    @group.command(name="start", description="Open a scrim lobby with a Join button")
    async def start(interaction: discord.Interaction):
        await manager.start(interaction)

    @group.command(name="wheel", description="Spin the wheel for the next pick")
    @app_commands.describe(mode="one pick (default), automatic, pause or continue an automatic draw, or reroll")
    @app_commands.choices(mode=[app_commands.Choice(name="one pick", value="one"),
                                app_commands.Choice(name="automatic", value="automatic"),
                                app_commands.Choice(name="pause automatic", value="pause"),
                                app_commands.Choice(name="continue automatic", value="continue"),
                                app_commands.Choice(name="reroll last pick", value="reroll")])
    async def spin(interaction: discord.Interaction, mode: app_commands.Choice[str] | None = None):
        await manager.wheel(interaction, mode.value if mode else "one")

    @group.command(name="add", description="Put someone in the wheel")
    @app_commands.describe(member="Who to add")
    async def add(interaction: discord.Interaction, member: discord.Member):
        await manager.add(interaction, member)

    @group.command(name="remove", description="Take someone out of the wheel or their team")
    @app_commands.describe(member="Who to take out")
    async def remove(interaction: discord.Interaction, member: discord.Member):
        await manager.remove(interaction, member)

    @group.command(name="end", description="Close the scrim (stops an automatic draw)")
    async def end(interaction: discord.Interaction):
        await manager.end(interaction)

    tree.add_command(group)
    return manager
