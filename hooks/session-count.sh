#!/bin/sh
# Runs on every user prompt; prints nothing unless /frugal is active in this session.
# - First code-looking prompt (clear signals only; a miss costs one Read, since SKILL.md has the model read code.md): injects code.md, so the model has the code rules without spending a turn on Read.
# - 10th prompt, every 15th after it, and the prompt after a compaction: injects core.md and session.md unless already
#   in context (a skill cannot count messages reliably; a hook can); with both loaded, a round gets one reminder line.
# - Context past each 150k tokens: injects the handoff rule (message count is a poor proxy for what each message re-sends).
# - Pasted handoff: a /frugal line inside pasted text is not run as a command, so tell the model to load the skill until it does.
# Keeps small per-session marker files in CLAUDE_PLUGIN_DATA; prune by age if that ever matters.
input=$(cat)
field() { printf '%s' "$input" | sed -n "s/.*\"$1\" *: *\"\([^\"]*\)\".*/\1/p"; }
sid=$(field session_id)
[ -n "$sid" ] || exit 0
# Background-task notifications arrive as prompts too; only count what the user typed.
printf '%s' "$input" | grep -q '<task-notification>' && exit 0
dir="${CLAUDE_PLUGIN_DATA:-${TMPDIR:-/tmp}}"
mkdir -p "$dir" 2>/dev/null
# Whole prompt, escaped quotes included (field stops at the first one).
prompt=$(printf '%s' "$input" | sed -nE 's/.*"prompt" *: *"(([^"\\]|\\.)*)".*/\1/p')
transcript=$(field transcript_path | sed 's/\\\\/\//g')
# A plugin reinstall wipes CLAUDE_PLUGIN_DATA, so never count below the typed prompts in the transcript.
typed=$(grep '"type":"user"' "$transcript" 2>/dev/null | grep '"origin":{"kind":"human"}' | grep -v '"isMeta":true' | grep -cvE '"(content|text)":"<(command-name|local-command)')
n=$(( $(cat "$dir/count-$sid" 2>/dev/null || echo 0) + 1 ))
[ "${typed:-0}" -gt "$n" ] && n=$typed
echo "$n" > "$dir/count-$sid"
loaded() { grep -qE 'command-name>/frugal|"skill":"frugal' "$transcript" 2>/dev/null; }
# A pasted handoff counts too: "/frugal" starting a line (a JSON-escaped newline) in the prompt or the transcript.
pasted() { printf '%s' "$prompt" | grep -q '\\n/frugal' || grep -q '\\n/frugal' "$transcript" 2>/dev/null; }
active() { printf '%s' "$prompt" | grep -q '/frugal' || loaded || pasted; }
root=$(printf '%s' "$CLAUDE_PLUGIN_ROOT" | sed 's/\\/\//g')
skill="$root/skills/frugal"
if pasted && ! loaded; then
  args=$({ printf '%s\n' "$prompt"; cat "$transcript" 2>/dev/null; } | sed -n 's/.*\\n\/frugal\([^\\"]*\).*/\1/p' | head -1 | sed 's/^:frugal//; s/^ *//; s/ *$//')
  echo "frugal: the pasted text has a /frugal line, which does not run as a command. Before anything else, call Skill frugal:frugal with args \"$args\", then answer the user."
fi
round=; case $n in 10|25|40|55|70|85|100) round=1 ;; esac
# A rules file counts as loaded when its header (from a Read or an earlier injection) follows the last compaction.
seen() { grep -oE "\"subtype\":\"compact_boundary\"|# Frugal: $1" "$transcript" 2>/dev/null | tail -1 | grep -qv compact_boundary; }
missing=; seen core || missing="$skill/core.md"; seen session || missing="$missing $skill/modules/session.md"
# Rounds and the prompts after a compaction load what is missing; a pasted copy is re-sent on every later
# message, so a round with both loaded gets one line.
load=; { [ -n "$round" ] || grep -q '"subtype":"compact_boundary"' "$transcript" 2>/dev/null; } && load=$missing
# Context of the last reply: input plus cached tokens from its usage record.
ctx=$(grep -o '"usage":{"input_tokens":[0-9]*,"cache_creation_input_tokens":[0-9]*,"cache_read_input_tokens":[0-9]*' "$transcript" 2>/dev/null | tail -1 | grep -o '[0-9][0-9]*' | awk '{s+=$1} END {print s+0}')
level=$(( ${ctx:-0} / 150000 ))
# Last level whose notice reached the model, read from the transcript: a marker file is spent even when
# Claude Code drops that run's output. A compaction boundary re-arms it.
fired=$(grep -oE '"subtype":"compact_boundary"|"hookEvent":"UserPromptSubmit","content":"([^"\]|\.)*frugal: the context is about [0-9]+k' "$transcript" 2>/dev/null | awk '/compact_boundary/ {k = 0; next} {sub(/k$/, ""); sub(/.* /, ""); k = $0} END {print int(k * 1000 / 150000)}')
if [ "$level" -gt "${fired:-0}" ] && active; then
  echo "frugal: the context is about $(( ctx / 1000 ))k tokens, re-sent on every message. If work remains, end this reply or the next at a natural breakpoint with the handoff block below."
  case $load in *session.md*) ;; *) sed -n '/^\*\*Handoff\.\*\*/,$p' "$skill/modules/session.md" ;; esac
fi
if [ -n "$load$round" ]; then
  active || exit 0
  if [ -n "$load" ]; then
    echo "frugal: this is the user's message $n. The session rules below are loaded; apply them."
    cat $load
  else
    echo "frugal: this is the user's message $n. The frugal core and session rules loaded earlier in this chat still apply: terse replies, fewer calls, a handoff when the context grows."
  fi
fi
seen code && exit 0
printf '%s' "$prompt" | grep -qiE '/frugal(:frugal)? [^\\]*\bcod(e|ing)\b|\b(implement\w*|refactor\w*|endpoints?|tracebacks?|exceptions?|stack ?traces?|compil\w*|migrat\w*|bugs?|crash\w*|regex\w*|functions?|unit tests?|failing tests?|tests? fail\w*|pull requests?|c(o|ó)digo|funci(o|ó)n\w*)\b|```|\.(py|ts|tsx|js|jsx|go|rs|java|kt|cs|rb|php|cpp|c|h|sql|css|html|sh)\b' || exit 0
active || exit 0
echo "frugal code rules (loaded by a hook; do not Read code.md):"
cat "$skill/code.md"
