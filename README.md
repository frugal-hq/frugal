# frugal

A low-token working mode for Claude Code. Type `/frugal` and it stays on for the rest of the session.

frugal is built around one fact about agent sessions: most of the bill is input, not output. Every call re-reads the system prompt, the history, and every file and tool result in context. Shorter replies help a little; keeping the context small and making fewer calls helps much more.

## What it does

`/frugal` loads a 2.3KB core. A short task (3 tool calls or fewer) needs nothing else. Longer tasks read more rules by path, only the ones they need:

| Part | Loaded | What it does |
|---|---|---|
| `SKILL.md` | on `/frugal` | Terse replies that report results, not process; exact terms, numbers, and negations; verify APIs and versions instead of guessing; a few always-on code rules; opt-in humanizer |
| `core.md` | tasks over 3 tool calls, the 10th message, or any module named after `/frugal` | Batched tool calls, no re-reading unchanged files, questions only when the answer changes the result, module picking |
| `code.md` | with `core.md`, when the task writes, fixes, or reviews code | Minimal code: does it need to exist, reuse what the codebase has, stdlib and native platform features before dependencies, root-cause fixes, one runnable check for non-trivial logic. Refactors, migrations, verify-only tasks, and commit messages in one line each. Never cuts validation, security, accessibility, or error handling that prevents data loss |
| `modules/analysis.md` | data, research, metrics, reports | Finding first; every number with a source or derivation and units; missing data, low confidence, and inferences labeled |
| `modules/delegate.md` | secondary accounts set up (any bounded task), raw material too big to read directly (roughly 100k+ tokens), or independent heavy parts | With secondary accounts, sends every bounded task to them; without, hands reading to `haiku` and bounded edits to `sonnet`, never above the session model; escalates a part only when it fails |
| `modules/session.md` | the 10th message (then every 15th, from a hook), a compaction, a new unrelated task, or a model or effort that does not fit the task | Says which model and effort fit, with a ready-to-paste prompt per task, and a handoff block for a fresh chat |
| `modules/review.md` | reviews, audits, shortcut lists | Diff review with bugs and over-engineering in one pass, one line per finding; whole-repo audit ranked by lines saved; a ledger of `frugal:` shortcut comments |
| `modules/compress.md` | on request | Rewrites CLAUDE.md or another memory file to fewer bytes with a backup, keeping every instruction; saved on every call after |
| `scripts/usage.sh` | before a handoff in the CLI, or when you ask about usage | Context size and token totals from the session transcript, where `get_usage` is not available |
| `modules/agents.md` | pipelines, subagent prompts, machine-read output | Structured, parseable output; no narration; never invents paths, endpoints, or field names |
| `plugin/hooks/session-count.sh` | every prompt, prints nothing until needed | When `/frugal` is active (typed, or in a pasted handoff), injects `core.md` and `session.md` at the 10th message and every 15th after, and the handoff rule each time the context passes another 150k tokens. Counts from the transcript, so a plugin reinstall does not reset it. Tells the model to load the skill when `/frugal` only came in pasted text. Needs `sh` (Git Bash on Windows) |
| `accounts/server.py`, `skills/accounts` | only while `~/.frugal/accounts.json` lists an account | Secondary Claude and Codex logins that take delegated work: the `delegate` tool, the delegation rule, and the gates below. Needs `python` on the PATH |
| `humanizer` | only if you say yes | Rewrites AI-sounding text. Based on [blader/humanizer](https://github.com/blader/humanizer), MIT |

The read instruction is the first line of the core and names both files at once. Haiku skipped a chain of reads ("read core, then the module it points to") in every run, and follows a single direct instruction in every run. You can force modules yourself: `/frugal analysis coding`.

**Cheaper models without settings.** The plugin ships two subagents, `frugal-scout` (read-only search and extraction) and `frugal-worker` (bounded edits). The `delegate` module passes the model on every call, so nothing in your settings changes:

| Session model | Search, read, extract | Bounded edits |
|---|---|---|
| Fable | haiku | sonnet |
| Opus | haiku | sonnet |
| Sonnet | haiku | sonnet |
| Haiku | haiku | haiku |

It delegates only when the raw material is too big to read directly, or more than ~30k tokens in a session that continues for many turns. Each subagent starts with an empty cache, and in the bench reading 30 contracts (~32k tokens) directly cost less than splitting them across three subagents. Once it delegates, the main thread does not redo the work: it spot-checks at most two results, or all of them when an error would touch money, security, legal terms, or unrecoverable data. A part that fails is re-run one model up (`haiku` to `sonnet`), and only that part.

**Secondary accounts.** Other Claude logins and Codex can take the bounded work, so the main account's limits last. Set them up with `/frugal:accounts`: `add claude <id>` (its config lives in `~/.claude-<id>`, so its login never touches the main one) or `add codex <id>`; a login terminal opens. From then on, in every session:

- The `delegate` tool runs each brief on the least-used account, in parallel for independent pieces, and moves to the next account when one hits its limit.
- Each provider gets its own model and effort per level: Claude `haiku`/`sonnet`/`opus` with low to max effort, Codex `gpt-6-luna`/`gpt-6.1-sol`/`gpt-6-astra` (only models the login lists) with low to xhigh. Work defaults to the standard level (`sonnet`, medium).
- A hook adds the delegation rule to each prompt. Another sends test and build runs, a fourth read or search in one prompt, and subagents to `delegate` once; asking again runs them here.
- A logged-out account leaves the pool, and each prompt names the command to sign it in again (`/frugal:accounts login <id>`).
- Delegated Claude runs load frugal (`/frugal`), and Codex runs get its core rules in the brief.
- A delegated run never delegates again, and with no account set up none of this loads.
- Heads-up, in Claude Desktop only (Claude Code's own "You should know" never runs there): after a turn with 6 or more tool calls, a side agent on a secondary account (no tools, light level: `haiku` or `gpt-6-luna`) reads the end of the transcript and names one thing you might miss, such as an exposed secret, an unverified claim, or something asked and not done. A note wakes Claude, which shows it as one `Heads up:` line. At most 8 checks and 4 notes per session, so it never drains the account delegate needs; never the same note twice. `/frugal:heads-up off` turns it off for the session, `off all` everywhere, `on` / `on all` back on. The transcript excerpt goes to that account.

**Humanizer is opt-in.** The first time frugal is about to write text for people (a customer email, a ticket reply, docs, a post), it asks once: Yes, No, Always this session, or Never this session. It never runs for code, commits, or internal notes.

frugal covers what caveman and ponytail do, so you do not need them. If they are enabled, disable them: their rules are added to every call (see [Benchmarks](#benchmarks)).

## Install

### Let your agent install it

Paste this into Claude Code (CLI, desktop, or IDE), Claude Cowork, Codex, Cursor, or any agent that can run shell commands:

```text
Install the frugal plugin from https://github.com/CroaBeast/frugal for me.

1. If you are Claude Code: run `claude plugin marketplace add CroaBeast/frugal`, then `claude plugin install frugal@frugal`. Tell me to start a new session and type /frugal.
2. Otherwise, if you support Agent Skills (folders containing a SKILL.md): clone the repo to a temporary folder and copy every folder under plugin/skills/ into your user skills directory. Tell me how to invoke the frugal skill.
3. Otherwise: add the text of plugin/skills/frugal/SKILL.md without its front matter, then plugin/skills/frugal/core.md and plugin/skills/frugal/code.md, to your persistent instructions file (for example AGENTS.md) and tell me which file you changed.

Change nothing else. If the caveman or ponytail plugins are enabled, tell me, but do not disable them yourself.
```

Outside Claude Code, the subagents (`frugal-scout`, `frugal-worker`) and the model routing in `modules/delegate.md` do not apply, and step 3 installs only the core rules.

### By hand, in Claude Code

```
/plugin marketplace add CroaBeast/frugal
/plugin install frugal@frugal
```

Open a new session and type `/frugal`. If you also have a separate copy of `humanizer` or of the frugal skills in `~/.claude/skills`, remove it so they do not show up twice.

## Usage

```
/frugal                   # modules picked per task
/frugal analysis          # force a module
/frugal coding agents     # several modules
```

Say `normal mode` to turn it off.

## Should you use it?

On Opus 5.5 (0.15, six tasks) frugal came out even on cost with 11% less output; on feature work (ponytail's harness, 0.18.4) it cost 38% less than plain Claude Code on Opus 5.5 and 13% less on Sonnet 5.5, and on Haiku 4.5 it passed every check where plain Claude Code missed 10 of 117 runs, at a cost within noise (-7%):

- **Long sessions and heavy tasks:** in 0.14, 10 to 12% cheaper on the 40-message session, the heavy coding task, and the contract risk review. Every call re-reads the whole history, so shorter replies and fewer re-reads compound as a session grows. The 16-message session came out -2%: at the 10th message frugal writes a handoff block for a fresh chat, which only pays back when you actually move to one (see [BENCHMARKS.md](BENCHMARKS.md)).
- **Short one-off tasks:** +1 to +8% on Opus. Turning frugal on costs ~1,140 input tokens once per session (about 0.009 USD on Opus), and a 3-call task has too little output to win that back.
- **Writing features:** on ponytail's own harness (39 tasks, n=3, same batch), 0.18.4 against ponytail 5.2.0 passed every check on all three models and cost 8.3% less on Haiku 4.5, 5.8% less on Sonnet 5.5, and 6.2% less on Opus 5.5. It wrote less code on Haiku and about the same on Sonnet and Opus (see below).
- **Plain questions:** on caveman's own eval, 0.16.0 answered with 28 to 45% fewer output tokens than caveman at the same judged quality.
- **Behavior, not just tokens:** verify APIs and versions instead of guessing, build the sensible default instead of stopping to ask, minimal code that keeps validation and tests, opt-in humanizer for text people read.

**Questions cost tokens too.** Each question is one extra call: the model writes the question, your answer comes back, and the whole context is read again (mostly from cache, which is cheaper). That is why frugal asks only when missing information would change the result: one question is much cheaper than redoing work built on a wrong guess.

## Model routing and other tools

A skill cannot switch the session's model or effort. frugal routes its subagents, and when a task fits another model: a bounded task goes to a subagent on the cheaper model automatically; open-ended work, or work that needs a stronger model than the session, gets a prompt to paste in a new chat with the model and effort to use. Small tasks it does in place, since a new chat re-pays the system prompt. Beyond that, Claude Code has its own controls, and they combine with frugal:

| Option | What it routes | Notes |
|---|---|---|
| `/model opusplan` | Opus while planning, Sonnet while executing | Switches at the plan-mode boundary, not per request |
| `/effort low` to `max` | How much the model thinks per step | Set it per session; `low` for simple work |
| `CLAUDE_CODE_SUBAGENT_MODEL` | Default model for subagents | frugal passes the model explicitly anyway |
| A proxy such as claude-code-router or OmniRoute | Every request, by rules or cost | Needs provider API keys and routes to non-Anthropic models, so the quality claim above no longer holds |

Switching models inside one session also throws away the prompt cache, which is per model: the next call re-pays the whole context at the uncached rate. That is why per-request routing pays off in your own app with independent requests, and much less inside one long agent session.

Knowledge-graph tools such as Graphify target a different cost (exploring very large repos) and are not measured here.

## Benchmarks

Every number here comes from real headless Claude Code sessions (`claude -p`), checked automatically, with every arm run in the same batch, and losses are shown next to wins. caveman and ponytail publish numbers from their own setups: caveman from single API calls against a model with no system prompt, ponytail from feature tasks on Haiku only. Here each plugin runs the way you would use it, on three tests and three models. Per-task tables, methods, and every earlier batch are in [BENCHMARKS.md](BENCHMARKS.md).

```mermaid
xychart-beta horizontal
    title "frugal 0.18.4 feature work cost (other arm = 100), lower is cheaper"
    x-axis ["vs no plugins, Haiku", "vs no plugins, Sonnet", "vs no plugins, Opus", "vs ponytail 5.2.0, Haiku", "vs ponytail 5.2.0, Sonnet", "vs ponytail 5.2.0, Opus"]
    y-axis "Relative cost" 0 --> 110
    bar [93.4, 86.9, 61.8, 91.7, 94.2, 93.8]
    line [100, 100, 100, 100, 100, 100]
```

Each bar is frugal 0.18.4 against one other arm on one model, both run in the same batch on ponytail's harness (39 tasks, n=3); the line at 100 is that other arm. frugal passed every check in all six batches. Pass rates and lines are in the tables under [Against plain Claude Code](#benchmarks) and [Latest head-to-head](#benchmarks) below.

### Summary: every test, every model

Feature work rows are the five-arm batches that ran every plugin together (frugal 0.17.0, ponytail 4.10.0); the latest two-arm results are in the chart above. Cost relative to the baseline of the same batch (= 100; lower is cheaper). Each row is one batch, so compare across a row, not down a column. Pass rates and per-task results are in [BENCHMARKS.md](BENCHMARKS.md). "Not run" means that combination has no batch yet.

| Test | Model | No plugins | frugal | caveman | ponytail | ponytail + caveman |
|---|---|---|---|---|---|---|
| Feature work (ponytail's harness, 39 tasks) | Haiku 4.5 | 100 | **81.6** | 100.3 | 91.3 | 98.7 |
| | Sonnet 5.5 | 100 | **87.8** | 120.3 | 91.3 | 110.3 |
| | Opus 5.5 (34 tasks) | 100 | **61.0** | 108.1 | 64.0 | 76.1 |
| Fixes, analysis, writing (`bench.py`, six tasks) | Haiku 4.5 | 100 | **89.9** | not run | not run | not run |
| | Sonnet 5.5 | 100 | 104.6 | not run | not run | not run |
| | Opus 5.5 | 100 | 100.1 | not run | not run | not run |
| Plain questions (caveman's eval, cost per answer) | Haiku 4.5 | 100 | **90.5** | 196.5 | | |
| | Sonnet 5.5 | 100 | **77.5** | 89.2 | | |
| | Opus 5.5 | 100 | **91.9** | 92.4 | | |

Batches: feature work, Haiku 2026-10-04 (frugal 0.17.0 before its last rule) Sonnet 2026-10-07 and Opus 2026-10-07 to 08 (frugal 0.17.0; Opus has 34 of 39 tasks, the rest stopped at the usage limit); `bench.py` Opus 2026-10-03 (frugal 0.15), Haiku and Sonnet 2026-10-20 to 21 (frugal 0.18.5); caveman's eval Opus 2026-10-03 (frugal 0.15), Haiku and Sonnet 2026-10-21 (frugal 0.18.5; Haiku has 14 answers per arm, the rest hit the session limit; baseline "Answer concisely."; caveman's eval has no ponytail arm).

**Where frugal loses.** On `bench.py` with Opus, one-step tasks cost 6 to 16% more than no plugins (1 to 8% in a 0.16.0 rerun): turning frugal on adds a fixed ~1,140 input tokens that a 3-call task cannot win back. Longer tasks pay it back, and the six-task sum comes out even on Opus and 10% cheaper on Haiku. On Sonnet (0.18.5) the six tasks cost 4.6% more: the short ones +2 to +7%, and the heavy and review tasks did not win the fixed cost back. The same fixed cost shows on Haiku feature work: the 12 small fix, security, and reuse tasks cost 19% more than no plugins (Sonnet +1%, Opus -10%), because a $0.03 task cannot absorb the extra rules and the caller and sibling search that bug fixes do.

**Latest head-to-head.** frugal 0.18.4 against ponytail 5.2.0 on ponytail's own harness (39 tasks, n=3, both arms in one batch per model, 2026-10-15 to 20):

| Model | Correct, frugal / ponytail | Safe | Lines (median) | frugal cost vs ponytail (90% CI) |
|---|---|---|---|---|
| Haiku 4.5 | **1.000** / 0.983 | **1.000** / 0.974 | **27** / 32 | **-8.3%** (-13.8% to -2.6%) |
| Sonnet 5.5 | 1.000 / 1.000 | 1.000 / 1.000 | 29 / 28 | **-5.8%** (-9.2% to -3.0%) |
| Opus 5.5 | **1.000** / 0.991 | 1.000 / 1.000 | 34 / 31 | **-6.2%** (-10.4% to -2.1%) |

ponytail still writes a few lines less on Sonnet and Opus, mostly line wrapping in frontend components and the guards frugal adds when it fixes a shared helper. The five-arm batches above ran earlier versions against ponytail 4.10.0.

**Against plain Claude Code.** frugal 0.18.4 against no plugins, same harness (39 tasks, n=3, both arms in one batch per model, 2026-10-20 to 21):

| Model | Correct, frugal / no plugins | Safe | Lines (median) | frugal cost vs no plugins (90% CI) |
|---|---|---|---|---|
| Haiku 4.5 | **1.000** / 0.940 | **1.000** / 0.974 | **24** / 48 | -6.6% (-14.0% to +1.2%) |
| Sonnet 5.5 | 1.000 / 1.000 | 1.000 / 1.000 | **30** / 73 | **-13.1%** (-19.0% to -7.2%) |
| Opus 5.5 | **1.000** / 0.974 | 1.000 / 1.000 | **34** / 91 | **-38.2%** (-45.6% to -29.9%) |

Plain Claude Code missed `trace-transfer` safety in 3 of 3 Haiku runs, wrote no file in 7 Haiku runs, and timed out (300 s) on all 3 Opus runs of `vibe-md2html`.

### Reproduce

```
cd bench
python bench.py run --conds AEFGB --tasks t0_ok --reps 3 --model claude-sonnet-5-5 --out fixed_sonnet   # fixed cost per plugin
python bench.py run --conds AEFGB --tasks t1_bug,t2_analysis,t3_email,t5_heavy,t8_review --reps 2 --model claude-opus-5-5 --out tasks_opus
python bench.py report --out tasks_opus

cd ponytail_agentic
FRUGAL_PLUGIN_DIR=<path to this repo>/plugin python run.py --all --arms baseline,frugal,ponytail,caveman,ponytail+caveman --models haiku --runs 2 --max-runs 3
python run.py --task core8 --arms baseline,frugal,ponytail,caveman,ponytail+caveman --models opus --runs 2 --max-runs 3
python run.py --all --arms frugal --models haiku --runs 2 --max-runs 3 --reuse runs/<earlier batch>   # new frugal, other arms reused
python run.py ... --resume <run dir>      # finish a batch cut by a usage limit
```

A: no plugins, E: frugal, F: caveman, G: ponytail, B: caveman + ponytail. `bench.py run` skips runs that already have results and does not save runs that hit the usage limit, so re-running the same command fills the gaps. Rep 1 of each batch pays for writing the prompt cache, so the fixed-cost table uses only later reps. How the batches are kept small is in [BENCHMARKS.md](BENCHMARKS.md#tests).

## Versions

- **0.25.3**: delegate's `check` runs in bash (Git Bash on Windows), not cmd.exe; with kind `run` it runs once, before any agent, and the reply is its real exit code and output, with an agent explaining only a failure; a check that cannot run (missing tool, Docker permission, timeout) answers "Do it here"; edit agents get the check in their brief. The hook no longer sends `ssh` commands or mentions of `bench.py` to delegate, and says how to get out of it.
- **0.25.2**: delegated commands no longer inherit the MCP server's stdin; parallel sessions lock frugal's files before writing them; session state older than a week is removed.
- **0.25.1**: heads-up checks use the light level and stop after 8 per session, so they do not spend the account delegate needs.
- **0.25.0**: heads-up notes in Claude Desktop from a side agent on a secondary account, with `/frugal:heads-up` to turn them off.
- **0.24.1**: a delegated run or check that times out kills its whole process tree, so it no longer hangs on Windows.
- **0.24.0**: the handoff comes from context size (every 150k tokens) instead of message count; a pasted handoff loads frugal; the session hook reads the whole prompt and survives a plugin reinstall.
- **0.23.0**: delegated Claude runs load frugal, and Codex runs get its core rules.
- **0.22.2**: explorations hand off after three reads, so the main session can read what an edit needs.
- **0.22.1**: a logged-out secondary account leaves the pool, and every prompt names the command to sign it in again.
- **0.22.0**: with secondary accounts, every bounded task goes to them; delegated work defaults to the standard level; explorations hand off after two reads; delegated Claude edits can run commands; `delegate` takes a working directory.
- **0.21.0 to 0.21.1**: secondary Claude and Codex accounts take delegated work through the plugin's own MCP server, hooks, and `/frugal:accounts`; the frugal-mod commands left the plugin.
- **0.20.0**: commands for the experimental frugal-mod (removed in 0.21.1).
- **0.18.5**: `frugal-worker` runs on the session model unless a cheaper one is passed; on Haiku it had escalated to Sonnet in 2 of 117 runs.
- **0.18.4**: unrequested features, commands, options, and limits stay out of new code (an extra worth having goes in a `frugal:` comment); trust-boundary validation rejects bad input, with size or rate limits only when asked; code goes in the working directory, never the scratchpad. On 25 tasks (n=3) it wrote 8% fewer lines on Sonnet and cost 2.8% less on Sonnet and 4.4% less on Haiku than 0.18.3, with every safety check passed.
- **0.18.3**: code goes into files through the edit tools, not only into the reply, and a file is reported written only after the call that wrote it. On Haiku, `reuse-slug` wrote the file in 6 of 6 runs against 2 of 3 for 0.18.2. On Sonnet (39 tasks) it cost 3.4% less than ponytail with every check passed.
- **0.18.2**: a bug-fix guard goes in the shared helper, not the function the report names, and a sibling with the same bug is fixed, never only mentioned. On Haiku, `trace-transfer` passed 14 of 18 runs against 9 of 18 for 0.17.0.
- **0.18.1**: the ceiling rule covers new code only, not bug fixes; the error rule covers CLI scripts only (`let file and argument errors raise`), after 0.18.0 left a `null` body unguarded in one Haiku run of `todo-null`.
- **0.18.0**: a known ceiling (naive heuristic, missing option, flag, format, or syntax) ships as the simple version with a `frugal:` comment naming the upgrade; CLI input stays on `sys.argv` until a flag is requested; no try/except that only rewords a traceback. On ponytail's harness (15 open-ended tasks, n=3) it wrote 17% fewer lines on Sonnet and Opus and 7% fewer on Haiku, with safety unchanged.
- **0.17.0**: requested code goes in a file in the working directory; a check is never run by a subagent; a new script with no language named is Python with the stdlib. On ponytail's harness (Haiku 4.5, 39 tasks) it passed every correctness check and cost 13% less than ponytail.
- **0.16.0**: plain questions go straight to an answer, which cut Opus's output on caveman's eval by a third; `t0_ok` measures the fixed cost.
- **0.15.0**: suggests the model and effort that fit each task (a subagent for bounded work, a prompt for a new chat otherwise); Fable in the delegation table; `review` and `compress` modules; usage totals in the CLI; code rules for the worker subagent and for every code task on Haiku and Sonnet; native UI elements named; root-cause rule in the core; the session hook ignores background-task notifications.
- **0.14.0**: a hook loads the session rules at the 10th message; frugal builds the default instead of stopping to ask. Benchmarked 6% cheaper than plain Claude Code on Opus 5.5 with 17% less output and the same pass rate.
- **0.13.0**: code rules in `code.md`, read with `core.md` only for code tasks.
- **0.12.0**: one direct read instruction first in the core (Haiku follows it); always-on code essentials; delegated parts verified by risk and escalated only on failure.
- **0.11.0, 0.11.1**: small always-on core; `core.md` and modules only for tasks over 3 tool calls or from the 10th message.
- **0.10.0**: delegation only for large inputs, never redone after delegating.
- **0.9.0**: always-on AI-tell rule; humanizer patterns 26 to 30 and a scoring step.
- **0.8.0, 0.8.1**: `tight` removed; modules read by path instead of listed as skills.
- **0.7.0 to 0.7.2**: first plugin build: per-task modules, delegation to haiku and sonnet subagents, session handoff, experimental `tight` profile.
- **0.3.0 to 0.6.0**: modular skills, standalone (no caveman or ponytail needed), opt-in humanizer.
- **0.2.0**: light and heavy work triaged per message; coding and analysis profiles dropped.
- **0.1.0, 0.1.1**: single skill on top of caveman and ponytail at `ultra`, with task profiles; the most expensive setup in the benchmarks.

## Licenses

frugal is MIT (see `LICENSE`). `humanizer` keeps its own MIT `LICENSE` file in its folder. `bench/ponytail_agentic` is ponytail's benchmark, MIT (see `LICENSE.ponytail`).
