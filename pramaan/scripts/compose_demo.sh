#!/usr/bin/env bash
# Compose the demo film from the cards and screen beats.
#
# Run `scripts/record_demo.py` first; this only assembles what it produced.
#
# ## Shape
#
# Card, then the screen it describes. The card sets up what to look for and the
# footage delivers it — a film that cuts straight between screens leaves a
# viewer decoding the interface instead of following the argument.
#
# ## Two deliberate choices
#
# **No music.** A pitch film with a stock bed sounds like every other pitch film,
# and the presenter narrates over this live. Silence also means the file can be
# dropped into a deck without fighting a voiceover.
#
# **Motion on cards only.** A slow push on a typographic card reads as
# intentional. The same push on a screen recording makes the interface look like
# a photograph of software rather than software.
#
# Output: build/video/PRAMAAN_demo.mp4 — 1080p, H.264, yuv420p, faststart, so it
# plays in Keynote, PowerPoint, QuickTime and a browser without transcoding.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
V="$ROOT/build/video"
CARDS="$V/cards"
CLIPS="$V/clips"
SEG="$V/seg"
OUT="$V/PRAMAAN_demo.mp4"

FPS=30
XF=0.5          # crossfade seconds
CARD_HOLD=4.0   # seconds a card holds before its crossfade

command -v ffmpeg >/dev/null || { echo "ffmpeg not found" >&2; exit 1; }
[ -d "$CARDS" ] || { echo "run scripts/record_demo.py first" >&2; exit 1; }

rm -rf "$SEG"; mkdir -p "$SEG"

# --- cards -------------------------------------------------------------------
# A 4 % push over the hold. Small on purpose: enough to feel alive, not enough
# to notice as an effect.
card() {
  local name="$1" dur="$2"
  local frames=$(python3 -c "print(int($dur*$FPS))")
  ffmpeg -v error -loop 1 -i "$CARDS/$name.png" -frames:v "$frames" \
    -vf "scale=2112:-1,zoompan=z='min(1.04,1+0.04*on/${frames})':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=$FPS,format=yuv420p" \
    -c:v libx264 -preset medium -crf 17 -pix_fmt yuv420p -r "$FPS" \
    "$SEG/$name.mp4" -y
  echo "  card  $name  ${dur}s"
}

# --- screen beats ------------------------------------------------------------
# Re-encoded to a common codec/rate so concat and xfade have nothing to guess
# at. `setpts` retimes rather than drops frames — playing a recording slightly
# slower gives the eye time on dense screens without visible stutter.
#
# `focus` optionally pushes into a measured region. Two beats need it: the
# expanded evidence tree and the temporal chart are the only places where the
# viewer is meant to read the evidence itself rather than take in the shape of
# the screen. Everywhere else the full frame is the point, and a crop would hide
# the context that makes the screen legible as a product.
#
# Region geometry is measured from the live page, not eyeballed. Tried on the
# chart too and reverted: the crop clipped the ribbon caption and the axis, so a
# zoom meant to aid reading removed the labels being read.
beat() {
  local name="$1" speed="${2:-1.0}" focus="${3:-}"
  local src; src=$(ls "$CLIPS/$name"/*.webm | head -1)
  local vf="setpts=${speed}*PTS,scale=1920:1080:flags=lanczos"
  if [ -n "$focus" ]; then
    # focus = "cx,cy,zoom" — centre of interest in delivery-frame pixels.
    local cx cy z cw ch cx0 cy0
    IFS=, read -r cx cy z <<< "$focus"
    cw=$(python3 -c "print(int(1920/$z)//2*2)")
    ch=$(python3 -c "print(int(1080/$z)//2*2)")
    cx0=$(python3 -c "print(max(0, min(1920-$cw, int($cx-$cw/2))))")
    cy0=$(python3 -c "print(max(0, min(1080-$ch, int($cy-$ch/2))))")
    vf="setpts=${speed}*PTS,scale=1920:1080:flags=lanczos,crop=$cw:$ch:$cx0:$cy0,scale=1920:1080:flags=lanczos"
  fi
  ffmpeg -v error -i "$src" -vf "$vf,fps=$FPS,format=yuv420p" \
    -c:v libx264 -preset medium -crf 19 -pix_fmt yuv420p -r "$FPS" \
    "$SEG/$name.mp4" -y
  local d; d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SEG/$name.mp4")
  printf "  beat  %-10s %.2fs%s\n" "$name" "$d" "${focus:+  focus $focus}"
}

echo "encoding segments"
card 00-open      4.6
card 01-thesis    4.4
card 02-register  4.2
beat register     1.06
card 03-verdict   4.4
beat claim        1.06
card 04-disk      4.0
beat disk         1.30
card 05-terrain   4.4
beat terrain      1.06  1042,780,1.5
beat dissent      1.06
card 06-payoff    5.0
# No focus on the chart: at 1.25x the crop clipped the 'rabi controls n=12 ·
# site INSIDE band' caption and the y-axis, which are the two things the beat
# exists to show. The chart already fills most of the frame.
beat temporal     1.10
card 07-refusal   4.8
card 08-method    4.2
beat method       1.02
card 09-audit     4.2
beat recompute    1.00
card 10-close     5.2

# Order is the argument. Card, then the screen it set up.
ORDER=(
  00-open 01-thesis
  02-register register
  03-verdict  claim
  04-disk     disk
  05-terrain  terrain  dissent
  06-payoff   temporal 07-refusal
  08-method   method
  09-audit    recompute
  10-close
)

# --- crossfade chain ---------------------------------------------------------
# xfade needs an explicit offset per join, which is the running sum of the
# preceding durations minus the accumulated fade overlap.
echo "building crossfade chain"
inputs=(); filter=""; prev="0:v"; offset=0; i=0
for name in "${ORDER[@]}"; do
  inputs+=(-i "$SEG/$name.mp4")
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SEG/$name.mp4")
  if [ "$i" -eq 0 ]; then
    offset=$(python3 -c "print(round($d - $XF, 3))")
  else
    filter+="[$prev][$i:v]xfade=transition=fade:duration=$XF:offset=$offset[v$i];"
    prev="v$i"
    offset=$(python3 -c "print(round($offset + $d - $XF, 3))")
  fi
  i=$((i+1))
done
filter="${filter%;}"

echo "rendering $OUT"
ffmpeg -v error -stats "${inputs[@]}" \
  -filter_complex "$filter" -map "[$prev]" \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -r "$FPS" \
  -movflags +faststart -an "$OUT" -y

dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT")
size=$(du -h "$OUT" | cut -f1)
printf "  full   %-22s %.1fs  %s\n" "$(basename "$OUT")" "$dur" "$size"

# --- short cut ---------------------------------------------------------------
# A 45-second version for submission portals and messaging apps, where the full
# film will not be watched to the end. Same segments, fewer of them: the three
# beats that carry the argument on their own, and the one number that matters.
SHORT="$V/PRAMAAN_demo_45s.mp4"
SHORT_ORDER=(01-thesis 02-register register 06-payoff temporal 07-refusal 09-audit recompute 10-close)

inputs=(); filter=""; prev="0:v"; offset=0; i=0
for name in "${SHORT_ORDER[@]}"; do
  inputs+=(-i "$SEG/$name.mp4")
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SEG/$name.mp4")
  if [ "$i" -eq 0 ]; then
    offset=$(python3 -c "print(round($d - $XF, 3))")
  else
    filter+="[$prev][$i:v]xfade=transition=fade:duration=$XF:offset=$offset[v$i];"
    prev="v$i"
    offset=$(python3 -c "print(round($offset + $d - $XF, 3))")
  fi
  i=$((i+1))
done
filter="${filter%;}"

ffmpeg -v error "${inputs[@]}" -filter_complex "$filter" -map "[$prev]" \
  -c:v libx264 -preset slow -crf 19 -pix_fmt yuv420p -r "$FPS" \
  -movflags +faststart -an "$SHORT" -y

sdur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SHORT")
ssize=$(du -h "$SHORT" | cut -f1)
printf "  short  %-22s %.1fs  %s\n" "$(basename "$SHORT")" "$sdur" "$ssize"
echo
echo "no audio track by design — the presenter narrates over it live, and a"
echo "stock music bed is what makes a pitch film sound like every other one."
