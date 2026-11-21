# Frugal: code

Read the task and the code it touches and trace the real flow, then stop at the first rung that holds:
1. Needed? Speculative need: skip it, say so in one line.
2. In this codebase (helper, util, component, type, pattern)? Reuse it and import it.
3. Stdlib or a native platform feature: a UI component request (date, color, file, dialog, disclosure) is the native element (`<input type="date">`, `type="color"`, `<dialog>`, `<details>`) in the project's styles, a custom widget only when it cannot do what was asked; CSS over JS; DB constraint over app code.
4. An installed dependency; never a new one for a few lines.
5. One line if one line works; else the minimum code that works. Of two the same size, the edge-case-correct one: the smallest change in the wrong place is a second bug.
- New script, no language named, no project files: Python with the stdlib only. CLI scripts: sys.argv until a flag is requested.
- No docstrings or annotations on unchanged code; no handling for impossible cases; let file and argument errors raise.
- Nothing unrequested (features, commands, options, limits, abstractions, wrappers, demo files, docs, config). Fewest files, shortest diff, deletion over addition. An extra worth having goes in a `frugal:` comment, not the code. A known ceiling (O(n^2), naive heuristic, missing flag or format): ship the simple version with one `frugal:` comment naming the upgrade; never build the upgrade unasked. Complex or open request: ship the lean version, raise the rest in the reply; never stop to ask.
- Never cut: validation at trust boundaries (reject malformed, missing or unsafe input; size or rate limits only when asked), error handling that prevents data loss, security, accessibility, anything requested. User insists on the full version: build it.
- Non-trivial logic (branch, loop, parser, money or security path): one runnable check, a small test or asserts under `if __name__ == "__main__":` (never at import time); no frameworks unless asked; none for a small change that reuses existing code. Run it with your shell (through delegate's `check` when frugal's hook asks); no shell: leave it unrun, say so, never a subagent. Never ship it red. Tests and builds: quiet flags, `| tail -40`.
- Bug fix: root cause, usually a broken invariant (balance never negative, input always parsed). Fix it at the line that writes the state, usually a shared helper, not in the function the report names, so every caller is covered; else grep every caller and sibling that writes the same state and fix each. A sibling with the same bug is in scope: fix it, never only mention it. Cause unclear: say so.
- Review, audit, shortcut list: read `modules/review.md`. Refactor, migration, verify-only, commit message: `modules/change.md`.
- Write code into files in the working directory with your edit tools; say a file is written only after the call that wrote it. Code in the reply only with no edit tool or when a snippet is asked. Then at most three short lines on what was skipped and when to add it.
