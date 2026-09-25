"""Render assets/demo.gif + assets/demo.png from REAL devline output.

Runs the actual CLI in a sandbox: install-hook -> two records -> friday.
Cumulative terminal frames with the family PIL recipe.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

BG = (13, 17, 23)
FG = (201, 209, 217)
PROMPT = (108, 118, 133)
SUCCESS = (126, 231, 135)
KEY = (121, 192, 255)

DURATIONS = [1000, 650, 650, 650, 950]

FONT_CANDIDATES = [
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def find_font():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("no monospace TTF font found")


def run(args, env):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    return (p.stdout + p.stderr).strip()


def line_color(ln: str) -> tuple:
    if ln.startswith(("created", "installed", "already", "Friday", "+")):
        return SUCCESS
    if ln.startswith(("  ",)) and "events" in ln:
        return KEY
    if ln.startswith(("by project:", "nothing recorded")):
        return PROMPT
    if ln[:2].isdigit() and ":" in ln[:8]:
        return FG
    return FG


def main():
    from PIL import Image, ImageDraw, ImageFont

    work = Path(tempfile.mkdtemp(prefix="devline-render-"))
    env = {**os.environ, "HOME": str(work), "DEVLINE_DB": str(work / "tl.sqlite")}
    try:
        steps = []
        out = run(["devline", "install-hook"], env)
        steps.append([("$ devline install-hook", PROMPT)] +
                     [(ln, line_color(ln)) for ln in out.splitlines()])
        out = run(["devline", "record", "--source", "shell", "--command",
                   "pytest -q", "--cwd", str(ROOT), "--exit", "0"], env)
        frame = [("$ devline record --source shell --command 'pytest -q'", PROMPT)]
        if out:
            frame.append((out, SUCCESS))
        steps.append(frame)
        out = run(["devline", "record", "--source", "shell", "--command",
                   "git push origin main", "--cwd", str(ROOT), "--exit", "0"], env)
        frame = [("$ devline record --command 'git push origin main'", PROMPT)]
        if out:
            frame.append((out, SUCCESS))
        steps.append(frame)
        out = run(["devline", "friday"], env)
        steps.append([("$ devline friday", PROMPT)] +
                     [(ln, line_color(ln)) for ln in out.splitlines()])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    font = ImageFont.truetype(find_font(), 15)
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    max_w, max_lines = 0, 0
    wrapped = []
    for frame in steps:
        wf = []
        for text, col in frame:
            while tmp.textlength(text, font=font) > 760 and len(text) > 40:
                text = text[: len(text) - 10] + "\u2026"
            wf.append((text, col))
            max_w = max(max_w, int(tmp.textlength(text, font=font)))
        wrapped.append(wf)
        max_lines += len(wf)  # cumulative worst case: final frame = all lines

    W = max_w + 48
    H = max_lines * 22 + 36
    frames, cum = [], []
    for wf in wrapped:
        cum = cum + wf
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        y = 18
        for text, col in cum:
            d.text((24, y), text, font=font, fill=col)
            y += 22
        frames.append(img)

    durs = list(DURATIONS)
    while len(durs) < len(frames):
        durs.append(700
                    )
    durs = durs[: len(frames)]

    ASSETS.mkdir(parents=True, exist_ok=True)
    gif = ASSETS / "demo.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=durs,
                   loop=0, palette=Image.ADAPTIVE, colors=200, optimize=True)
    frames[0].save(ASSETS / "demo.png")
    print("gif ok", len(frames), "frames", gif.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
