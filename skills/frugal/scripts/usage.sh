#!/bin/sh
# Token usage of a Claude Code session, summed from its transcript. Main thread only: subagents keep their own transcripts.
# Usage: sh usage.sh [transcript.jsonl]   (default: the most recently written transcript, i.e. the current session)
f=${1:-$(ls -t "$HOME"/.claude/projects/*/*.jsonl 2>/dev/null | head -1)}
[ -f "$f" ] || { echo "Usage: unavailable (no transcript found)"; exit 1; }
# A response streams as several lines sharing one msg id; keep the last line per id.
grep '"type":"assistant"' "$f" | awk '
function num(k) { return match($0, "\"" k "\":[0-9]+") ? substr($0, RSTART + length(k) + 3, RLENGTH - length(k) - 3) + 0 : 0 }
match($0, /"id":"msg_[^"]*"/) {
  id = substr($0, RSTART, RLENGTH)
  if (!(id in seen)) { seen[id] = 1; order[++n] = id }
  inp[id] = num("input_tokens"); cw[id] = num("cache_creation_input_tokens"); cr[id] = num("cache_read_input_tokens"); out[id] = num("output_tokens")
}
END {
  if (!n) { print "Usage: unavailable (no responses yet)"; exit }
  for (i = 1; i <= n; i++) { id = order[i]; I += inp[id]; W += cw[id]; R += cr[id]; O += out[id] }
  last = order[n]
  printf "Usage: context %.1fk tokens, %d calls; input %.1fk uncached + %.1fk cache write + %.1fk cache read; output %.1fk\n", (inp[last] + cw[last] + cr[last]) / 1000, n, I / 1000, W / 1000, R / 1000, O / 1000
}'
