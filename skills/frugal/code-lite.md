# Frugal: scoped code

For a scoped change to a named function or file:
- Read the named file in the working directory directly (not the skill's base directory), never a search for a path you were given; then what it calls and the helpers beside it.
- Reuse what the file or project already has (helpers, formatters, parsers), then the stdlib; never a new dependency for a few lines.
- Minimum code that handles the real edge cases; nothing unrequested; no docstrings or annotations on unchanged code; no handling for impossible cases.
- Never cut: validation at trust boundaries (reject malformed, missing or unsafe input), error handling that prevents data loss, security.
- Bug fix: root cause, usually a broken invariant (balance never negative, input always parsed). Fix it at the line that writes the state, usually a shared helper, not in the function the report names, so every caller is covered; else grep every caller and sibling that writes the same state and fix each.
- Money or security logic: one small runnable check, run with your shell; no shell: leave it unrun and say so, never a subagent. Never ship it red.
- Reply: the change and anything skipped, at most three lines.
