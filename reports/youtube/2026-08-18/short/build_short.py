#!/usr/bin/env python3
"""ChatCut-style recut: 8/18 hook VO + stills → ~29s 9:16 short."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DIR = Path(__file__).resolve().parent
ASSETS = DIR / "assets"
WORK = DIR / "_work"
OUT = DIR / "short_9x16.mp4"
W, H = 1080, 1920
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"

CUES = [
    (0.00, 1.52, "今天大盤狂殺"),
    (1.52, 7.40, "欣興外資狂掃 122.2億"),
    (7.40, 13.24, "臻鼎卻被提款 41.5億"),
    (13.24, 20.88, "成交量連前20名都沒進"),
    (20.88, 23.64, "這不是普通輪動"),
    (23.64, 29.32, "122.2億砸出來的反差"),
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT, size, index=0)
    except OSError:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Songti.ttc", size, index=0)


def cover_crop(src: Path, dest: Path) -> None:
    im = Image.open(src).convert("RGB")
    scale = max(W / im.width, H / im.height)
    nw, nh = int(im.width * scale), int(im.height * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left, top = (nw - W) // 2, (nh - H) // 2
    im.crop((left, top, left + W, top + H)).save(dest, quality=92)


def caption_png(text: str, dest: Path) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    tag_font = load_font(36)
    hook_font = load_font(64)
    draw.rectangle((0, 0, W, 110), fill=(10, 16, 28, 200))
    draw.text((W // 2, 55), "8/18 台股盤後", font=tag_font, fill=(255, 214, 0, 255), anchor="mm")
    box_h = 280
    y0 = H - box_h - 80
    draw.rounded_rectangle((40, y0, W - 40, y0 + box_h), radius=28, fill=(8, 10, 16, 220))
    # wrap
    words = text
    font = hook_font
    if font.getlength(words) > W - 140:
        font = load_font(52)
    draw.text((W // 2, y0 + box_h // 2), words, font=font, fill=(255, 255, 255, 255), anchor="mm")
    img.save(dest)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    WORK.mkdir(exist_ok=True)
    cover_crop(ASSETS / "still_fuse.jpg", WORK / "bg_a.jpg")
    cover_crop(ASSETS / "still_volume.jpg", WORK / "bg_b.jpg")
    cover_crop(ASSETS / "still_122.jpg", WORK / "bg_c.jpg")

    clips = []
    for i, (start, end, text) in enumerate(CUES):
        cap = WORK / f"cap_{i:02d}.png"
        caption_png(text, cap)
        dur = round(end - start, 2)
        if start < 7.40:
            bg = WORK / "bg_a.jpg"
        elif start < 20.88:
            bg = WORK / "bg_b.jpg"
        else:
            bg = WORK / "bg_c.jpg"
        clip = WORK / f"clip_{i:02d}.mp4"
        run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-t",
                str(dur),
                "-i",
                str(bg),
                "-loop",
                "1",
                "-t",
                str(dur),
                "-i",
                str(cap),
                "-filter_complex",
                "overlay=0:0,format=yuv420p",
                "-r",
                "24",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-an",
                str(clip),
            ]
        )
        clips.append(clip)

    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in clips), encoding="utf-8")
    silent = WORK / "silent.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            str(silent),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent),
            "-i",
            str(ASSETS / "hook.m4a"),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(OUT),
        ]
    )
    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=width,height",
            "-of",
            "default=noprint_wrappers=1",
            str(OUT),
        ],
        text=True,
    )
    print(probe)
    print(f"WROTE {OUT}")


if __name__ == "__main__":
    main()
