import { atom, read, update } from 'claude-code'
import type { EngineInterface, On } from 'claude-code'

import type { Account, AccountKind, Task, TaskStatus } from '../types'

const accounts = atom({ plugin: 'frugal-mod', key: 'accounts' } as const, [])
const modules = atom({ plugin: 'frugal-mod', key: 'modules' } as const, {})
const providers = atom({ plugin: 'frugal-mod', key: 'providers' } as const, {})
const tasks = atom({ plugin: 'frugal-mod', key: 'tasks' } as const, [])
const turn = atom({ plugin: 'frugal-mod', key: 'turn' } as const, null)

export type Kind = 'search' | 'edit' | 'run' | 'review' | 'classify'

// How each provider logs in. Plan, paid or free, and models come from the provider once it answers.
export const PRESETS: Record<string, { kind: AccountKind; label: string; baseUrl?: string; env?: string; keyUrl?: string }> = {
  claude: { kind: 'claude-cli', label: 'Claude' },
  codex: { kind: 'codex-cli', label: 'Codex' },
  anthropic: { kind: 'anthropic-api', label: 'Anthropic API', baseUrl: 'https://api.anthropic.com/v1', env: 'ANTHROPIC_API_KEY', keyUrl: 'https://console.anthropic.com/settings/keys' },
  gemini: { kind: 'openai-compatible', label: 'Gemini', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', env: 'GEMINI_API_KEY', keyUrl: 'https://aistudio.google.com/apikey' },
  grok: { kind: 'openai-compatible', label: 'Grok (xAI)', baseUrl: 'https://api.x.ai/v1', env: 'XAI_API_KEY', keyUrl: 'https://console.x.ai' },
  groq: { kind: 'openai-compatible', label: 'Groq', baseUrl: 'https://api.groq.com/openai/v1', env: 'GROQ_API_KEY', keyUrl: 'https://console.groq.com/keys' },
  openrouter: { kind: 'openai-compatible', label: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1', env: 'OPENROUTER_API_KEY', keyUrl: 'https://openrouter.ai/settings/keys' },
  cerebras: { kind: 'openai-compatible', label: 'Cerebras', baseUrl: 'https://api.cerebras.ai/v1', env: 'CEREBRAS_API_KEY', keyUrl: 'https://cloud.cerebras.ai' },
  mistral: { kind: 'openai-compatible', label: 'Mistral', baseUrl: 'https://api.mistral.ai/v1', env: 'MISTRAL_API_KEY', keyUrl: 'https://console.mistral.ai/api-keys' },
  github: { kind: 'openai-compatible', label: 'GitHub Models', baseUrl: 'https://models.github.ai/inference', env: 'GITHUB_TOKEN', keyUrl: 'https://github.com/settings/tokens' },
  ollama: { kind: 'openai-compatible', label: 'Ollama (local)', baseUrl: 'http://localhost:11434/v1' },
}

// frugal picks the model: the cheapest-looking one an HTTP provider lists, never one for embeddings, audio or images.
// Whole name parts only: "mini" is in "gemini".
const CHEAP = /(^|[-_./:])(flash|mini|small|lite|haiku|nano|instant|8b)/i
const NOT_CHAT = /embed|whisper|tts|audio|image|vision|guard|moderation|rerank/i
export const pickModel = (models: string[] = [], strong = false) => {
  const chat = models.filter(m => !NOT_CHAT.test(m))
  return chat.find(m => CHEAP.test(m) !== strong) ?? chat[0] ?? ''
}

export type Level = 'light' | 'standard' | 'heavy' | 'max'
// What the main session picked for one call; left out, the kind sets the level and the level the effort.
export type Pick = { level?: Level; effort?: string }
const LEVEL: Record<Kind, Level> = { search: 'light', edit: 'standard', run: 'light', review: 'light', classify: 'light' }
const EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']

// Per provider: the model and effort each level runs as, and the efforts it takes (claude --help, ~/.codex/models_cache.json).
// A Codex model the account does not list falls back to the one in its config.toml; HTTP providers take no effort.
export const RULES: Partial<Record<AccountKind, { efforts: string[]; levels: Record<Level, { model?: string; effort: string }> }>> = {
  'claude-cli': {
    efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
    levels: { light: { model: 'haiku', effort: 'low' }, standard: { model: 'sonnet', effort: 'medium' }, heavy: { model: 'opus', effort: 'high' }, max: { model: 'opus', effort: 'max' } },
  },
  'codex-cli': {
    efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    levels: {
      light: { model: 'gpt-6-luna', effort: 'low' },
      standard: { model: 'gpt-6.1-sol', effort: 'medium' },
      heavy: { model: 'gpt-6.1-sol', effort: 'high' },
      max: { model: 'gpt-6-astra', effort: 'xhigh' },
    },
  },
}

// An effort the provider does not take becomes the closest one below it, else its lowest.
const fit = (effort: string, efforts: string[]) =>
  [...efforts].reverse().find(e => EFFORTS.indexOf(e) <= EFFORTS.indexOf(effort)) ?? efforts[0]!

// The model and effort one account runs a task with; on an HTTP provider light and standard take a cheap model, heavier levels a strong one.
export const choose = (a: Account, kind: Kind, pick: Pick = {}) => {
  const level = pick.level ?? LEVEL[kind]
  const rule = RULES[a.kind]
  if (!rule) return { model: pickModel(a.models, level === 'heavy' || level === 'max'), effort: undefined }
  const base = rule.levels[level]
  const listed = a.kind !== 'codex-cli' || (a.models ?? []).includes(base.model ?? '')
  return { model: listed ? base.model : undefined, effort: fit(pick.effort ?? base.effort, rule.efforts) }
}
const LIMIT = /usage limit|rate limit|limit reached|quota|too many requests|\b429\b/i
const HOUR = 3_600_000
const MAX_TRIES = 3
export const DELEGATE_TOOL = 'mcp__frugal-mod__delegate'
export const DELEGATE_SPEC = {
  name: 'delegate',
  description: `Runs bounded work on the user's secondary accounts so this account's limits last: searches, reading or summarizing files, test or build runs, edits you can brief exactly. Independent pieces go in "tasks" and run in parallel. Keep design and the final check here; "do it here" means do it yourself.`,
  inputSchema: {
    type: 'object',
    properties: {
      task: { type: 'string', description: 'Goal, exact paths, done-when.' },
      tasks: { type: 'array', items: { type: 'string' }, description: 'Independent briefs, run in parallel.' },
      kind: { type: 'string', enum: ['search', 'edit', 'run'], description: 'search: read-only. edit: changes files. run: runs commands and reports.' },
      check: { type: 'string', description: 'Bash command (Git Bash on Windows) that exits 0 when done right. With kind run: the command to run, once.' },
      level: { type: 'string', enum: ['light', 'standard', 'heavy', 'max'], description: 'light: searches, reads. standard: edits, test runs. heavy: hard multi-file work. max: after heavy failed.' },
      effort: { type: 'string', enum: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'], description: 'Overrides the effort of the level.' },
    },
    required: ['kind'],
  },
  isDeferred: false,
}

// HTTP providers see only the prompt, so they cannot search or edit the repo.
export const serves = (a: Account, kind: Kind) => a.kind === 'claude-cli' || a.kind === 'codex-cli' || kind === 'review' || kind === 'classify'

export const modelFor = (a: Account, kind: Kind, pick: Pick = {}) => {
  const c = choose(a, kind, pick)
  return [c.model, c.effort && `${c.effort} effort`].filter(Boolean).join(', ')
}

// An ordered provider list is a preference; with none, the least busy account goes first, then the least used today.
export const candidates = (list: Account[], kind: Kind, order: string[], now: number, day: string, busy: Record<string, number> = {}) => {
  const ready = list.filter(a => a.enabled && a.status === 'connected' && serves(a, kind) && !((a.exhaustedUntil ?? 0) > now) &&
    !(a.cap && a.day === day && a.used >= a.cap))
  if (order.length) return order.map(id => ready.find(a => a.id === id)).filter((a): a is Account => !!a)
  const load = (a: Account) => (busy[a.id] ?? 0) * 1000 + (a.day === day ? a.used : 0)
  return [...ready].sort((x, y) => load(x) - load(y))
}

const isOn = async ($: EngineInterface, id: string) => (await read($, modules))[id] ?? true

// $.env.get takes literal names only.
const keyFor = async ($: EngineInterface, preset: string) => {
  switch (preset) {
    case 'anthropic': return $.env.get('ANTHROPIC_API_KEY')
    case 'gemini': return $.env.get('GEMINI_API_KEY')
    case 'groq': return $.env.get('GROQ_API_KEY')
    case 'openrouter': return $.env.get('OPENROUTER_API_KEY')
    case 'cerebras': return $.env.get('CEREBRAS_API_KEY')
    case 'mistral': return $.env.get('MISTRAL_API_KEY')
    case 'github': return $.env.get('GITHUB_TOKEN')
    case 'grok': return $.env.get('XAI_API_KEY')
    default: return 'none'
  }
}

const run = async ($: EngineInterface, a: Account, kind: Kind, prompt: string, cwd: string, pick: Pick) => {
  const c = choose(a, kind, pick)
  if (a.kind === 'claude-cli' || a.kind === 'codex-cli') {
    const argv = a.kind === 'claude-cli'
      ? ['claude', '-p', '--model', c.model!, '--effort', c.effort!, ...(kind === 'edit' ? ['--permission-mode', 'acceptEdits'] : []),
          // A search may run read-only scripts; claude -p refuses Bash unless it is allowed.
          ...(kind === 'run' || kind === 'search' ? ['--allowedTools', 'Bash Read Grep Glob'] : [])]
      // codex 0.166 dropped --full-auto: the workspace-write sandbox lets an edit write files.
      : ['codex', 'exec', '--skip-git-repo-check', ...(c.model ? ['-m', c.model] : []), '-c', `model_reasoning_effort=${c.effort}`,
          ...(kind === 'edit' || kind === 'run' ? ['-s', 'workspace-write'] : []), '-']
    const env: Record<string, string> = { FRUGAL_MOD_DELEGATED: '1', ...(a.configDir ? { CLAUDE_CONFIG_DIR: a.configDir } : {}) }
    // Windows resolves npm's .cmd shims only through cmd.
    const win = (await $.env.get('OS')) === 'Windows_NT'
    const r = await $.process.run(win ? ['cmd', '/c', ...argv] : argv, { cwd, env, stdin: prompt, timeoutMs: 10 * 60_000 })
    const out = r.stdout.trim()
    return { ok: r.exitCode === 0 && out !== '', text: out || r.stderr.trim(), limited: r.exitCode !== 0 && LIMIT.test(r.stderr + r.stdout) }
  }
  const key = await keyFor($, a.preset)
  if (!key) return { ok: false, text: `no API key for ${a.preset}`, limited: false }
  const isAnthropic = a.kind === 'anthropic-api'
  const r = await $.http.fetch(`${a.baseUrl}/${isAnthropic ? 'messages' : 'chat/completions'}`, {
    method: 'POST',
    headers: isAnthropic
      ? { 'content-type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01' }
      : { 'content-type': 'application/json', authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: c.model, max_tokens: 800, messages: [{ role: 'user', content: prompt }] }),
  })
  if (!r.ok) return { ok: false, text: `HTTP ${r.status}`, limited: r.status === 429 }
  const body = JSON.parse(r.text)
  const text: string = isAnthropic ? body.content?.[0]?.text ?? '' : body.choices?.[0]?.message?.content ?? ''
  return { ok: text !== '', text: text.trim(), limited: false }
}

// Test, build and lint runs fill this session's context; while an account is connected they go to delegate.
const HEAVY_RUN = /\b(npm|pnpm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck)\b|\b(pytest|jest|vitest|tsc|gradlew?|mvn)\b|\bpython\S*\s+\S*bench\.py\b|\b(cargo|go|dotnet)\s+(test|build)\b|\bclaude\s+plugin\s+test\b/i
// A command delegate already refused once goes through when asked again ("do it here").
const waived = new Set<string>()

const gateRun = async ($: EngineInterface, command: string) => {
  // A heredoc body is data (a script, a test file), not a command this session runs.
  const head = command.split(/<<-?\s*['"]?\w+/)[0]!
  // CLAUDE_CONFIG_DIR= already sends the run to another account; ssh runs on another machine and may change it or prompt for a key.
  if (!HEAVY_RUN.test(head) || /\bCLAUDE_CONFIG_DIR=/.test(head) || /\bssh\s/.test(head) || waived.has(command) || !(await connected($, 'run'))) return undefined
  waived.add(command)
  return `frugal-mod: send this to ${DELEGATE_TOOL} with kind "run", the exact command as "check" and a brief asking for the failures; it runs the command once in bash and returns its real exit code and output. If it says "Do it here", run the same command again and it goes through.`
}

const connected = async ($: EngineInterface, kind: Kind) =>
  (await isOn($, 'delegate')) && (await read($, accounts)).some(a => a.enabled && a.status === 'connected' && serves(a, kind))

// Reads and searches the main session made this turn; past the budget the rest of the exploration goes to delegate, asked once.
const EXPLORE_BUDGET = 4
let explored = { turn: -1, calls: 0, agent: false, nudged: false }
const thisTurn = async ($: EngineInterface) => {
  const t = (await read($, turn))?.startedAt ?? 0
  if (explored.turn !== t) explored = { turn: t, calls: 0, agent: false, nudged: false }
  return explored
}

const gateExplore = async ($: EngineInterface, agentId?: string) => {
  if (agentId) return undefined
  const x = await thisTurn($)
  if (++x.calls <= EXPLORE_BUDGET || x.nudged || !(await connected($, 'search'))) return undefined
  x.nudged = true
  return `frugal-mod: ${EXPLORE_BUDGET} reads and searches this turn already. Hand the rest of this exploration to ${DELEGATE_TOOL} (kind "search", pieces in "tasks") and work from its summary. If you need the raw text here, call again and it goes through.`
}

// A subagent of this session runs on the main account; a secondary account can take the same brief.
const gateAgent = async ($: EngineInterface, agentId?: string) => {
  if (agentId) return undefined
  const x = await thisTurn($)
  if (x.agent || !(await connected($, 'search'))) return undefined
  x.agent = true
  return `frugal-mod: a subagent here runs on the main account. Give this to ${DELEGATE_TOOL} instead (kind "search", "edit" or "run", one brief per piece in "tasks"). If it needs this session's own tools, call again and it goes through.`
}

const setTask = async ($: EngineInterface, id: string, patch: Partial<Task>) =>
  update($, tasks, list => list.map(t => t.id === id ? { ...t, ...patch } : t))

// Tasks in flight per account, so parallel work spreads instead of piling on one account.
const busy: Record<string, number> = {}

// Runs one prompt on the first account that answers, marking accounts that hit a limit.
const ask = async ($: EngineInterface, module: string, kind: Kind, prompt: string, cwd: string, skip: string[] = [], pick: Pick = {}) => {
  const now = await $.clock.now()
  const day = new Date(now).toISOString().slice(0, 10)
  const order = (await read($, providers))[module] ?? []
  for (const a of candidates(await read($, accounts), kind, order, now, day, busy).filter(a => !skip.includes(a.id))) {
    busy[a.id] = (busy[a.id] ?? 0) + 1
    const r = await run($, a, kind, prompt, cwd, pick).finally(() => { busy[a.id] = (busy[a.id] ?? 1) - 1 })
    await update($, accounts, list => list.map(x => x.id !== a.id ? x : r.limited
      ? { ...x, exhaustedUntil: now + HOUR }
      : { ...x, day, used: (x.day === day ? x.used : 0) + 1 }))
    await $.store.set('accounts', await read($, accounts))
    if (!r.limited) return { account: a, ...r }
  }
}

// The engine runs no shell and cmd.exe fails on bash syntax, so Windows checks run in Git Bash (System32's bash.exe is WSL).
const runCheck = async ($: EngineInterface, check: string, cwd: string) => {
  const bash = (await $.env.get('OS')) === 'Windows_NT' ? `${(await $.env.get('ProgramFiles')) ?? 'C:\\Program Files'}\\Git\\bin\\bash.exe` : 'bash'
  try {
    const c = await $.process.run([bash, '-c', check], { cwd, timeoutMs: 5 * 60_000 })
    return { code: c.exitCode, out: `${c.stdout}${c.stderr}`.trim() }
  } catch (err) {
    return { code: 127, out: `the check did not start or did not finish in 5 minutes: ${String(err)}` }
  }
}

// The command could not run on this machine (missing tool, no permission, timeout); the code was never tested.
const ENV_FAIL = /command not found|is not recognized as an internal|permission denied while trying to connect to the docker|cannot connect to the docker daemon|docker daemon is not running/i
const envFailure = (code: number, out: string) => [124, 126, 127].includes(code) || (code !== 0 && ENV_FAIL.test(out))
const doHere = (check: string, code: number, out: string) =>
  `frugal-mod: check \`${check}\` exited ${code} and could not run here:\n${out.slice(-800)}\nDo it here: run the same command again in your shell and it goes through.`

// One brief through the ladder: run, its check, the inspector for edits, up to MAX_TRIES accounts.
// A run's check runs once, here, first; its exit code and output are the result, and an agent only explains a failure.
const runBrief = async ($: EngineInterface, id: string, brief: string, kind: Kind, check: string | undefined, cwd: string, verify: boolean, pick: Pick) => {
  await update($, tasks, list => [...list, { id, title: brief.split('\n')[0]!.slice(0, 60), account: '', model: '', status: 'running' as TaskStatus, tries: 0 }].slice(-20))
  let header = ''
  if (check && kind === 'run') {
    await setTask($, id, { status: 'checking' })
    const c = await runCheck($, check, cwd)
    if (envFailure(c.code, c.out) || c.code === 0) {
      await setTask($, id, { status: c.code === 0 ? 'accepted' : 'escalated' })
      return c.code === 0 ? `frugal-mod: check \`${check}\` exited 0, passed:\n${c.out.slice(-300)}` : doHere(check, c.code, c.out)
    }
    header = `frugal-mod: check \`${check}\` exited ${c.code}, failed:\n${c.out.slice(-800)}\n\nAgent notes, not a test result:\n`
    brief = `${brief}\n\nThe command \`${check}\` already ran and exited ${c.code}. The end of its output:\n${c.out.slice(-8000)}\n\n` +
      'Find the cause of each failure from this output and the code. Do not run the command again.'
    kind = 'search'
    check = undefined
  } else if (check) {
    brief = `${brief}\n\nDone when this bash command exits 0: ${check}`
  }
  let failure = '', passed = ''
  const tried: string[] = []
  for (let n = 1; n <= MAX_TRIES; n++) {
    const diff = failure && kind === 'edit' ? (await $.process.run(['git', 'diff', '--stat'], { cwd })).stdout : ''
    const prompt = `${brief}${failure ? `\n\nA previous attempt failed: ${failure}${diff ? `\nCurrent git diff --stat:\n${diff}` : ''}` : ''}\n\nReply in under 150 words: what you found or changed, with file paths.`
    const r = await ask($, 'delegate', kind, prompt, cwd, tried, pick)
    if (!r) break
    tried.push(r.account.id)
    await setTask($, id, { account: r.account.id, model: modelFor(r.account, kind, pick), tries: n, status: 'running' })
    if (!r.ok) { failure = r.text.slice(-400); continue }
    if (check) {
      await setTask($, id, { status: 'checking' })
      const c = await runCheck($, check, cwd)
      if (envFailure(c.code, c.out)) {
        await setTask($, id, { status: 'escalated' })
        return `${doHere(check, c.code, c.out)}\n\nChanges by ${r.account.id}, untested:\n${r.text.slice(0, 2000)}`
      }
      if (c.code !== 0) {
        failure = `check \`${check}\` failed: ${c.out.slice(-400)}`
        await setTask($, id, { status: 'retrying' })
        continue
      }
      passed = c.out.slice(-300)
    }
    if (verify && kind === 'edit') {
      await setTask($, id, { status: 'inspecting' })
      const v = await ask($, 'verify', 'review', `Brief:\n${brief}\n\nResult:\n${r.text}\n\nDoes the result satisfy the brief? Answer PASS, or FAIL: <reason>.`, cwd)
      if (v?.ok && /^\W*FAIL/i.test(v.text)) {
        failure = `inspector (${v.account.id}): ${v.text.slice(0, 300)}`
        await setTask($, id, { status: 'retrying' })
        continue
      }
    }
    await setTask($, id, { status: 'accepted' })
    const done = `Done by ${r.account.id} (${modelFor(r.account, kind, pick)})`
    return `${header}${check ? `${done}; check \`${check}\` exited 0, passed:\n${passed}\n\nAgent notes:` : `${done}.`}\n${r.text.slice(0, 2000)}`
  }
  await setTask($, id, { status: 'escalated' })
  return `${header}frugal-mod: no secondary account finished this${failure ? ` (${failure})` : ''}. Do it here.`
}

export const registerDelegate = (on: On) => {
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const deny = await gateRun($, e.command)
    return deny ? { deny } : next(e)
  })
  on('tool.call', { tool: 'PowerShell' }, async ($, e, next) => {
    const deny = await gateRun($, (e as unknown as { command: string }).command)
    return deny ? { deny } : next(e)
  })

  for (const tool of ['Read', 'Grep', 'Glob'] as const) {
    on('tool.call', { tool }, async ($, e, next) => {
      const deny = await gateExplore($, e.agentId)
      return deny ? { deny } : next(e)
    })
  }
  on('tool.call', { tool: 'Agent' }, async ($, e, next) => {
    const deny = await gateAgent($, e.agentId)
    return deny ? { deny } : next(e)
  })

  on('tool.call', { tool: DELEGATE_TOOL }, async ($, e) => {
    const input = e as unknown as { task?: string; tasks?: string[]; kind: Kind; check?: string; level?: Level; effort?: string }
    const briefs = [...(input.tasks ?? []), ...(input.task ? [input.task] : [])]
    if (!briefs.length) return { result: 'frugal-mod: give a task or tasks.', text: 'frugal-mod: give a task or tasks.' }
    const cwd = (await $.session.cwd()) ?? '.'
    const verify = await isOn($, 'verify')
    const results = await Promise.all(briefs.map((brief, i) =>
      runBrief($, `${e.tool_use_id}${briefs.length > 1 ? `-${i + 1}` : ''}`, brief, input.kind, input.check, cwd, verify, { level: input.level, effort: input.effort })))
    const text = results.length === 1 ? results[0]! : results.map((r, i) => `## Task ${i + 1}\n${r}`).join('\n\n')
    return { result: text, text }
  })

  // Rules are classified on a secondary or free account; with none, the user judges the raw text.
  on('command.run', { command: 'frugal:reflect' }, async $ => {
    const cwd = (await $.session.cwd()) ?? '.'
    const key = `reflect:${cwd}`
    const queue = ((await $.store.get(key)) as string[] | undefined) ?? []
    if (!queue.length) return { text: 'Nothing queued: frugal-mod saves corrections and "remember:" notes while /frugal is on.' }
    const saved: string[] = []
    for (const item of queue) {
      const c = await ask($, 'reflect', 'classify', `${REFLECT_PROMPT}\n\n${item}`, cwd)
      const rule = c?.ok ? c.text.split('\n')[0]!.trim() : item
      if (/^\W*SKIP\b/i.test(rule)) continue
      const pick = await $.ui.ask(`Save this rule to CLAUDE.local.md? ${rule}`, ['Save', 'Skip']).catch(() => 'Skip')
      if (pick === 'Save') saved.push(rule)
    }
    await $.store.set(key, [])
    if (!saved.length) return { text: `Reviewed ${queue.length}; nothing saved.` }
    const path = `${cwd}/CLAUDE.local.md`
    const before = (await $.fs.exists(path)) ? await $.fs.read(path) : ''
    await $.fs.write(path, mergeRules(before, saved))
    return { text: `Saved ${saved.length} of ${queue.length} to CLAUDE.local.md.` }
  })
}

const REFLECT_PROMPT = 'The user said this to a coding agent. If it states a preference or rule that should hold in future sessions of this project, rewrite it as one imperative rule under 20 words. If it only applies to the task at hand, answer SKIP. Answer with the rule or SKIP only.'
const OPEN = '<!-- frugal-reflect -->'
const CLOSE = '<!-- /frugal-reflect -->'
const MAX_RULES = 30

// Rules live in one marked section, deduplicated, newest kept when over the cap.
export const mergeRules = (file: string, rules: string[]) => {
  const start = file.indexOf(OPEN)
  const end = file.indexOf(CLOSE)
  const has = start >= 0 && end > start
  const old = has ? file.slice(start + OPEN.length, end).split('\n').map(l => l.replace(/^- /, '').trim()).filter(Boolean) : []
  const seen = new Set<string>()
  const all = [...old, ...rules].reverse().filter(r => !seen.has(r.toLowerCase()) && seen.add(r.toLowerCase())).reverse().slice(-MAX_RULES)
  const section = `${OPEN}\n${all.map(r => `- ${r}`).join('\n')}\n${CLOSE}`
  if (has) return file.slice(0, start) + section + file.slice(end + CLOSE.length)
  return `${file}${file && !file.endsWith('\n') ? '\n' : ''}${file ? '\n' : ''}${section}\n`
}
