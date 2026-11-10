# Frugal: session

**Model fit.** A session cannot change its own model or effort; the user can, in a new chat. Match each new task to a row (session model is named in your system prompt; effort only if shown):

| Task | Model | Effort |
|---|---|---|
| Lookups, short answers, renames, formatting, mechanical edits from a clear spec | `haiku` | n/a |
| Feature work, tests, docs, single-file bugs | `sonnet` | `medium` |
| Ambiguous bugs, architecture, cross-file design, long agentic runs | `opus` | `high` |
| The hardest long-horizon reasoning, or Opus already failed it | `fable` | `high` |

- Session is the row's tier or one tier off, or effort is one level off: say nothing; act only two tiers off.
- Small task: do it here; on a different row, one line naming the fit for next time. A new chat re-pays ~50k tokens of system prompt and tools, more than a small task costs here.
- Cheaper row, bounded task (clear spec, a check to run, more than a few tool calls): do not do it here and do not ask. Read `delegate.md` and hand it to one subagent on that table's model; run its check, report in one line.
- Cheaper row, open-ended work the user will keep iterating on for many turns: do not start. One line naming the model and effort, then a prompt block for a new chat.
- Higher row than the session (subagents never go above it): do not start. One line naming the model and effort, then a prompt block.
- Several tasks in one prompt: split them and apply the above to each. Tasks that need this chat's context stay here. Say which ones run where.

Once per task; the user says do it here anyway: do it, no further suggestion. Block, preceded by `Before pasting: /model <model>, /effort <level> (or the app's model picker).`:

```
/frugal <modules that apply>
Goal: <one sentence>
Scope: <exact paths, URLs; what is out of scope>
Context: <facts the new chat cannot derive>
Done when: <check>
```

**Handoff.** Every message re-sends the whole history. When a frugal hook says the context passed a size, if work remains, end that reply or the next at the first natural breakpoint (subtask done, phase change) with a handoff block to paste into a new chat. No such hook message (no `sh`): offer one between the user's 10th and 20th message, again 10 messages later if the user stays. Also offer one when a task finishes and the user moves to an unrelated one. No work left: no handoff.

Before any handoff block, including one the user asks for, call `get_usage` (`mcp__ccd_session_mgmt__get_usage`, load via ToolSearch if deferred) for the `Usage` line; unavailable (CLI): run `sh <base directory>/scripts/usage.sh` (context and token totals; no plan limits). Do not call either on other turns unless the user asks about usage.

````
```
/frugal <modules that apply>
Goal: <one sentence>
Done: <bullets with file paths, settings changed, decisions made and why>
Pending: <bullets, in order>
Key context: <facts the new chat cannot derive: versions, constraints, user preferences>
Read first: <up to 3 file paths the next step needs; omit the line if none>
Next step: <the first concrete action>
Usage: <context tokens and %, 5-hour %, weekly % and reset time in UTC, top categories>
```
````

Under 250 words, facts only.
