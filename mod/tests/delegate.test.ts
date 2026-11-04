import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { candidates, choose } from '../hooks/delegate'
import type { Account } from '../types'

const DAY = '2026-11-01'
const NOW = Date.parse(`${DAY}T12:00:00Z`)
const START = { cwd: '/repo', surface: 'desktop', isInteractive: true } as const
const acc = (id: string, patch: Partial<Account> = {}): Account =>
  ({ id, preset: 'claude', kind: 'claude-cli', enabled: true, status: 'connected', used: 0, day: DAY, ...patch })
const ok = (stdout: string) => ({ exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false })
const fail = (stderr: string) => ({ exitCode: 1, stdout: '', stderr, isStdoutTruncated: false, isStderrTruncated: false })

type Run = (argv: readonly string[], env: Record<string, string>, stdin?: string) => ReturnType<typeof ok>

const engine = (on: On, runs: Run, store: Record<string, unknown>, vars: Record<string, string> = {}) => {
  on('store.get', (_, e) => ({ value: store[e.key] }))
  on('store.set', (_, e) => { store[e.key] = e.value; return { value: undefined } })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.messages', () => ({ value: [] as never }))
  on('command.register', () => ({ value: {} as never }))
  on('tool.register', () => ({ value: {} as never }))
  on('mcp.call', () => ({ value: { content: [], isError: true } }))
  on('clock.now', () => ({ value: NOW }))
  on('clock.every', () => ({ value: (() => undefined) as never }))
  on('env.get', (_, e) => ({ value: vars[e.name] }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('process.run', (_, e) => ({ value: runs(e.argv, e.init?.env ?? {}, e.init?.stdin as string | undefined) }))
}
const isCheck = (argv: readonly string[]) => argv[0] === 'bash' || argv[0]!.endsWith('bash.exe')

test('the least used account goes first; capped, limited, disabled and HTTP-only accounts are skipped for edits', () => {
  const list = [
    acc('busy', { used: 5 }),
    acc('idle', { used: 1 }),
    acc('capped', { cap: 2, used: 2 }),
    acc('limited', { exhaustedUntil: NOW + 1 }),
    acc('off', { enabled: false }),
    acc('gemini', { preset: 'gemini', kind: 'openai-compatible' }),
    acc('waiting', { status: 'pending' }),
  ]
  expect(candidates(list, 'edit', [], NOW, DAY).map(a => a.id)).toEqual(['idle', 'busy'])
  expect(candidates(list, 'review', ['gemini', 'busy'], NOW, DAY).map(a => a.id)).toEqual(['gemini', 'busy'])
  expect(candidates([acc('old', { used: 9, day: '2026-10-31' }), acc('new', { used: 1 })], 'search', [], NOW, DAY).map(a => a.id)).toEqual(['old', 'new'])
})

test('a search runs on a secondary Claude login and comes back short', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('claude-b', { configDir: '/home/me/.claude-b' })] }
  const seen: Record<string, string>[] = []
  engine(on, (argv, env) => { seen.push(env); return ok(`found in a.ts (${argv[0]})`) }, store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'find the parser', kind: 'search' })
  expect(r.text).toContain('Done by claude-b (haiku, low effort)')
  expect(r.text).toContain('found in a.ts (claude)')
  expect(seen[0]?.CLAUDE_CONFIG_DIR).toBe('/home/me/.claude-b')
  expect((store.accounts as Account[])[0]?.used).toBe(1)
})

test('an account at its limit is marked and the next one takes the task', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('claude-b'), acc('codex', { preset: 'codex', kind: 'codex-cli', used: 1 })] }
  engine(on, argv => argv[0] === 'claude' ? fail('Claude usage limit reached') : ok('done'), store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'find where x is set', kind: 'search' })
  expect(r.text).toContain('Done by codex')
  expect((store.accounts as Account[]).find(a => a.id === 'claude-b')?.exhaustedUntil).toBe(NOW + 3_600_000)
})

test('a failing check retries elsewhere, then hands the task back', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('a'), acc('b')] }
  engine(on, argv => isCheck(argv) ? fail('1 test failed') : ok('edited a.ts'), store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'fix the off-by-one', kind: 'edit', check: 'npm test' })
  expect(r.text).toContain('Do it here')
  expect(r.text).toContain('npm test')
  expect(r.text).not.toContain('passed')
})

test('on Windows a run checks once in Git Bash, not cmd; a pass needs no agent', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('a')] }
  const argvs: (readonly string[])[] = []
  engine(on, argv => { argvs.push(argv); return isCheck(argv) ? ok('ok 12 tests') : ok('PASS') }, store, { OS: 'Windows_NT', ProgramFiles: 'C:\\Program Files' })
  await $.session.start(START)
  const check = 'MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/src golang go test ./...'
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'run the tests', kind: 'run', check })
  expect(argvs).toEqual([['C:\\Program Files\\Git\\bin\\bash.exe', '-c', check]])
  expect(r.text).toContain('exited 0, passed')
  expect(r.text).toContain('ok 12 tests')
})

test('a failing run is its exit code and output; the agent only explains it, from that output', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('a')] }
  const prompts: string[] = []
  engine(on, (argv, _, stdin) => isCheck(argv) ? fail('boom') : (prompts.push(stdin ?? ''), ok('PASS: check-web.sh exited 0')), store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'run the tests', kind: 'run', check: 'go test ./...' })
  const [head, notes] = r.text.split('Agent notes')
  expect(head).toContain('exited 1, failed')
  expect(head).toContain('boom')
  expect(head).not.toContain('PASS')
  expect(notes).toContain('PASS')
  expect(prompts).toHaveLength(1)
  expect(prompts[0]).toContain('boom')
  expect(prompts[0]).toContain('Do not run the command again')
})

test('the agent gets the check; a check that cannot run hands the task back as do it here', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('a')] }
  const prompts: string[] = []
  engine(on, (argv, _, stdin) => isCheck(argv) ? { ...fail('bash: go: command not found'), exitCode: 127 } : (prompts.push(stdin ?? ''), ok('edited a.ts')), store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'fix the parser', kind: 'edit', check: 'go test ./...' })
  expect(prompts[0]).toContain('Done when this bash command exits 0: go test ./...')
  expect(r.text).toContain('exited 127')
  expect(r.text).toContain('Do it here')
  expect(r.text).not.toContain('passed')
})

test('the inspector can reject an edit that passed its check', async ($, on) => {
  const store: Record<string, unknown> = {
    accounts: [acc('a'), acc('b', { used: 3 }), acc('codex', { preset: 'codex', kind: 'codex-cli' })],
    providers: { delegate: ['a', 'b'], verify: ['codex'] },
  }
  const calls: string[] = []
  engine(on, argv => { calls.push(argv[0]!); return argv[0] === 'codex' ? ok('FAIL: renamed only one call site') : ok('edited') }, store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'rename x everywhere', kind: 'edit' })
  expect(r.text).toContain('Do it here')
  expect(r.text).toContain('inspector (codex)')
  expect(calls.filter(c => c === 'codex')).toHaveLength(2)
})

test('several briefs run in parallel, one per account', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('a', { configDir: '/a' }), acc('b', { configDir: '/b' })] }
  const seen: string[] = []
  engine(on, (_, env) => { seen.push(env.CLAUDE_CONFIG_DIR ?? ''); return ok('done') }, store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', tasks: ['find x', 'find y'], kind: 'search' })
  expect(r.text).toContain('## Task 1')
  expect(r.text).toContain('## Task 2')
  expect([...new Set(seen)].sort()).toEqual(['/a', '/b'])
})

test('a level maps to each provider; an effort the provider lacks is fitted', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('claude-b'), acc('codex', { preset: 'codex', kind: 'codex-cli', used: 1 })] }
  const argvs: string[][] = []
  engine(on, argv => { argvs.push([...argv]); return ok('done') }, store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'split the parser module', kind: 'edit', level: 'heavy' })
  expect(r.text).toContain('Done by claude-b (opus, high effort)')
  expect(argvs[0]).toEqual(expect.arrayContaining(['--model', 'opus', '--effort', 'high']))
  await $.tool.call({ tool: 'mcp__frugal-mod__delegate', tasks: ['find x', 'find y'], kind: 'search', level: 'max' })
  expect(argvs.find(a => a[0] === 'codex')).toContain('model_reasoning_effort=xhigh')
})

test('per-provider rules: Claude has no minimal effort, HTTP providers pick a strong model for heavy work', () => {
  expect(choose(acc('c'), 'search', { effort: 'minimal' })).toEqual({ model: 'haiku', effort: 'low' })
  expect(choose(acc('c'), 'edit', { effort: 'xhigh' })).toEqual({ model: 'sonnet', effort: 'xhigh' })
  const gem = acc('g', { preset: 'gemini', kind: 'openai-compatible', models: ['gemini-pro', 'gemini-flash', 'text-embedding'] })
  expect(choose(gem, 'review')).toEqual({ model: 'gemini-flash', effort: undefined })
  expect(choose(gem, 'review', { level: 'heavy' })).toEqual({ model: 'gemini-pro', effort: undefined })
})

test('Codex runs the model of the level when its login lists it, else the one in config.toml', async ($, on) => {
  const models = ['gpt-6-luna', 'gpt-6.1-sol']
  const store: Record<string, unknown> = { accounts: [acc('codex', { preset: 'codex', kind: 'codex-cli', models })] }
  const argvs: string[][] = []
  engine(on, argv => { argvs.push([...argv]); return ok('done') }, store)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'find x', kind: 'search' })
  expect(r.text).toContain('Done by codex (gpt-6-luna, low effort)')
  expect(argvs[0]).toEqual(expect.arrayContaining(['-m', 'gpt-6-luna']))
  await $.tool.call({ tool: 'mcp__frugal-mod__delegate', task: 'fix y', kind: 'edit', level: 'max' })
  expect(argvs[1]).not.toContain('-m')
  expect(argvs[1]).toEqual(expect.arrayContaining(['-s', 'workspace-write', 'model_reasoning_effort=xhigh']))
})

test('a test run in this session is sent to delegate once, then goes through', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('claude-b')] }
  engine(on, () => ok('done'), store)
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran', text: 'ran' }))
  await $.session.start(START)
  const first = await $.tool.call({ tool: 'Bash', command: 'npm test -- parser' } as never)
  expect(first.deny).toContain('kind "run"')
  const again = await $.tool.call({ tool: 'Bash', command: 'npm test -- parser' } as never)
  expect(again.deny).toBeUndefined()
  const ls = await $.tool.call({ tool: 'Bash', command: 'ls src' } as never)
  expect(ls.deny).toBeUndefined()
  const script = await $.tool.call({ tool: 'Bash', command: "python - <<'EOF'\nprint('npm test')\nEOF" } as never)
  expect(script.deny).toBeUndefined()
  const elsewhere = await $.tool.call({ tool: 'Bash', command: 'CLAUDE_CONFIG_DIR=/b python bench/bench.py run' } as never)
  expect(elsewhere.deny).toBeUndefined()
})

test('past four reads and searches in a turn, the next one is sent to delegate once', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('claude-b')] }
  engine(on, () => ok('done'), store)
  on('tool.call', { tool: 'Read' }, () => ({ result: 'text', text: 'text' }))
  await $.session.start(START)
  const reads = []
  for (let i = 0; i < 6; i++) reads.push(await $.tool.call({ tool: 'Read', file_path: `/repo/f${i}.ts` } as never))
  expect(reads.map(r => r.deny === undefined)).toEqual([true, true, true, true, false, true])
  expect(reads[4]!.deny).toContain('kind "search"')
})

test('a subagent of the main session is sent to delegate once', async ($, on) => {
  const store: Record<string, unknown> = { accounts: [acc('claude-b')] }
  engine(on, () => ok('done'), store)
  on('tool.call', { tool: 'Agent' }, () => ({ result: 'done', text: 'done' }))
  await $.session.start(START)
  const first = await $.tool.call({ tool: 'Agent', description: 'x', prompt: 'find x', subagent_type: 'Explore' } as never)
  expect(first.deny).toContain('runs on the main account')
  const again = await $.tool.call({ tool: 'Agent', description: 'x', prompt: 'find x', subagent_type: 'Explore' } as never)
  expect(again.deny).toBeUndefined()
})

test('with no secondary account connected nothing is gated', async ($, on) => {
  engine(on, () => ok('done'), { accounts: [acc('claude-b', { status: 'pending' })] })
  on('tool.call', { tool: 'Read' }, () => ({ result: 'text', text: 'text' }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran', text: 'ran' }))
  await $.session.start(START)
  for (let i = 0; i < 6; i++) expect((await $.tool.call({ tool: 'Read', file_path: `/repo/f${i}.ts` } as never)).deny).toBeUndefined()
  expect((await $.tool.call({ tool: 'Bash', command: 'npm test' } as never)).deny).toBeUndefined()
})

test('a session started by delegate does not delegate again', async ($, on) => {
  const registered: string[] = []
  const store: Record<string, unknown> = { accounts: [acc('claude-b')] }
  on('store.get', (_, e) => ({ value: store[e.key] }))
  on('store.set', (_, e) => { store[e.key] = e.value; return { value: undefined } })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.messages', () => ({ value: [] as never }))
  on('command.register', () => ({ value: {} as never }))
  on('mcp.call', () => ({ value: { content: [], isError: true } }))
  on('clock.now', () => ({ value: NOW }))
  on('clock.every', () => ({ value: (() => undefined) as never }))
  on('env.get', (_, e) => ({ value: e.name === 'FRUGAL_MOD_DELEGATED' ? '1' : undefined }))
  on('tool.register', (_, e) => { registered.push(e.name); return { value: {} as never } })
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran', text: 'ran' }))
  await $.session.start(START)
  expect(registered).not.toContain('delegate')
  expect((await $.tool.call({ tool: 'Bash', command: 'npm test' } as never)).deny).toBeUndefined()
})
