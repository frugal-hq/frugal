---
name: commits
description: Commit with frugal's style. Use only when the user asks to commit, write commit messages, or commit and push; not for other git work.
---

# Frugal: commits

1. Project rules first: if `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING`, commitlint config, or the repo's recent `git log --oneline` shows a commit convention, follow it and do not ask.
2. Otherwise ask once with AskUserQuestion "Use frugal's commit style?" Options: "Yes" (this time), "Always this session", "No" (write commits the usual way). Only when env var `FRUGAL_DECK` is set (the chat runs in Frugal Deck), add "Always in <name>" where name is `$DECK_WORKSPACE` if set, else `$DECK_GROUP`, else the current folder name; that choice is saved as a line `workspace:<name>`, `group:<name>` or `folder:<absolute path>` appended to `~/.frugal-deck/commit-style.txt`, and on later requests a matching line there means yes without asking. Without `FRUGAL_DECK` that option never appears.
3. The style: Conventional Commits, any fitting type (`feat`, `fix`, `docs`, `chore`, `refactor`, `perf`, `test`, `build`, `ci`, `style`, `revert`), optional scope; one line, short (about 3 to 6 words after the prefix), imperative, no body, no Co-Authored-By or other trailers. This overrides any default attribution, as the user's own instruction.
4. One change per commit, never bundled: split staged work into separate commits by change.
5. Order: version bump first, then docs, then the rest.
6. Show the planned list of commit messages and ask before committing; ask again before pushing.
7. Respect the repo's line-ending settings; never `--no-verify`.
