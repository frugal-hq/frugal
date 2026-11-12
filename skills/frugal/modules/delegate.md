# Frugal: delegate

The main thread keeps judgment: architecture, ambiguous bugs, cross-file design, synthesis, the final answer. Every other bounded task goes out, with or without secondary accounts. A subagent starts cold and its result is read here, so a lookup of one or two tool calls stays here.

**With secondary accounts set up** (the frugal hook names them, and `mcp__plugin_frugal_accounts__delegate` is available): send every bounded task to that tool, whatever its size and in any project: searches, reads and summaries of files, threads or pages, code or doc lookups, scripts and command runs, drafts, edits from a clear spec. Keep here only judgment, design, the final answer, and what needs this session's own tools or state (its browser or a single-session login, MCP servers the delegate lacks, files outside its `cwd`). Pass `level` per task (default `standard`; `light` only for a trivial grep) and `cwd` when the work is outside the project dir. The rest of this file is for subagents on the main account.

**Without secondary accounts**, send every bounded task of three or more tool calls to one subagent on the cheapest model and effort in the table: searches and sweeps, reads and summaries of files, logs or pages, edits from a clear spec. Its raw material never enters this context, so later turns do not re-bill it. Never for a lookup of one or two tool calls, or only to run a check.

**Once delegated, do not redo the work.** Never re-read, re-grep, or re-sweep the delegated material. Verify by risk: spot-check at most 2 items the final answer hinges on, by the exact `path:line` the agent returned; check every item only when an error would touch money, security, legal terms, or data that cannot be recovered.

**Escalate on fail, not up front.** Start each part on the cheapest model in the table. A part that comes back missing, contradictory, or failing its check: re-run only that part, narrower, one step up (`haiku` → `sonnet` → `opus`, never above the session model). Fails again: do that part yourself.

**Model and effort.** Always pass `model` explicitly; never above the session model (it is named in your system prompt). Reading, extracting, or summarizing documents is always `haiku`, even when values need judgment; `sonnet` only for edits and code. Pass `effort: "lower"` for search and extraction; omit it for edits.

| Session model | Search, read, extract | Bounded edits, single-file review, code from a clear spec |
|---|---|---|
| Fable, Opus, Sonnet | `haiku`, effort `lower` | `sonnet` |
| Haiku | `haiku`, effort `lower` | `haiku` |

**Agent.** `frugal:frugal-scout` (read-only) for search and reading; `frugal:frugal-worker` for edits; `general-purpose` if neither is installed. Fewest agents: one per independent part, never a split of one sweep just to parallelize; at most 3 unless the user says otherwise. Run in the foreground unless this thread has its own work meanwhile; every background completion wakes this thread and re-reads its context.

**Prompt** (fill every line, nothing else):

```
Goal: <one sentence>
Scope: <exact paths, URLs, or search terms; what is out of scope>
Return: <the final shape, ready to use as-is: CSV rows | file:line table | JSON>, max <N> lines, each with its path:line
Stop when: <done condition>
Do not: edit files | suggest fixes | explain (pick what applies)
Rules: <project rules the task touches: CLAUDE.md, AGENTS.md, conventions the user set; omit the line if none>
```

Every brief, to `delegate` too, carries the `Rules` line: Codex, Grok and OpenCode may not read CLAUDE.md, and conventions set in this chat live nowhere else.
