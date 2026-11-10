# Frugal: compress

Memory files (CLAUDE.md, AGENTS.md, rule files, memory notes, todo lists) are re-read on every call, so every byte saved there is saved on every call. Files written for humans (README, docs, changelogs) are not memory files: refuse and say why.

1. Copy the file to `<name>.original<ext>` next to it, unless that backup already exists.
2. Rewrite in place:
   - Keep exact: every instruction and its conditions, code, commands, paths, URLs, names, numbers, versions, every not/never/only/except.
   - Cut: filler, politeness, explanations of why a rule is good, examples that repeat a rule, duplicate rules (keep the stricter), headings over one line.
   - Fragments and bullets over sentences; one rule per line; same language as the file.
3. Diff old against new: any instruction missing or weakened, put it back.
4. Report one line: bytes before and after, percent saved, backup path.
