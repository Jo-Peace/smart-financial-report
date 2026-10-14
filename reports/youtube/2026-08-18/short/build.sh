#!/bin/bash
# ChatCut-style recut: 8/18 長片開場鉤子 → 約 29 秒 9:16
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ASSETS="$DIR/assets"
OUT="$DIR/short_9x16.mp4"
FONTDIR="/System/Library/Fonts"

vf_still() {
  # fill 1080x1920, slow zoom
  echo "scale=1400:2489:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(1.08\\,zoom+0.0006)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=24,format=yuv420p"
}

ffmpeg -y \
  -loop 1 -t 7.40 -i "$ASSETS/still_fuse.jpg" \
  -loop 1 -t 13.48 -i "$ASSETS/still_volume.jpg" \
  -loop 1 -t 8.44 -i "$ASSETS/still_122.jpg" \
  -i "$ASSETS/hook.m4a" \
  -filter_complex "
[0:v]$(vf_still)[v0];
[1:v]$(vf_still)[v1];
[2:v]$(vf_still)[v2];
[v0][v1][v2]concat=n=3:v=1:a=0[vcat];
[vcat]ass='$DIR/captions.ass':fontsdir='$FONTDIR'[vout]
" \
  -map "[vout]" -map 3:a \
  -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
  -c:a aac -b:a 192k \
  -shortest -movflags +faststart \
  "$OUT"

ffprobe -v error -show_entries format=duration -show_entries stream=width,height -of default=noprint_wrappers=1 "$OUT"
echo "WROTE $OUT"
