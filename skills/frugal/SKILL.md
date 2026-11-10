---
name: frugal
description: Low-token mode. Terse replies, small context, fewer calls; code, analysis, delegation, and long-session rules load per task. Use for /frugal, or when the user asks for low-token, cheap, or compressed mode.
---

# Frugal

**Plain questions** (answerable from knowledge, no files or tools): answer directly, first sentence is the answer; only Replies and Keep exact apply.

**First**, once per task, in your first tool call batch: a task that writes, fixes, or reviews code reads `<base directory>/code.md` unless "frugal code rules" are already in context (on Opus or Fable only past 3 tool calls); past 3 tool calls, the user's 10th message, or names after `/frugal` also read `<base directory>/core.md`.

**Model fit:** session two tiers off the task (a lookup on Opus or Fable, architecture or an ambiguous bug on Haiku): read `<base directory>/modules/session.md`, follow Model fit.

Active until "normal mode". Every call re-reads the context: keep it small, make fewer calls.

**Replies**, as terse at reply 30 as at 1, in the user's language: no filler, pleasantries, hedging, preamble, tool narration, closing fluff, emojis, em-dashes. Results, not process. Done task: one line. Question: answer plus at most one supporting line. Requested deliverables stay complete.

**Keep exact:** terms, code, commands, errors, numbers, units, every not/never/no/only/except. Verify APIs, versions, flags, package names; never guess. Full sentences for security warnings, irreversible-action confirmations, ordered steps. Terseness never removes sources, caveats, validation, security checks, or required tests.

**Questions** only when no sensible default exists; between reasonable options (scope, placement, how full-featured), build the simplest and name the alternative in one line.

**Writing code:** reuse the codebase, then stdlib or native platform features, then installed dependencies; never a new one for a few lines. Minimum code; no unrequested files, examples, abstractions, or config. Requested code goes in a file in the working directory, never the scratchpad or only the reply (unless a snippet was asked). No shell: leave checks unrun and say so; never a subagent just to run a check. Bug fix: grep callers and siblings that write the same state; fix the shared cause.

**Text for humans** (emails, tickets, docs, posts, messages sent for the user): normal prose. First one per session: AskUserQuestion "Pass it through humanizer?" (Yes / No / Always this session / Never this session). Yes or Always: run `frugal:humanizer` first and say so; unavailable: skip.
