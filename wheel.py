"""Draws the scrim wheel: a spinning GIF that lands on a chosen slice, and a still image of where it stopped.

No Discord code here, so it can be tried offline. Uses Pillow's built-in font (Pillow 10.1+), so no font file.
"""
import io
import math
import random

from PIL import Image, ImageDraw, ImageFont

SIZE = 360
RADIUS = 160
CX, CY = SIZE // 2, SIZE // 2 + 8
FPS = 15
SUPERSAMPLE = 2  # the wheel is drawn at 2x and shrunk, for smooth edges
SLICE_COLORS = ["#f4b400", "#7e57c2", "#26a69a", "#ef6c00", "#42a5f5", "#ec407a", "#9ccc65", "#5c6bc0"]
TEAM_COLORS = {"blue": "#2f80ed", "red": "#eb4d4b"}
BG = "#1e1f22"
LABEL_MAX = 12
HOLD_SECONDS = 6  # the GIF shows the stopped wheel this long after the spin, so it never loops before the swap

_fonts: dict[int, ImageFont.FreeTypeFont] = {}
_drawable: dict[str, bool] = {}


def font(px: int) -> ImageFont.FreeTypeFont:
    if px not in _fonts:
        _fonts[px] = ImageFont.load_default(size=px)
    return _fonts[px]


def _can_draw(ch: str) -> bool:
    """False for characters the built-in font only has a blank or box for (emoji, Japanese, fancy unicode)."""
    if ch not in _drawable:
        # what the font shows for characters it doesn't have: a box, or nothing at all
        missing = {_glyph("͸"), _glyph("一"), _glyph("\U000f0000"), bytes(40 * 40)}
        _drawable[ch] = ch.isspace() or _glyph(ch) not in missing
    return _drawable[ch]


def _glyph(ch: str) -> bytes:
    img = Image.new("L", (40, 40))
    ImageDraw.Draw(img).text((8, 8), ch, font=font(20), fill=255)
    return img.tobytes()


def label(name: str, username: str) -> str:
    """The name to print on a slice: the profile name if the font can draw it, else the plain username."""
    text = name.strip() if name and all(_can_draw(c) for c in name.strip()) else username
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"


def _dim(hex_color: str) -> tuple:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (r * 2 // 5 + 12, g * 2 // 5 + 12, b * 2 // 5 + 12, 255)


def _wheel(labels: list[str], lit: int | None = None) -> Image.Image:
    """The wheel at rest: slice 0 starts at 12 o'clock and they go clockwise. `lit` dims every other slice."""
    s = RADIUS * SUPERSAMPLE
    img = Image.new("RGBA", (2 * s + 4, 2 * s + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    n = len(labels)
    width = 360 / n
    f = font(int(min(22, 300 / n + 6) * SUPERSAMPLE))
    for i, text in enumerate(labels):
        start = -90 + i * width
        color = SLICE_COLORS[i % len(SLICE_COLORS)]
        if n == len(SLICE_COLORS) + 1 and i == n - 1:
            color = SLICE_COLORS[2]  # don't put two slices of the same colour next to each other
        fill = color if lit is None or i == lit else _dim(color)
        d.pieslice([2, 2, 2 * s + 2, 2 * s + 2], start, start + width, fill=fill, outline=BG, width=2 * SUPERSAMPLE)
        tw = int(d.textlength(text, font=f)) + 8
        th = int(f.size * 1.4)
        t = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        ink = "white" if lit is None or i == lit else "#9a9a9a"
        ImageDraw.Draw(t).text((4, th // 2), text, font=f, fill=ink, anchor="lm",
                               stroke_width=SUPERSAMPLE, stroke_fill="#00000080")
        mid = start + width / 2
        rotated = t.rotate(-mid, expand=True, resample=Image.BICUBIC)  # reads outward from the hub
        x = s + 2 + s * 0.58 * math.cos(math.radians(mid)) - rotated.width / 2
        y = s + 2 + s * 0.58 * math.sin(math.radians(mid)) - rotated.height / 2
        img.alpha_composite(rotated, (int(x), int(y)))
    return img.resize((2 * RADIUS + 2, 2 * RADIUS + 2), Image.LANCZOS)


def _overlay(team: str, pick_no: int) -> Image.Image:
    """Pointer at the top and the hub in the middle, in the team's colour."""
    o = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(o)
    c = TEAM_COLORS[team]
    d.polygon([(CX - 15, CY - RADIUS - 15), (CX + 15, CY - RADIUS - 15), (CX, CY - RADIUS + 13)],
              fill=c, outline="white", width=2)
    d.ellipse([CX - 36, CY - 36, CX + 36, CY + 36], fill=BG, outline=c, width=4)
    d.text((CX, CY - 8), team.upper(), font=font(18), fill=c, anchor="mm")
    d.text((CX, CY + 12), f"pick {pick_no}", font=font(13), fill="#dddddd", anchor="mm")
    return o


def _flat(wheel: Image.Image) -> Image.Image:
    """The wheel on a solid square of background, so frames can rotate plain RGB (much faster than RGBA)."""
    sq = Image.new("RGB", wheel.size, BG)
    sq.paste(wheel, (0, 0), wheel)
    return sq


def _frame(base, wheel, top, angle) -> Image.Image:
    im = base.copy()
    # the corners a rotation uncovers get the background colour, and the round wheel never leaves its square
    r = wheel.rotate(-angle, resample=Image.BILINEAR, fillcolor=BG)
    im.paste(r, (CX - r.width // 2, CY - r.height // 2))
    im.paste(top, (0, 0), top)
    return im


def render_spin(labels: list[str], winner: int, team: str, pick_no: int,
                rng: random.Random | None = None) -> tuple[bytes, bytes, float]:
    """Spin that stops with `labels[winner]` under the pointer.

    Returns (gif, png of where it stopped with the winner lit, seconds the spin lasts).
    """
    rng = rng or random.Random()
    n = len(labels)
    width = 360 / n
    seconds = rng.uniform(4.5, 6.0)
    # where in the winning slice the pointer stops; about a third stop a hair from the edge (a near miss)
    if rng.random() < 0.35:
        u = rng.choice([rng.uniform(0.04, 0.12), rng.uniform(0.88, 0.96)])
    else:
        u = rng.uniform(0.2, 0.8)
    land = (winner + u) * width  # angle clockwise from 12 o'clock that ends under the pointer
    start = rng.uniform(0, 360)
    total = rng.randint(4, 6) * 360 + ((-land - start) % 360)

    wheel = _flat(_wheel(labels))
    top = _overlay(team, pick_no)
    base = Image.new("RGB", (SIZE, SIZE), BG)
    palette = _frame(base, wheel, top, start).quantize(colors=96, method=Image.Quantize.MEDIANCUT)

    def quantize(im):
        return im.quantize(palette=palette, dither=Image.Dither.NONE)

    # fast frames: rotate the already-quantized wheel with nearest-neighbour (no smoothing, no per-frame
    # colour matching). At that speed nobody sees the rough edges; the slow end is drawn smooth.
    wheel_p, base_p, top_p = quantize(wheel), quantize(base), quantize(Image.alpha_composite(
        Image.new("RGBA", (SIZE, SIZE), BG), top).convert("RGB"))
    bg_index = base_p.getpixel((0, 0))
    top_mask = top.getchannel("A").point(lambda a: 255 if a > 127 else 0)

    def fast_frame(angle):
        im = base_p.copy()
        r = wheel_p.rotate(-angle, resample=Image.NEAREST, fillcolor=bg_index)
        im.paste(r, (CX - r.width // 2, CY - r.height // 2))
        im.paste(top_p, (0, 0), top_mask)
        return im

    frames, durations = [], []
    step_ms = 1000 // FPS
    steps = int(seconds * FPS)
    last = None
    for k in range(steps + 1):
        p = k / steps
        rot = total * (1 - (1 - p) ** 4)  # ease-out: fast, then a long crawl past the last few names
        if last is not None and rot - last < 0.6 and k != steps:
            durations[-1] += step_ms  # it barely moved: show the last frame longer instead (smaller file)
            continue
        fast = last is not None and rot - last > 6
        frames.append(fast_frame(start + rot) if fast else quantize(_frame(base, wheel, top, start + rot)))
        durations.append(step_ms)
        last = rot
    spin_seconds = sum(durations) / 1000
    still = _frame(base, _flat(_wheel(labels, lit=winner)), top, start + total)
    # Discord ignores a long last-frame delay and loops the GIF, so the wheel would start spinning again
    # before the bot swaps in the still. Instead the GIF ends with HOLD_SECONDS of real frames of the result
    # (one corner pixel flickers between two near-identical colours so the encoder can't merge them).
    lit = quantize(still)
    corner = lit.getpixel((0, 0))
    pal = lit.getpalette()[: 3 * 256]
    colors = [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
    near = min((i for i in range(len(colors)) if i != corner),
               key=lambda i: sum((a - b) ** 2 for a, b in zip(colors[i], colors[corner])))
    for i in range(int(HOLD_SECONDS * 2)):
        f = lit.copy()
        f.putpixel((0, 0), corner if i % 2 == 0 else near)
        frames.append(f)
        durations.append(500)

    gif = io.BytesIO()
    # optimize=False skips Pillow's extra size pass, which was most of the CPU (free hosts stop busy servers)
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0,
                   optimize=False)
    png = io.BytesIO()
    still.save(png, format="PNG")
    return gif.getvalue(), png.getvalue(), spin_seconds
