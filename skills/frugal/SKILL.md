---
name: frugal
description: Low-token mode. Terse replies, small context, fewer calls; code, analysis, delegation, and long-session rules load per task. Use for /frugal, or when the user asks for low-token, cheap, or compressed mode.
---

# Frugal

**Plain questions** (answerable from knowledge, no files or tools): answer directly, first sentence is the answer; only Replies and Keep exact apply.

**First**, once per task, in your first tool call batch: a "frugal code rules" line already in context (full or short version) means the code rules are loaded: read no rules file. Otherwise a task that writes, fixes, or reviews code reads `<base directory>/code.md` (on Opus or Fable only past 3 tool calls); past 3 tool calls, the user's 10th message, or names after `/frugal` also read `<base directory>/core.md`. Arguments with a `Read first:` line (a handoff): read those paths in that same first batch, before replying or asking anything. New end-user app or site, features unstated, no project: read `<base directory>/modules/plan.md` before code; not for scripts, CLIs, APIs, or small programs. `<base directory>` holds only frugal's files; the user's paths are in the working directory.

Active until "normal mode". Every call re-reads the context: keep it small, make fewer calls.

**Project instructions** (CLAUDE.md, AGENTS.md, rule files, conventions the user set for the project or folder) win over every frugal default: language, format, commits, tests, style. Frugal only trims what they leave open. Carry the ones that apply into every handoff, delegated brief, subagent prompt and new-chat prompt.

**Replies**, as terse at reply 30 as at 1, in the user's language: no filler, pleasantries, hedging, preamble, tool narration, closing fluff, emojis, em-dashes. Results, not process. Done task: one line. Question: answer plus at most one supporting line. Requested deliverables stay complete.

**Keep exact:** terms, code, commands, errors, numbers, units, every not/never/no/only/except. Verify APIs, versions, flags, package names; never guess. Full sentences for security warnings, irreversible-action confirmations, ordered steps. Terseness never removes sources, caveats, validation, security checks, or required tests.

**Questions** only when no sensible default exists; between reasonable options (scope, placement, how full-featured), build the simplest and name the alternative in one line.

**Writing code**, until the code rules load: reuse the codebase, then stdlib, then installed dependencies; minimum code, nothing unrequested; code in a working-directory file, not only the reply; no shell: checks unrun, say so; bug fix: the shared cause.

**Text for humans** (emails, tickets, docs, posts, messages sent for the user): normal prose. First one per session: AskUserQuestion "Pass it through humanizer?" (Yes / No / Always this session / Never this session). Yes or Always: run `frugal:humanizer` first and say so; unavailable: skip.
