#!/usr/bin/env bash
# Narrate the CashCycle dashboard tour with the macOS `say` command.
#
#   ./scripts/tour_say.sh                 # speak each step, wait for Return before the next (click Next in the tour as you go)
#   ./scripts/tour_say.sh --auto          # speak every step with a short pause, hands-free
#   ./scripts/tour_say.sh --out tour.m4a  # write the whole narration to an audio file for a Loom voice-over
#   VOICE=Daniel RATE=175 ./scripts/tour_say.sh --auto
#
# The text comes from dashboard/tour.json, the same script the dashboard's built-in voice reads.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v say >/dev/null || { echo "This needs macOS (the 'say' command)."; exit 1; }
VOICE="${VOICE:-Daniel}"; RATE="${RATE:-180}"; PAUSE="${PAUSE:-1.2}"
say -v '?' | grep -q "^$VOICE " || { echo "Voice '$VOICE' not installed, using the system default."; VOICE=""; }
VOPT=(); [ -n "$VOICE" ] && VOPT=(-v "$VOICE")
MODE="step"; OUT=""
while [ $# -gt 0 ]; do case "$1" in --auto) MODE="auto";; --out) MODE="file"; OUT="$2"; shift;; *) echo "unknown option $1"; exit 2;; esac; shift; done

steps() { python3 -c 'import json,sys; [print(f"{t[\"title\"]}\t{t[\"say\"]}") for t in json.load(open("dashboard/tour.json"))]'; }

if [ "$MODE" = "file" ]; then
  TEXT="$(steps | cut -f2 | paste -sd ' ' - | sed 's/\. /. [[slnc 900]] /g')"
  case "$OUT" in *.aiff) say "${VOPT[@]}" -r "$RATE" -o "$OUT" "$TEXT";; *) say "${VOPT[@]}" -r "$RATE" -o "$OUT" --data-format=aac "$TEXT";; esac
  echo "Wrote $OUT"; exit 0
fi

n=0; total=$(steps | wc -l | tr -d ' ')
steps | while IFS=$'\t' read -r title text; do
  n=$((n + 1))
  printf '\n[%d/%d] %s\n' "$n" "$total" "$title"
  say "${VOPT[@]}" -r "$RATE" "$text"
  if [ "$MODE" = "step" ] && [ "$n" -lt "$total" ]; then read -r -p "  Press Return, then click Next in the tour... " _ </dev/tty; else sleep "$PAUSE"; fi
done
echo; echo "Tour finished."
