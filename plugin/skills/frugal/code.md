# Frugal: code

Read the task and the code it touches, trace the real flow, then stop at the first rung that holds:
1. Needed? Speculative need: skip it, say so in one line.
2. In this codebase (helper, util, component, type, pattern)? Reuse it, and import it where you use it.
3. Stdlib or a native platform feature: built-in element or browser API over a custom component, CSS over JS, DB constraint over app code. A UI component request (date, color, range, file, select, dialog, disclosure) is the native element (`<input type="date">`, `type="color"`, `<dialog>`, `<details>`) in the project's styles; a custom widget only when the native one cannot do what was asked.
4. An installed dependency. Never add one for a few lines.
5. One line if one line works; else the minimum code that works.
- Two options of the same size: the edge-case-correct one. The smallest change in the wrong place is a second bug.
- New script, no language named, no project files: Python with the stdlib only.
- Nothing unrequested: features, commands, options, limits, abstractions, wrappers, scaffolding, example or demo files, docs, config for fixed values. An extra worth having goes in the `frugal:` comment, not the code. Fewest files, shortest diff, deletion over addition.
- No docstrings or annotations on unchanged code; no handling for impossible cases. New code with a known ceiling (global lock, O(n^2) scan, naive heuristic, missing option, flag, format, or syntax): ship the simple version with one `frugal:` comment naming the upgrade; never build the upgrade unasked.
- CLI scripts: sys.argv until a flag is requested; let file and argument errors raise.
- Complex or open request: ship the lean version, question the rest in the same reply; never stop to ask which variant.
- Never cut: validation at trust boundaries (reject malformed, missing, or unsafe input; size or rate limits only when asked), error handling that prevents data loss, security, accessibility, anything requested. User insists on the full version: build it.
- Non-trivial logic (branch, loop, parser, money or security path): one runnable check, a small test or asserts under `if __name__ == "__main__":` (never at import time); no frameworks unless asked; none for a small change that reuses existing code. Run it with your own shell, batched with other work (when frugal's hook asks, through delegate's `check`; on "Do it here", your shell again); no shell: leave it unrun, say so, never a subagent. Failing check: fix the code or the expected value, never ship it red.
- Bug fix: root cause, usually a broken invariant (balance never negative, input always parsed). Grep every caller and every sibling that writes the same state; fix once where they all route through (the shared helper, not the function the report names), or in each. A sibling with the same bug is in scope: fix it, never only mention it. Cause unclear: say so.
- Review, audit, shortcut list: read `modules/review.md`. Refactor, migration, verify-only task, commit message: read `modules/change.md`.
- Output: code goes into files in the working directory through your edit tools, never the scratchpad or only the reply; say a file is written only after the tool call that wrote it. Code in the reply (copy-paste safe) only with no edit tool or when a snippet is asked for. Then at most three short lines on what was skipped and when to add it.
