#!/bin/sh
# Denies a Read of this plugin's code.md once session_count.py has pasted it since the last compaction:
# Haiku re-read it in 15 of 48 bench runs (Sonnet in none), and the copy is re-sent on every later call.
input=$(cat)
norm() { tr '\\' '/' | sed 's#//*#/#g; s#/$##' | tr 'A-Z' 'a-z'; }
file=$(printf '%s' "$input" | sed -n 's/.*"file_path" *: *"\([^"]*\)".*/\1/p' | norm)
root=$(printf '%s' "$CLAUDE_PLUGIN_ROOT" | norm)
[ -n "$root" ] && [ "$file" = "$root/skills/frugal/code.md" ] || exit 0
transcript=$(printf '%s' "$input" | sed -n 's/.*"transcript_path" *: *"\([^"]*\)".*/\1/p' | sed 's/\\\\/\//g')
grep -oE '"subtype":"compact_boundary"|frugal code rules \(loaded' "$transcript" 2>/dev/null | tail -1 | grep -q 'frugal code rules' || exit 0
echo 'frugal: code.md is already in context, pasted above as "frugal code rules"; use that copy.' >&2
exit 2
