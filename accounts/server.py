"""frugal accounts: secondary Claude, Codex, Grok and OpenCode logins that take delegated work.

One file, stdlib only, four roles:
  server.py                 MCP server (stdio) exposing `delegate` while an account is set up
  server.py hook-prompt     UserPromptSubmit hook: the delegation rule, then a one-line reminder
  server.py hook-pretool    PreToolUse hook: sends test runs, long explorations (shell reads too), long edit runs and subagents to delegate
  server.py hook-stop       Stop hook (Claude Desktop only): a side agent reads the session and may wake Claude with a heads-up
  server.py list | add <claude|codex|grok> <id> | add opencode <id> <provider/model> | opencode-training <on|off> | remove <id> | login <id> | selftest
Nothing happens while ~/.frugal/accounts.json lists no account, so frugal without accounts is unchanged.
"""
import concurrent.futures, contextlib, datetime, hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile, threading, time, tomllib, traceback
from pathlib import Path

HOME = Path.home()
DIR = HOME / ".frugal"
FILE = DIR / "accounts.json"
TOOL = "mcp__plugin_frugal_accounts__delegate"
DELEGATED = "FRUGAL_DELEGATED"  # set on every delegated run, so it never delegates again
ROLES = "FRUGAL_ROLES"  # "off" from Frugal Deck's settings: a session may also delegate to its own account
LOCK = threading.Lock()

LEVELS = ["light", "standard", "heavy", "max"]
EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
# Per provider: model and effort per level, and the efforts it takes (claude --help, ~/.codex/models_cache.json).
RULES = {
    "claude": {"efforts": ["low", "medium", "high", "xhigh", "max"],
               "levels": {"light": ("haiku", "low"), "standard": ("sonnet", "medium"), "heavy": ("opus", "high"), "max": ("opus", "max")}},
    "codex": {"efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
              "levels": {"light": ("gpt-6-luna", "low"), "standard": ("gpt-6.1-sol", "medium"), "heavy": ("gpt-6.1-sol", "high"), "max": ("gpt-6-astra", "xhigh")}},
    # grok-4.6 rejects minimal and max ("use one of: xhigh, high, medium, low") yet still exits 0.
    "grok": {"efforts": ["low", "medium", "high", "xhigh"],
             "levels": {"light": ("grok-4.6", "low"), "standard": ("grok-4.6", "medium"), "heavy": ("grok-4.6", "high"), "max": ("grok-4.6", "xhigh")}},
    # OpenCode runs the one model its account names; --variant is per provider, and one a model lacks is ignored.
    "opencode": {"efforts": ["low", "medium", "high"],
                 "levels": {"light": (None, "low"), "standard": (None, "medium"), "heavy": (None, "high"), "max": (None, "high")}},
}
DEFAULT_LEVEL = {"search": "standard", "run": "standard", "edit": "standard", "note": "light"}  # light (haiku, low) missed most of a 5-question lookup; pass level "light" only for trivial greps. A heads-up note is one judgment, which haiku makes as well as sonnet
LIMIT = re.compile(r"usage limit|rate limit|limit reached|session limit|quota|too many requests|\b429\b", re.I)
# A logged-out account fails every task until the user signs in again, so it leaves the pool and the user is told.
AUTH = re.compile(r"\b401\b|not logged in|run /login|failed to authenticate|oauth token (has )?(expired|been revoked)"
                  r"|invalid (api|x-api) key|authentication_error|not authenticated|not signed in|login required", re.I)
HEAVY_RUN = re.compile(r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck)\b|(?<![.\w-])(pytest|jest|vitest|tsc|gradlew|mvn)(\.(bat|cmd|exe))?(?![.\w/-])|(?<![.\w/\\-])gradle(?![.\w/-])|\bpython\S*\s+\S*bench\.py\b"
                       r"|\b(cargo|go|dotnet)\s+(test|build)\b|\bclaude\s+plugin\s+test\b", re.I)
CODEX_RULES = (
    "Work rules: report results, not process; no filler, preamble or closing summary. Keep exact terms, code, commands, errors, "
    "numbers and every not, never and only. Verify APIs, versions and flags; never guess. For code: reuse what the codebase has, "
    "then the stdlib or native features, then installed dependencies; never add one for a few lines. Minimum code: no unrequested "
    "features, files, abstractions or config. Fix bugs at the root cause, and fix siblings with the same bug. Never cut validation, "
    "security or error handling that prevents data loss."
)
EXPLORE_BUDGET = 5
# Edits per prompt before the next one goes to delegate: of 31,524 prompts, 1,340 made more than 3 (93% across 2+ files) and 51 delegated.
EDIT_BUDGET = 3
EDITS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# The session's own notes (memory, plans, settings under .claude*) and its scratchpad are not delegable work.
OWN_FILE = re.compile(r"[\\/](\.claude[^\\/]*|Temp[\\/]claude)[\\/]", re.I)
# Shell commands that only read. A chain of them counts against EXPLORE_BUDGET as Read and Grep do: in one day 816 shell reads went uncounted.
READ_ONLY = re.compile(r"(cat|head|tail|less|grep|egrep|fgrep|rg|ls|dir|find|tree|wc|stat|file|du|awk|sed|jq|sort|uniq|cut|column|nl|diff|cmp"
                       r"|basename|dirname|realpath|which|where|git\s+(log|show|diff|status|grep|ls-files|blame|branch|rev-parse)"
                       r"|Get-Content|Get-ChildItem|Select-String|Test-Path)(\s|$)", re.I)
NEUTRAL = re.compile(r"(cd|echo|printf|true)(\s|$)")
TIMEOUT = {"heavy": 1200, "max": 1800}  # seconds per agent run; heavy edits ran past 10 minutes and started over
HEADS_UP = DIR / "heads-up.json"
JOBS = DIR / "jobs.jsonl"  # one line per delegated brief as it starts and ends; Frugal Deck's details panel reads it
HEADS_UP_CALLS = 6  # tool calls in a turn before the side agent looks; shorter turns are not worth a check
HEADS_UP_MAX = 4  # notes per session, as in Claude Code's own "You should know"
HEADS_UP_CHECKS = 8  # checks per session: each one costs the secondary account, and a spent account leaves delegate for an hour
HEADS_UP_ASK = (
    "You watch a Claude Code session on the user's behalf. Below is the end of its transcript. Name the one thing the user most "
    "needs to know but could miss: a risk Claude took or left (a secret exposed, data that can be lost, a destructive or "
    "outward-facing step), a claim Claude made without verifying it, something asked that was not done, or a side effect the "
    "user would not expect. Skip anything Claude already told the user plainly, style points and guesses. If nothing clears "
    "that bar, reply exactly: none. Otherwise reply with one plain sentence under 40 words, in the user's language, no preamble."
)
# OpenCode Zen models that may train on, or warn against, what they receive (opencode.ai/docs/zen). Blocked until the user turns opencode-training on.
TRAINING_MODELS = ("big-pickle", "mimo", "flint", "iceman", "inkling", "nemotron")
MAX_TRIES = 3
HOUR = 3600


def load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"accounts": []}


@contextlib.contextmanager
def file_lock():
    """Serializes read-modify-write of frugal's files across processes: the MCP server and every session's hooks."""
    path = DIR / "files.lock"
    DIR.mkdir(exist_ok=True)
    fd = None
    for _ in range(500):  # 5 seconds, then go ahead unlocked rather than stall delegate or a hook
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL)
            break
        except (FileExistsError, PermissionError):  # Windows: PermissionError while another process deletes the lock
            try:
                if time.time() - path.stat().st_mtime > 10:
                    path.unlink()  # left by a killed process
            except OSError:
                pass
            time.sleep(0.01)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            path.unlink(missing_ok=True)


def save(data):
    DIR.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, FILE)  # atomic, so parallel sessions never leave a half-written file


def today():
    return datetime.date.today().isoformat()


def same(p):
    return os.path.normcase(os.path.abspath(p))


def own(a):
    # The account this session runs on is its primary, never one of its secondaries.
    mine = os.environ.get("CLAUDE_CONFIG_DIR") or str(HOME / ".claude")
    return os.environ.get(ROLES) != "off" and bool(a.get("config_dir")) and same(a["config_dir"]) == same(mine)


def session_mcp(cwd):
    """This session's MCP servers, user scope plus the project's. A delegate gets only the ones a task names; Grok never,
    and Codex or OpenCode never a server in claude_only_mcp: which agents may reach a server's data is each project's rule.
    In a Frugal Deck chat, FRUGAL_DECK_MCP holds the chat's resolved set: the account's servers its allow and deny lists pass, plus the Deck's."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        data = json.loads((Path(folder) / ".claude.json" if folder else HOME / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    servers = dict(data.get("mcpServers") or {})
    for project, conf in (data.get("projects") or {}).items():
        if same(project) == same(cwd):
            servers.update(conf.get("mcpServers") or {})
    try:
        deck = json.loads(os.environ.get("FRUGAL_DECK_MCP") or "null")
    except ValueError:
        deck = None
    if not isinstance(deck, dict):
        return servers
    def entries(name, conf):  # the forms Claude Code's allowedMcpServers and deniedMcpServers match a server by
        target = {"serverUrl": conf["url"]} if conf.get("url") else {"serverCommand": [conf.get("command"), *(conf.get("args") or [])]}
        return {"serverName": name}, target
    allowed, denied = deck.get("allowed") or [], deck.get("denied") or []
    servers = {n: c for n, c in servers.items() if any(e in allowed for e in entries(n, c)) and not any(e in denied for e in entries(n, c))}
    servers.update(deck.get("mcpServers") or {})
    return servers


def with_auth(servers, now=None):
    """Hands this session's MCP OAuth access token to the delegate as a header, so no account logs in to a server twice.
    The delegate never sees the refresh token, so it never rotates the session's login. Returns (servers, expired names)."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        tokens = json.loads((Path(folder) if folder else HOME / ".claude").joinpath(".credentials.json").read_text(encoding="utf-8")).get("mcpOAuth") or {}
    except (OSError, ValueError):
        return servers, []  # frugal: macOS keeps these in the keychain; read it with `security find-generic-password` if needed
    now = time.time() if now is None else now
    out, expired = {}, []
    for name, conf in servers.items():
        headers = conf.get("headers") or {}
        t = next((t for t in tokens.values() if t.get("serverName") == name and t.get("serverUrl") == conf.get("url") and t.get("accessToken")), None)
        if not t or not conf.get("url") or any(k.lower() == "authorization" for k in headers):
            out[name] = conf
        elif t.get("expiresAt") and t["expiresAt"] / 1000 < now + 300:
            expired.append(name)
        else:
            out[name] = {**conf, "headers": {**headers, "Authorization": f"Bearer {t['accessToken']}"}}
    return out, expired


def mcp_rule(server, tool=None):
    # Claude Code names a server's tools mcp__<name>__<tool>, with characters outside [A-Za-z0-9_-] turned into _.
    return "mcp__" + "__".join(re.sub(r"[^A-Za-z0-9_-]", "_", x) for x in (server, tool) if x)


def list_tools(conf):
    """An MCP server's tools, over streamable HTTP or from a stdio server started for the purpose, or None when they cannot be listed."""
    import queue, urllib.request
    # Cloudflare's browser check (error 1010) refuses urllib's default Python-urllib agent.
    head = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": "frugal", **(conf.get("headers") or {})}
    proc, lines, end = None, queue.Queue(), time.time() + 30

    def call(body):
        if proc:
            proc.stdin.write(json.dumps(body) + "\n")
            proc.stdin.flush()
            while "id" in body:
                if (m := json.loads(lines.get(timeout=max(0.1, end - time.time())))).get("id") == body["id"]:
                    return m
            return None
        with urllib.request.urlopen(urllib.request.Request(conf["url"], json.dumps(body).encode(), head), timeout=20) as r:
            if sid := r.headers.get("Mcp-Session-Id"):
                head["Mcp-Session-Id"] = sid
            text = r.read().decode("utf-8")
        data = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
        return json.loads(data[-1] if data else text) if "id" in body else None

    try:
        if not conf.get("url"):
            proc = subprocess.Popen([shutil.which(conf["command"]) or conf["command"], *(conf.get("args") or [])], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={**os.environ, **(conf.get("env") or {})},
                                    text=True, encoding="utf-8", start_new_session=os.name != "nt")
            threading.Thread(target=lambda: [lines.put(x) for x in proc.stdout], daemon=True).start()
        call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "frugal", "version": "1"}}})
        call({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor = [], None
        while True:
            res = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", **({"params": {"cursor": cursor}} if cursor else {})})["result"]
            tools += res["tools"]
            if not (cursor := res.get("nextCursor")):
                return tools
    except (OSError, ValueError, KeyError, TypeError, queue.Empty):
        return None
    finally:
        if proc:
            kill_tree(proc)


def cached_tools(name, tools=None):
    """Saves a server's listed tools, or with none returns the last saved list. Safe when stale: an unlisted tool is never allowed."""
    path = DIR / "mcp-tools.json"
    with LOCK:
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        if tools is None:
            return cache.get(name)
        cache[name] = [{"name": t["name"], "annotations": t.get("annotations") or {}} for t in tools]
        path.write_text(json.dumps(cache), encoding="utf-8")
        return tools


def mcp_rules(servers):
    """A delegate gets only the tools a server marks readOnlyHint and is denied the rest: it can read a ticket, never answer one.
    Returns (allowed, denied, servers whose tools could not be listed, {server: its read-only tool names})."""
    allow, deny, failed, ro = [], [], [], {}
    for name, conf in servers.items():
        tools = list_tools(conf)
        if tools is None:
            tools = list_tools(conf)  # a server still connecting often answers the second time
        tools = cached_tools(name) if tools is None else cached_tools(name, tools)
        if tools is None:
            failed.append(name)
            continue
        ro[name] = [t["name"] for t in tools if (t.get("annotations") or {}).get("readOnlyHint")]
        for t in tools:
            (allow if t["name"] in ro[name] else deny).append(mcp_rule(name, t["name"]))
    return allow, deny, failed, ro


def mcp_for(kind, servers, ro):
    """Codex -c overrides, or OpenCode's config, for this session's servers: (args, env). Header and env values go in env vars,
    never on the command line. Codex enables only the read-only tools; OpenCode denies the server's other tools."""
    args, env, conf = [], {}, {"mcp": {}, "permission": {}}
    for i, (name, c) in enumerate(servers.items()):
        key, heads, envs = mcp_rule(name)[5:], c.get("headers") or {}, c.get("env") or {}
        if kind == "opencode":
            conf["mcp"][key] = ({"type": "remote", "url": c["url"], "headers": heads} if c.get("url")
                                else {"type": "local", "command": [c["command"], *(c.get("args") or [])], "environment": envs})
            conf["permission"].update({f"{key}_*": "deny", **{f"{key}_{mcp_rule(t)[5:]}": "allow" for t in ro[name]}})  # the last matching rule wins
            continue
        p = f"mcp_servers.{key}."
        if c.get("url"):
            var = {h: f"FRUGAL_MCP_{i}_{j}" for j, h in enumerate(heads)}
            env.update({var[h]: v for h, v in heads.items()})
            args += ["-c", p + "url=" + json.dumps(c["url"])]
            args += ["-c", p + "env_http_headers={" + ", ".join(f"{json.dumps(h)} = {json.dumps(v)}" for h, v in var.items()) + "}"] if var else []
        else:
            env.update(envs)
            args += ["-c", p + "command=" + json.dumps(c["command"]), "-c", p + "args=" + json.dumps(c.get("args") or [])]
            args += ["-c", p + "env_vars=" + json.dumps(list(envs))] if envs else []
        args += ["-c", p + "enabled_tools=" + json.dumps(ro[name])]
    if kind == "opencode":
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(conf)
    return args, env


def clear_needs_auth(config_dir, names):
    # Claude Code skips a server it marked as needing a login, even when frugal hands it this session's token.
    p = Path(config_dir) / "mcp-needs-auth-cache.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if any(n in d for n in names):
        p.write_text(json.dumps({k: v for k, v in d.items() if k not in names}), encoding="utf-8")


def ready(accounts, now=None):
    now = time.time() if now is None else now
    return [a for a in accounts if a.get("enabled", True) and not a.get("needs_login") and a.get("exhausted_until", 0) <= now and not own(a)]


def active():
    return not os.environ.get(DELEGATED) and bool(ready(load()["accounts"]))


def fit(effort, efforts):
    i = EFFORTS.index(effort) if effort in EFFORTS else 1
    below = [e for e in efforts if EFFORTS.index(e) <= i]
    return below[-1] if below else efforts[0]


def choose(a, kind, level=None, effort=None):
    rule = RULES[a["kind"]]
    model, base = rule["levels"][level if level in LEVELS else DEFAULT_LEVEL.get(kind, "light")]
    # A Codex model the login does not list falls back to the one in its config.toml.
    if a["kind"] == "codex" and model not in (a.get("models") or []):
        model = None
    if a["kind"] == "opencode":
        model = a["model"]
    return model, fit(effort or base, rule["efforts"])


STRONG = {"claude": 0, "codex": 1, "grok": 2}  # heavy and max: Claude secondaries, then GPT-6.1 Sol, then Grok 4.6; free OpenCode models never


def order(accounts, busy, level=None):
    day = today()
    pool = ready(accounts)
    if level in ("heavy", "max"):
        pool = [a for a in pool if a["kind"] in STRONG]
    rank = (lambda a: STRONG[a["kind"]]) if level in ("heavy", "max") else (lambda a: 0)
    return sorted(pool, key=lambda a: (rank(a), busy.get(a["id"], 0) * 1000 + (a.get("used", 0) if a.get("day") == day else 0)))


def codex_models():
    try:
        cache = json.loads((HOME / ".codex" / "models_cache.json").read_text(encoding="utf-8"))
        return [m["slug"] for m in cache.get("models", []) if m.get("visibility") != "hide"]
    except (OSError, ValueError, KeyError):
        return []


def codex_mcp():
    try:
        return set(tomllib.loads((HOME / ".codex" / "config.toml").read_text(encoding="utf-8")).get("mcp_servers", {}))
    except (OSError, ValueError):
        return set()


def blocked(a, data):
    m = (a.get("model") or "").lower()
    return (a["kind"] == "opencode" and m.startswith("opencode/") and any(t in m for t in TRAINING_MODELS)
            and not data.get("opencode_allow_training", False))


def argv_for(a, kind, model, effort, mcp_off=(), mcp_file=None, mcp_allow=(), mcp_deny=(), mcp_args=()):
    if a["kind"] == "claude" and kind == "note":
        return ["claude", "-p", "--output-format", "json", "--model", model, "--effort", effort, "--tools", "", "--strict-mcp-config"]  # no tools: it only reads the brief
    if a["kind"] == "claude":
        allowed = " ".join(["Bash Read Grep Glob" if kind in ("search", "run") else "Bash Read Grep Glob Edit Write", *mcp_allow])
        tools = ["--allowedTools", allowed] if kind in ("search", "run") else ["--permission-mode", "acceptEdits", "--allowedTools", allowed]
        tools += ["--disallowedTools", " ".join(mcp_deny)] if mcp_deny else []
        # Strict: without it every delegated run starts all of the user's MCP servers (WSL, Python, Node) and leaves them running.
        # --plugin-dir replaces the account's own frugal: a delegate runs the same version as this session, never an older install.
        return ["claude", "-p", "--output-format", "json", "--model", model, "--effort", effort, *tools, "--strict-mcp-config", *(["--mcp-config", mcp_file] if mcp_file else []),
                "--plugin-dir", str(Path(__file__).resolve().parent.parent)]
    if a["kind"] == "grok":
        # A headless grok refuses every call that needs approval and still exits 0, so it always approves; search keeps read tools and the shell, as Claude's does.
        tools = ["--tools", "read_file,grep,list_dir,run_terminal_cmd"] if kind == "search" else []
        # MCP servers come in an agent file; deny rules (server__tool) block the tools that are not read-only, even under --always-approve.
        tools += (["--agent", mcp_file] if mcp_file else []) + [x for r in mcp_deny for x in ("--deny", f"MCPTool({r[5:]})")]
        return ["grok", "--no-auto-update", "-m", model, "--effort", effort, "--always-approve", "--output-format", "json", *tools]
    if a["kind"] == "opencode":
        # --auto approves what is not denied; the plan agent denies edits, so search stays read-only (tested on opencode 1.19.5)
        return ["opencode", "run", "-m", model, "--variant", effort, "--agent", "plan" if kind == "search" else "build", "--auto", "--format", "json"]
    sandbox = ["-s", "workspace-write"] if kind in ("edit", "run") else []
    off = [x for n in mcp_off for x in ("-c", f"mcp_servers.{n}.enabled=false")]  # only servers its config has: an unknown one breaks codex
    return ["codex", "exec", "--skip-git-repo-check", *(["-m", model] if model else []), "-c", f"model_reasoning_effort={effort}", *off, *mcp_args, *sandbox, "-"]


class Cancelled(Exception):
    pass


# One per tools/call: the processes it runs, so a cancel or the end of the session kills them, and the jobs its progress names.
class Request:
    def __init__(self):
        self.lock, self.cancelled, self.procs, self.jobs = threading.Lock(), False, set(), []

    def cancel(self):
        with self.lock:
            self.cancelled, procs = True, list(self.procs)
        for p in procs:
            kill_tree(p)


TL = threading.local()  # .req: the Request this thread works for; unset in hooks and the CLI


def kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdin=subprocess.DEVNULL, capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_tree(cmd, timeout, input=None, **kw):
    # On timeout kill the whole tree: subprocess.run kills only the .cmd shim or shell, and the
    # grandchild keeps the pipes open, so communicate() never returns. None means timed out.
    # Never inherit stdin: in the MCP server it carries the protocol, and a command that reads it would eat requests.
    # On Windows, starting a child that inherits it also blocks until the next message arrives (the hung edit delegates).
    req = getattr(TL, "req", None)
    if req and req.cancelled:
        raise Cancelled
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt", **kw)
    if req:
        with req.lock:
            req.procs.add(p)
            dead = req.cancelled
        if dead:
            kill_tree(p)
    if (job := getattr(TL, "job", None)) is not None:
        job["pid"] = p.pid
        log_job({**job, "status": "running"})  # a hung agent can be found by its pid while it runs
    err, result = "", None
    try:
        out, err = p.communicate(input, timeout=timeout)
        result = p.returncode, out, err
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            err = p.communicate(timeout=10)[1]
        except subprocess.TimeoutExpired:
            pass  # a process that escaped the tree still holds the pipes; give up on it
        result = None
    finally:
        if req:
            with req.lock:
                req.procs.discard(p)
        if job is not None and (result is None or (req and req.cancelled)):
            job["stderr"] = (err or "")[-500:]  # why a timed-out or cancelled agent never answered
    if req and req.cancelled:
        raise Cancelled  # stops the retries too: every try and check runs through here
    return result


def run_cli(argv, prompt, cwd, env, timeout=600):
    exe = shutil.which(argv[0])  # finds npm's .cmd shims on Windows
    if not exe:
        return 127, "", f"{argv[0]} not found"
    tmp = None
    if argv[0] == "grok":  # grok takes no prompt on stdin, and a long one overflows the command line
        fd, tmp = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        argv, prompt = [*argv, "--prompt-file", tmp], None
    try:
        # PWD is inherited from this session's shell; opencode works there instead of in cwd, so it must match
        r = run_tree([exe, *argv[1:]], timeout, input=prompt, cwd=cwd, env={**env, "PWD": cwd} if cwd else env)
    finally:
        if tmp:
            os.unlink(tmp)
    if r is None:
        return 124, "", f"timed out after {timeout // 60} minutes"
    return r[0], r[1].strip(), r[2].strip()


def bash():
    # shell=True would mean cmd.exe on Windows, which fails on bash syntax; System32's bash.exe is WSL, another machine.
    found = shutil.which("bash")
    if os.name != "nt":
        return found or shutil.which("sh")
    git = [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
           os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "bin", "bash.exe")]
    return next((p for p in [found, *git] if p and os.path.isfile(p) and "system32" not in p.lower()), None)


def run_check(check, cwd, timeout=300):
    sh = bash()
    if not sh:
        return 127, "bash not found; on Windows it comes with Git for Windows"
    r = run_tree([sh, "-c", check], timeout, cwd=cwd)
    if r is None:
        return 124, f"check timed out after {timeout // 60} minutes"
    return r[0], (r[1] + r[2]).strip()


# The command could not run on this machine (missing tool, no permission, timeout); the code was never tested.
ENV_FAIL = re.compile(r"command not found|is not recognized as an internal|permission denied while trying to connect to the docker"
                      r"|cannot connect to the docker daemon|docker daemon is not running|failed to connect to the docker api"
                      r"|error during connect", re.I)


def env_failure(code, out):
    return code in (124, 126, 127) or (code != 0 and bool(ENV_FAIL.search(out)))


def do_here(check, code, out):
    return (f"frugal: check `{check}` exited {code} and could not run here:\n{out[-800:]}\n"
            "Do it here: run the same command in your shell. If it fails the same way there, the tool is missing or not running "
            "on this machine (start or install it); the code was not tested.")


def run_brief(text, check, level, effort, cwd, job):
    # The check runs once, here; its exit code and output are the result. An agent only explains a failure, from that output.
    code, out = run_check(check, cwd, TIMEOUT.get(level, 600))  # a data build ran past the old fixed 5 minutes and came back to the chat
    if env_failure(code, out):
        return do_here(check, code, out)
    if code == 0:
        return f"frugal: check `{check}` exited 0, passed:\n{out[-300:]}"
    notes = brief(f"{text}\n\nThe command `{check}` already ran and exited {code}. The end of its output:\n{out[-8000:]}\n\n"
                  "Find the cause of each failure from this output and the code. Do not run the command again, and do not change any file.",
                  "search", None, level, effort, cwd, job=job)
    return f"frugal: check `{check}` exited {code}, failed:\n{out[-800:]}\n\nAgent notes, not a test result:\n{notes}"


def cli_reply(agent, out, err, job):
    """The agent's answer from its CLI output; the tokens and cost it reports add up in job across tries."""
    def add(tokens, cost=0):
        job["tokens"] = job.get("tokens", 0) + (tokens or 0)
        if cost or "cost" in job or agent == "claude":
            job["cost"] = job.get("cost", 0) + (cost or 0)
    try:
        if agent == "claude":  # --output-format json; cache reads count, as in grok's and opencode's totals
            j = json.loads(out)
            u = j.get("usage") or {}
            add(sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")), j.get("total_cost_usd"))
            return ("", err or j.get("result", "")) if j.get("is_error") else (j.get("result") or "", err)
        if agent == "grok":  # --output-format json
            j = json.loads(out)
            add((j.get("usage") or {}).get("total_tokens"), j.get("total_cost_usd"))
            return j.get("text") or "", err or ("" if j.get("text") else out)
        if agent == "opencode":  # --format json: one event per line
            events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
            if not events:
                return out, err
            parts = [e.get("part") or {} for e in events]
            add(sum((p.get("tokens") or {}).get("total") or 0 for p in parts if p.get("type") == "step-finish"),
                sum(p.get("cost") or 0 for p in parts if p.get("type") == "step-finish"))
            text = "\n".join(p["text"] for p in parts if p.get("type") == "text" and p.get("text")).strip()
            return text, err or ("" if text else out)
    except (ValueError, AttributeError, TypeError):
        return out, err
    if m := re.search(r"^tokens used\s*\n([\d,]+)", err, re.M):  # codex exec ends its stderr log with the count
        add(int(m.group(1).replace(",", "")))
    return out, err


BUSY = {}


def touched(cwd):
    """Modified and untracked files of cwd's git tree, with their mtimes; {} outside one."""
    try:
        out = subprocess.run(["git", "ls-files", "-m", "-o", "--exclude-standard", "-z"], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    files = {}
    for f in filter(None, out.decode("utf-8", "replace").split("\0")):
        try:
            files[f] = os.path.getmtime(os.path.join(cwd, f))
        except OSError:
            pass  # deleted: frugal: list deletions too with `git ls-files -d`
    return files


def name(i):
    """The name Frugal Deck shows for an account (renaming there sets it); the id where none is set."""
    try:
        label = json.loads((HOME / ".frugal-deck" / "settings.json").read_text(encoding="utf-8"))["labels"][i]
        return label.strip() if isinstance(label, str) and label.strip() else i
    except (OSError, ValueError, KeyError, TypeError):
        return i


def relogin(ids):
    return "".join(f"frugal: secondary account {name(i)} is logged out; tell the user to run `/frugal:accounts login {i}`.\n" for i in ids)


def failed_try(job, a, model, code, began, reason):
    # Every try's reason, for Frugal Deck's Failed tasks; the reply names only the last one.
    job.setdefault("failures", []).append({"account": a["id"], "model": model, "exit": code, "seconds": round(time.time() - began), "reason": reason[-300:]})


def brief(text, kind, check, level, effort, cwd, mcp=(), job=None):
    job = {} if job is None else job  # filled with the account, model and cost of the last try
    if check and kind == "run":
        return run_brief(text, check, level, effort, cwd, job)
    if (barred := [n for n in mcp if n in load().get("no_delegate_mcp", [])]):  # set per server in Frugal Deck's MCP servers
        return f"frugal: {', '.join(barred)} is off for delegates in Frugal Deck. Do it here."
    found = session_mcp(cwd) if mcp else {}
    names = {mcp_rule(k)[5:]: k for k in found}  # a brief may name "claude_ai_Pylon", as the tool names spell it
    mcp = [n if n in found else names.get(n, n) for n in mcp]
    if (missing := [n for n in mcp if n not in found]):
        return f"frugal: this session has no MCP server named {', '.join(missing)}. Do it here."
    servers, expired = with_auth({n: found[n] for n in mcp})
    if expired:
        return f"frugal: this session's login to {', '.join(expired)} expired; call one of its tools here once to refresh it, then delegate again."
    allow, deny, failed, ro = mcp_rules(servers)
    if failed:
        return f"frugal: could not list the tools of {', '.join(failed)} to keep the delegate read-only. Do it here."
    if level not in LEVELS and kind == "edit" and len(text) > 2500:
        level = "heavy"  # a long edit brief is multi-file work: on the standard level it ran past 10 minutes and started over
    raw = kind == "note"  # a heads-up check wants the bare answer, or "" when no account answered
    failure, tried, relog, failed_kinds, check_fails = "", [], [], set(), set()
    start = touched(cwd) if kind == "edit" else {}
    if check:
        text += f"\n\nDone when this bash command exits 0: {check}"
    for n in range(1, MAX_TRIES + 1):
        with LOCK:
            data = load()
            # A heads-up note carries the session transcript, which never goes to Grok or OpenCode.
            # A task needing a claude_only_mcp server never goes to Codex, Grok or OpenCode, nor one needing a server with an
            # mcp_kinds list to an account of another kind; a server without a list goes to any kind.
            only, kinds = data.get("claude_only_mcp", []), data.get("mcp_kinds", {})  # both set per server in Frugal Deck's MCP servers
            pool = [a for a in order(data["accounts"], BUSY, level) if a["id"] not in tried and not (raw and a["kind"] in ("grok", "opencode"))
                    and not any(a["kind"] != "claude" and n in only or n in kinds and a["kind"] not in kinds[n] for n in servers) and not blocked(a, data)]
            if not pool:
                break
            # After a failure another provider goes first: three free OpenCode models failed one after another on its server errors.
            pool.sort(key=lambda x: x["kind"] in failed_kinds)
            a = pool[0]
            BUSY[a["id"]] = BUSY.get(a["id"], 0) + 1
        tried.append(a["id"])
        model, eff = choose(a, kind, level, effort)
        job.update(account=a["id"], model=model, level=level if level in LEVELS else DEFAULT_LEVEL.get(kind, "light"))
        log_job({**job, "status": "running"})  # Frugal Deck shows who runs it, and who took over after a failed try
        began = time.time()
        prompt = (f"{text}\n\nA previous attempt failed: {failure}" if failure else text) + \
            ("" if raw else "\n\nReply in under 150 words: what you found or changed, with file paths.")
        if raw:
            pass  # the side agent gets only the brief
        elif a["kind"] == "claude":
            prompt = "/frugal:frugal " + prompt  # loads frugal's rules in the delegated session
        else:
            prompt = f"{CODEX_RULES}\n\n{prompt}"  # Codex cannot load the skill, so it gets the essentials
        env = child_env(a, {DELEGATED: "1"})
        env.pop("FRUGAL_DECK_MCP", None)  # the chat's server configs, which may carry keys; a delegate gets only its servers, in mcp_file
        if a["kind"] != "claude":
            # frugal: a token is found by the server's name in the variable (SERVICEMINDER_MCP_TOKEN); read bearer_token_env_var if one is named otherwise.
            env = {k: v for k, v in env.items() if not any(n.upper() in k.upper() for n in only)}
        if a["kind"] == "grok":
            env.update(GROK_CLAUDE_MCPS_ENABLED="false", GROK_CLAUDE_HOOKS_ENABLED="false")  # grok loads Claude's MCP servers and hooks by default
        off = [n for n in only if n in codex_mcp()] if a["kind"] == "codex" else []
        mcp_args, mcp_env = mcp_for(a["kind"], servers, ro) if servers and a["kind"] in ("codex", "opencode") else ([], {})
        env.update(mcp_env)
        mcp_file = None
        if servers and a["kind"] in ("claude", "grok"):  # a file, not an argument: server configs can carry tokens, and a command line is visible to every process
            fd, mcp_file = tempfile.mkstemp(suffix=".json" if a["kind"] == "claude" else ".md")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if a["kind"] == "claude":
                    json.dump({"mcpServers": servers}, f)
                else:  # a Grok agent file; its frontmatter takes ACP's server list, written as JSON, which YAML reads too
                    pairs = lambda d: [{"name": k, "value": v} for k, v in (d or {}).items()]
                    acp = [{"type": "http", "name": mcp_rule(n)[5:], "url": c["url"], "headers": pairs(c.get("headers"))} if c.get("url") else
                           {"name": mcp_rule(n)[5:], "command": c["command"], "args": c.get("args") or [], "env": pairs(c.get("env"))} for n, c in servers.items()]
                    f.write(f"---\nname: frugal-mcp\ndescription: frugal delegate with MCP servers\nmcpServers: {json.dumps(acp)}\n---\n")
        try:
            if servers and a.get("config_dir"):
                clear_needs_auth(a["config_dir"], [n for n, c in servers.items() if c.get("url")])
            before = touched(cwd) if kind == "edit" else {}
            code, out, err = run_cli(argv_for(a, kind, model, eff, off, mcp_file, allow, deny, mcp_args), prompt, cwd, env, TIMEOUT.get(level, 600))
            # A timed-out edit leaves its work in the tree: the next account would start over on top of it, so hand it back as is.
            if code == 124 and kind == "edit" and (left := sorted(f for f, t in touched(cwd).items() if before.get(f) != t)):
                return (f"{relogin(relog)}frugal: {a['id']} timed out after changing {', '.join(left[:30])}, untested. "
                        "Review that diff here, then finish it here or delegate the rest, saying what is already done.")
        finally:
            with LOCK:
                BUSY[a["id"]] -= 1
            if mcp_file:
                os.unlink(mcp_file)
        out, err = cli_reply(a["kind"], out, err, job)
        with LOCK, file_lock():
            data = load()
            for x in data["accounts"]:
                if x["id"] == a["id"]:
                    if code != 0 and AUTH.search(out + err):
                        x["needs_login"] = True
                        relog.append(a["id"])
                    elif code != 0 and LIMIT.search(out + err):
                        x["exhausted_until"] = time.time() + HOUR
                    else:
                        x["used"] = (x.get("used", 0) if x.get("day") == today() else 0) + 1
                        x["day"] = today()
            save(data)
        notice = relogin(relog)
        label = f"{name(a['id'])} ({', '.join(filter(None, [model, f'{eff} effort']))})"
        if code != 0 or not out:
            failure = (err or out or f"exit {code}")[-400:]
            failed_try(job, a, model, code, began, failure)
            failed_kinds.add(a["kind"])
            if code == 124 and level not in ("heavy", "max"):
                level = "heavy"  # bigger than its level: the next try gets a strong account and 20 minutes instead of another 10
            continue
        if check:
            c, tail = run_check(check, cwd)
            if env_failure(c, tail):
                return f"{notice}{do_here(check, c, tail)}\n\nChanges by {label}, untested:\n{out[:2000]}"
            if c != 0:
                failure = f"check `{check}` failed: {tail[-400:]}"
                failed_try(job, a, model, code, began, failure)
                failed_kinds.add(a["kind"])
                if failure in check_fails:
                    break  # the same check output after two accounts' work: the check itself fails, another try only redoes it
                check_fails.add(failure)
                continue
            return f"{notice}Done by {label}; check `{check}` exited 0, passed:\n{tail[-300:]}\n\nAgent notes:\n{out[:2000]}"
        if raw:
            return out
        return f"{notice}Done by {label}.\n{out[:2000]}"
    if raw:
        return ""
    # Work a failed try left in the tree: without the list, the chat redid files that already existed.
    left = sorted(f for f, t in touched(cwd).items() if start.get(f) != t) if kind == "edit" else []
    changed = f" Changed, untested: {', '.join(left[:30])}; review that diff here before redoing any of it." if left else ""
    return f"{relogin(relog)}frugal: no secondary account finished this{f' ({failure})' if failure else ''}.{changed} Do it here."


SPEC = {
    "name": "delegate",
    "description": "Runs bounded work on the user's secondary accounts so this account's limits last: searches, reading or summarizing files, "
                   "test or build runs, edits you can brief exactly. Independent pieces go in \"tasks\" and run in parallel. "
                   "Keep design and the final check here; \"do it here\" means do it yourself.",
    "inputSchema": {"type": "object", "required": ["kind"], "properties": {
        "task": {"type": "string", "description": "Goal, exact paths, done-when. With kind run it is never executed: it only guides the explanation of a failure."},
        "tasks": {"type": "array", "items": {"type": "string"}, "description": "Independent briefs, run in parallel."},
        "kind": {"type": "string", "enum": ["search", "edit", "run"], "description": "search: read-only. edit: changes files. run: runs commands and reports."},
        "check": {"type": "string", "description": "Bash command (Git Bash on Windows) that exits 0 when done right; only this brief's own files, never ones a parallel brief makes. With kind run: the command to run, once; put every command to run here, chained with &&. A pipe exits with its last stage: put `set -o pipefail;` first when piping a run into tail or head, or a failing run passes."},
        "cwd": {"type": "string", "description": "Directory the work happens in (sandbox root, check runs there). Default: the folder the check starts with `cd` into, else the session's project dir. Set it when the work is outside the project."},
        "level": {"type": "string", "enum": LEVELS, "description": "light: searches, reads. standard: edits, test runs. heavy: hard multi-file work. max: after heavy failed. With kind run: the command's time limit, 10 minutes, heavy 20, max 30."},
        "effort": {"type": "string", "enum": EFFORTS[:-1], "description": "Overrides the effort of the level."},
        "mcp": {"type": "array", "items": {"type": "string"},
                "description": "MCP servers of this session the task needs (the <name> in mcp__<name>__ tools). The delegate gets those servers' read-only tools only."},
    }},
}


def log_job(entry):
    # frugal: the file only grows; trim it to the last few thousand lines once it gets large.
    with LOCK, file_lock(), open(JOBS, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def outcome(text, job):
    if re.match(r"frugal: check `.*` exited \d+, failed", text):
        return "failed"
    if re.search(r"^(Done by |frugal: check `.*` exited 0, passed)", text, re.M):
        return "done"
    return "failed" if job.get("account") else "not delegated"


def run_job(b, args, cwd):
    kind = args.get("kind", "search")
    job = {"id": os.urandom(6).hex(), "session": os.environ.get("CLAUDE_CODE_SESSION_ID", ""), "cwd": cwd, "kind": kind,
           "brief": b.strip().split("\n")[0][:200], "start": time.time()}
    log_job({**job, "status": "running"})
    if (req := getattr(TL, "req", None)):
        req.jobs.append(job)
    text, TL.job = None, job
    try:
        text = brief(b, kind, args.get("check"), args.get("level"), args.get("effort"), cwd, args.get("mcp") or (), job)
        return text
    finally:
        TL.job = None
        status = "cancelled" if req and req.cancelled else outcome(text, job) if text else "failed"
        log_job({**job, "status": status, "end": time.time(), "result": (text or "")[:300]})


def work_dir(args):
    """The cwd given, else the folder a check starts with `cd <dir> &&`, else the session's project dir."""
    if args.get("cwd"):
        return args["cwd"]
    if m := re.match(r"""\s*cd\s+("[^"]+"|'[^']+'|[^\s;&|]+)\s*&&""", args.get("check") or ""):
        d = os.path.expanduser(m.group(1).strip("\"'"))
        if os.name == "nt":
            d = re.sub(r"^/([a-zA-Z])(/|$)", r"\1:/", d)  # Git Bash's /d/x is D:/x
        if os.path.isdir(d):
            return d
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def system_dir(cwd):
    # An elevated shell starts in C:\Windows\System32: an account working there can write neither there nor in the project.
    root = os.environ.get("SystemRoot")
    return bool(root) and Path(cwd).resolve().is_relative_to(Path(root).resolve())


def delegate(args):
    # A model reaches for "brief" or "prompt" first; each rejected call re-reads the whole context. A run needs only its check.
    one = args.get("task") or args.get("brief") or args.get("prompt")
    briefs = [*(args.get("tasks") or []), *([one] if one else [])]
    if not briefs and args.get("kind") == "run" and args.get("check"):
        briefs = ["Report each failure verbatim with its file:line."]
    if not briefs:
        return 'frugal: put the brief in "task", or independent briefs in "tasks".'
    cwd = work_dir(args)
    if system_dir(cwd):
        return f"frugal: this would run in {cwd}, where a secondary account cannot write. Delegate again with cwd set to the project folder."
    req = getattr(TL, "req", None)

    def work(b):
        TL.req = req  # pool threads start without it
        return run_job(b, args, cwd)

    with concurrent.futures.ThreadPoolExecutor(len(briefs)) as ex:
        out = list(ex.map(work, briefs))
    return out[0] if len(out) == 1 else "\n\n".join(f"## Task {i}\n{r}" for i, r in enumerate(out, 1))


PROGRESS_EVERY = 15  # seconds between progress notifications; they also tell a client that resets its timeout on progress that the call is alive


def progress_message(req, start):
    names = ", ".join(dict.fromkeys(j.get("account") or "starting" for j in req.jobs)) or "starting"
    return f"{names}: {int(time.time() - start) // 60} min"


def serve(inp=None, outp=None):
    # Each tools/call runs in its own thread, so the loop keeps reading: pings, cancellations, and the end of the session.
    inp, outp, out_lock, live, workers = inp or sys.stdin, outp or sys.stdout, threading.Lock(), {}, []

    def send(msg):
        with out_lock:
            outp.write(json.dumps(msg) + "\n")
            outp.flush()

    def beat(req, token, done):
        start, n = time.time(), 0
        while not done.wait(PROGRESS_EVERY):
            n += 1
            send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progressToken": token, "progress": n, "message": progress_message(req, start)}})

    def call(rid, p, req):
        TL.req, done = req, threading.Event()
        token = (p.get("_meta") or {}).get("progressToken")
        beater = threading.Thread(target=beat, args=(req, token, done), daemon=True) if token is not None else None
        if beater:
            beater.start()
        try:
            text = delegate(p.get("arguments") or {}) if p.get("name") == "delegate" and active() else "frugal: no secondary account is set up."
        except Cancelled:
            text = None
        except Exception as e:  # one bad call must not close the server for the rest of the session
            with open(DIR / "errors.log", "a", encoding="utf-8") as f:
                traceback.print_exc(file=f)
            text = f"frugal: delegate failed ({type(e).__name__}: {e}). Do it here."
        finally:
            done.set()
            if beater:
                beater.join()
            live.pop(rid, None)
        if text is not None and not req.cancelled:  # a cancelled request gets no reply
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}})

    for line in inp:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        rid, method = msg.get("id"), msg.get("method")
        if rid is None:
            if method == "notifications/cancelled" and (req := live.get((msg.get("params") or {}).get("requestId"))):
                req.cancel()
            continue
        if method == "tools/call":
            live[rid] = Request()
            workers = [t for t in workers if t.is_alive()] + [threading.Thread(target=call, args=(rid, msg.get("params") or {}, live[rid]), daemon=True)]
            workers[-1].start()
            continue
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "frugal-accounts", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [SPEC] if active() else []}
        elif method == "ping":
            result = {}
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})
            continue
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    for req in list(live.values()):  # the session ended: no agent keeps working for nobody
        req.cancel()
    end = time.time() + 10
    for t in workers:  # their jobs log as cancelled before the process exits; else they stayed "running" forever
        t.join(max(0, end - time.time()))


# Hooks keep per-session state (rule shown, reads this prompt, waived denials) next to the accounts file.
def state(sid):
    return DIR / f"session-{re.sub(r'[^A-Za-z0-9_-]', '', sid)[:64]}.json"


def read_state(sid):
    try:
        return json.loads(state(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def hook_prompt(event):
    if os.environ.get(DELEGATED):
        return ""
    if (m := re.match(r"\s*/frugal:heads-up\b(.*)", event.get("prompt", ""), re.S)):
        return heads_up_set(event.get("session_id", ""), m.group(1))
    warn = relogin(a["id"] for a in load()["accounts"] if a.get("needs_login")).strip()
    if not active():
        return warn
    return f"{rule(event)} {warn}".strip()


def prune():
    cutoff = time.time() - 7 * 86400
    for f in DIR.glob("session-*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def rule(event):
    sid = event.get("session_id", "")
    s = read_state(sid)
    if not s:
        prune()  # once per new session: state files of week-old sessions
    ids = ", ".join(a["id"] for a in ready(load()["accounts"]))
    first = s.get("announced") != ids
    DIR.mkdir(exist_ok=True)
    state(sid).write_text(json.dumps({**s, "announced": ids, "reads": 0, "edits": 0, "delegated": 0, "agent": False}), encoding="utf-8")
    if first:
        return (f"frugal: secondary accounts are set up. Delegate every bounded task with {TOOL}, level per task; "
                "keep here only design, judgment calls and the final check.")
    return nudge(s.get("reads", 0), s.get("delegated", 0))


def nudge(reads, delegated):
    # The same line on every prompt went unread (one session got it 120 times and delegated 14 times in 34 prompts);
    # this one shows only after a prompt that explored here, and says how much.
    if reads > EXPLORE_BUDGET and not delegated:
        return f"frugal: the last prompt ran {reads} reads and searches here and delegated none. Send bounded pieces to {TOOL}."
    return ""


QUOTED = re.compile(r"\"(?:\\.|[^\"\\])*\"|'[^']*'")
SHELL_C = re.compile(r"\b(bash|sh|zsh|pwsh|powershell|cmd)(\.exe)?\s+([-/]\w+\s+)*?([-/]c|-command)\s*$", re.I)


def unquoted(cmd):
    """The command with quoted arguments blanked: a commit message or an inline script's text is data. A quoted program, or the code
    after bash -c, cmd /c or -Command, still runs."""
    def keep(m):
        before = cmd[:m.start()]
        return m.group(0) if not before.strip() or re.search(r"(&&|\|\||[;|&(\n])[ \t]*$", before) or SHELL_C.search(before) else '""'
    # A lookup (which tsc, Get-Command cargo) names a tool without running it.
    return re.sub(r"\b(which|where\.exe|command\s+-v|Get-Command|gcm)\s+[^;&|\n]*", "", QUOTED.sub(keep, cmd), flags=re.I)


def heavy(cmd):
    head = unquoted(re.split(r"<<-?\s*['\"]?\w+", cmd)[0])  # a heredoc body is data, not a command run here
    # ssh runs on another machine and may change it there or prompt for a key; CLAUDE_CONFIG_DIR= already uses another account
    return bool(HEAVY_RUN.search(head)) and not re.search(r"\bssh\s", head) and "CLAUDE_CONFIG_DIR=" not in head


def reads_only(cmd):
    """True when each command of a chain or pipeline only reads (cat, grep, ls, git log), with no redirect into a file."""
    if "<<" in cmd or "`" in cmd or "$(" in cmd:
        return False
    s = re.sub(r"\d?>\s*(/dev/null|&\d|\$null)", "", QUOTED.sub('""', cmd))  # a quoted | or > is a pattern, not a pipe
    if ">" in s or re.search(r"\bsed\b[^|;&]*\s(-i|--in-place)", s) or re.search(r"\bfind\b[^|;&]*\s-(delete|exec|ok)", s):
        return False
    parts = [re.sub(r"^([A-Za-z_]\w*=\S*\s+)+", "", p.strip()) for p in re.split(r"&&|\|\||;|\|", s)]
    parts = [p for p in parts if p and not NEUTRAL.match(p)]
    return bool(parts) and all(READ_ONLY.match(p) for p in parts)


def gate(event):
    """(reason to deny this tool call and send it to delegate, note added to context while it runs); None where nothing applies."""
    if not active() or event.get("agent_id"):
        return None, None
    sid, tool, inp = event.get("session_id", ""), event.get("tool_name", ""), event.get("tool_input") or {}
    s = read_state(sid)
    reason = note = None
    # A denied read is repeated with the whole context re-read, so past the budget a read still runs and only gets a note.
    def count_read():
        nonlocal note
        s["reads"] = s.get("reads", 0) + 1
        if s["reads"] == EXPLORE_BUDGET + 1:
            note = (f"frugal: {EXPLORE_BUDGET} reads and searches this prompt already. Hand the rest of this exploration to {TOOL} "
                    "(kind \"search\", pieces in \"tasks\") and work from its summary.")
    if tool in ("Bash", "PowerShell"):
        cmd = inp.get("command", "")
        # A background launch costs nothing while it runs; delegate would block on it and end it at its timeout.
        if heavy(cmd) and not inp.get("run_in_background") and cmd not in s.get("waived", []):
            s["waived"] = [*s.get("waived", []), cmd][-20:]
            reason = (f"frugal: send this to {TOOL} with kind \"run\" and the exact command as \"check\" (with `set -o pipefail;` first if it pipes the run into tail or head); no \"task\" needed; "
                      "it runs the command once in bash and returns its real exit code and output. If it says \"Do it here\", "
                      "run the same command again and it goes through.")
        elif reads_only(cmd):
            count_read()
    elif tool in ("Read", "Grep", "Glob"):
        count_read()
    elif tool in EDITS and not OWN_FILE.search(inp.get("file_path") or inp.get("notebook_path") or ""):
        # Once per prompt: the retry counts again and goes through, and a prompt that already delegated is left alone.
        s["edits"] = s.get("edits", 0) + 1
        if s["edits"] == EDIT_BUDGET + 1 and not s.get("delegated"):
            reason = (f"frugal: {EDIT_BUDGET} edits here this prompt already. Hand the remaining edits to {TOOL} with kind \"edit\" "
                      "(level \"heavy\" for multi-file work): in \"task\" name the files, the exact change and what is already done; in \"check\" "
                      "a command that proves it (build, test). Then review its diff here. If the rest depends on judgment only this "
                      "conversation has, call again and it goes through.")
    elif tool == TOOL:
        s["delegated"] = s.get("delegated", 0) + 1
    elif tool == "Agent" and (tries := s.get("agents", []).count(key := hashlib.sha1(inp.get("prompt", "").encode()).hexdigest()[:12])) < 2:
        # Per brief, like "waived": a session-wide flag let the rest of a parallel batch through after the first was sent back.
        # Sent back twice: one retry used to pass, and nearly every retry was work a delegate could do.
        s["agents"] = [*s.get("agents", []), key][-40:]
        reason = (f"frugal: a subagent here runs on the main account. Give this to {TOOL} instead (one brief per piece in \"tasks\"); "
                  "delegates read files, run bash and fetch the web, and take this session's MCP servers named in \"mcp\" (read-only tools).") if not tries else (
                  f"frugal: still send it to {TOOL}: file reads, bash, web and this session's read-only MCP tools all work there. "
                  "Call Agent a third time only if it needs what a delegate lacks (an MCP tool that writes, or this conversation's context); then it goes through.")
    DIR.mkdir(exist_ok=True)
    state(sid).write_text(json.dumps(s), encoding="utf-8")
    return reason, note


def heads_up():
    try:
        return json.loads(HEADS_UP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_heads_up(g):
    DIR.mkdir(exist_ok=True)
    HEADS_UP.write_text(json.dumps(g, indent=2), encoding="utf-8")


def heads_up_set(sid, arg):
    words = arg.lower().split()
    if words not in (["on"], ["off"], ["on", "all"], ["off", "all"]):
        return "frugal: /frugal:heads-up takes on, off, on all or off all. Tell the user that in one line."
    off = words[0] == "off"
    if words[1:]:
        with file_lock():
            save_heads_up({**heads_up(), "off": off})
    s = read_state(sid)
    s["heads_up_off"] = off
    DIR.mkdir(exist_ok=True)
    state(sid).write_text(json.dumps(s), encoding="utf-8")
    return (f"frugal: heads-up notes are now {'off' if off else 'on'} {'in every session' if words[1:] else 'for this session'}. "
            "Confirm that to the user in one line.")


def transcript(path):
    """The session as plain lines, and the tool calls since the user's last prompt."""
    lines, calls = [], 0
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return "", 0
    with f:
        for raw in f:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if e.get("isMeta") or e.get("type") not in ("user", "assistant") or not isinstance(e.get("message"), dict):
                continue
            content = e["message"].get("content")
            for b in [{"type": "text", "text": content}] if isinstance(content, str) else content or []:
                t = b.get("type") if isinstance(b, dict) else None
                if t == "text":
                    if e["type"] == "user":
                        calls = 0
                    lines.append(f"{'User' if e['type'] == 'user' else 'Claude'}: {b.get('text', '')[:2000]}")
                elif t == "tool_use":
                    calls += 1
                    lines.append(f"Tool {b.get('name')}: {json.dumps(b.get('input'), ensure_ascii=False)[:300]}")
                elif t == "tool_result":
                    c = b.get("content")
                    text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                    lines.append(f"Result: {text[:300]}")
    return "\n".join(lines)[-24000:], calls


def hook_stop(event):
    """The note to wake Claude with, or None. Claude Code's own "You should know" never runs in Claude Desktop, so only there."""
    if os.environ.get(DELEGATED) or os.environ.get("CLAUDE_CODE_ENTRYPOINT") != "claude-desktop" or not active():
        return None
    sid, g = event.get("session_id", ""), heads_up()
    s = read_state(sid)
    if s.get("heads_up_off", g.get("off", False)) or s.get("heads_up_shown", 0) >= HEADS_UP_MAX or s.get("heads_up_checks", 0) >= HEADS_UP_CHECKS:
        return None
    text, calls = transcript(event.get("transcript_path"))
    if calls < HEADS_UP_CALLS:
        return None
    s["heads_up_checks"] = s.get("heads_up_checks", 0) + 1
    state(sid).write_text(json.dumps(s), encoding="utf-8")
    seen = g.get("seen", [])
    ask = HEADS_UP_ASK + ("\n\nAlready shown, do not repeat:" + "".join(f"\n- {n}" for n in seen[-10:]) if seen else "")
    note = brief(f"{ask}\n\nTranscript:\n{text}", "note", None, None, None, event.get("cwd") or os.getcwd()).strip()
    if not note or re.match(r"none\b", note, re.I) or note in seen:
        return None
    s = read_state(sid)  # the check took a while; keep what other hooks wrote meanwhile
    s["heads_up_shown"] = s.get("heads_up_shown", 0) + 1
    state(sid).write_text(json.dumps(s), encoding="utf-8")
    with file_lock():
        save_heads_up({**heads_up(), "seen": [*heads_up().get("seen", []), note][-30:]})
    return note


def account_env(a):
    # OpenCode keeps its login and sessions in its own data folder per account
    return {"CLAUDE_CONFIG_DIR": a["config_dir"]} if a.get("config_dir") else {"XDG_DATA_HOME": a["data_dir"]} if a.get("data_dir") else {}


def child_env(a, extra=None):
    # The main account uses the default folder, so its delegate drops the CLAUDE_CONFIG_DIR this session may run with.
    env = {**os.environ, **(extra or {}), **account_env(a)}
    if env.get("CLAUDE_CONFIG_DIR") and same(env["CLAUDE_CONFIG_DIR"]) == same(HOME / ".claude"):
        del env["CLAUDE_CONFIG_DIR"]
    return env


def find_email(x):
    if isinstance(x, dict):
        return next((v for k, v in x.items() if "email" in k.lower() and isinstance(v, str)), None) or next(filter(None, map(find_email, x.values())), None)
    return next(filter(None, map(find_email, x)), None) if isinstance(x, list) else None


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def logged_in(a):
    """(signed in, email or None). Grok and OpenCode keep their login in auth.json, which grok logout leaves as {}."""
    if a["kind"] in ("grok", "opencode"):
        auth = read_json(Path(a["data_dir"]) / "opencode" / "auth.json" if a["kind"] == "opencode"
                         else Path(os.environ.get("GROK_HOME") or HOME / ".grok") / "auth.json")
        return bool(auth), find_email(auth)
    cmd = ["codex", "login", "status"] if a["kind"] == "codex" else ["claude", "auth", "status"]
    exe = shutil.which(cmd[0])
    r = run_tree([exe, *cmd[1:]], 60, env=child_env(a)) if exe else None
    if not r:
        return False, None
    try:
        email = find_email(json.loads(r[1]))
    except ValueError:
        email = None
    return r[0] == 0, email


def login(a):
    """Opens a terminal that closes when the login ends, then checks it. Blocks until then, so run it in the background."""
    env = account_env(a)
    cmd = {"codex": "codex login", "grok": "grok login", "opencode": "opencode auth login"}.get(a["kind"], "claude auth login")
    if os.name != "nt":
        # frugal: no terminal is opened outside Windows; run login in-process with the user's tty when one is asked for.
        return f"Run in a terminal: {' '.join(f'{k}={v}' for k, v in env.items())} {cmd}, then: login {a['id']}".strip()
    sets = "".join(f'set "{k}={v}" && ' for k, v in env.items())
    subprocess.run(f'start "frugal login {a["id"]}" /wait cmd /c "{sets}{cmd}"', shell=True)
    ok, email = logged_in(a)
    with file_lock():
        data = load()
        for x in data["accounts"]:
            if x["id"] == a["id"] and a["kind"] != "opencode":  # OpenCode's free models need no login
                if ok:
                    x.pop("needs_login", None)
                else:
                    x["needs_login"] = True
        save(data)
    who = f" as {email}" if email else ""
    if ok:
        return f"{a['id']} is signed in{who}; frugal can use it (run /mcp if it is the first account)."
    if a["kind"] == "opencode":
        return f"{a['id']} has no OpenCode login; its free models still work."
    return f"{a['id']} is not signed in; it stays out of the pool. Try again with: login {a['id']}"


PENDING = []  # accounts to sign in once cli() is done: login() waits for the user, so it runs outside the file lock


def cli(args):
    data = load()
    if args[:1] == ["list"] or not args:
        rows = [f"{a['id']}: {a['kind']}{f" {a['model']}" if a.get('model') else ''}{' (off)' if not a.get('enabled', True) else ''}, used today {a.get('used', 0) if a.get('day') == today() else 0}"
                + (", at its limit" if a.get("exhausted_until", 0) > time.time() else "")
                + (", blocked: its model may train on your data (opencode-training on)" if blocked(a, data) else "")
                + (f", logged out (run: login {a['id']})" if a.get("needs_login") else "") for a in data["accounts"]]
        return "\n".join(rows) or "No secondary accounts. Add one with: add claude <id> | add codex <id> | add grok <id> | add opencode <id> <provider/model>"
    if args[0] == "add" and len(args) == (4 if args[1:2] == ["opencode"] else 3) and args[1] in RULES:
        kind, aid = args[1], args[2]
        if any(a["id"] == aid for a in data["accounts"]):
            return f"{aid} is already set up."
        a = {"id": aid, "kind": kind, "enabled": True, "used": 0, "day": ""}
        if kind == "claude":
            a["config_dir"] = str(HOME / f".claude-{aid}")
        elif kind == "codex":
            a["models"] = codex_models()
        elif kind == "opencode":
            a["model"], a["data_dir"] = args[3], str(DIR / f"opencode-{aid}")
            if blocked(a, data):
                return (f"{args[3]} may train on what it receives, so frugal blocks it. "
                        "To allow such models, run: opencode-training on")
        if kind != "opencode":
            a["needs_login"] = True  # out of the pool until login() confirms it
        data["accounts"].append(a)
        save(data)
        PENDING.append(a)
        return f"Added {aid}."
    if args[0] == "opencode-training" and args[1:] in (["on"], ["off"]):
        data["opencode_allow_training"] = args[1] == "on"
        save(data)
        return ("OpenCode models that may train on your data are now allowed." if args[1] == "on"
                else "OpenCode models that may train on your data are blocked.")
    if args[0] == "remove" and len(args) == 2:
        data["accounts"] = [a for a in data["accounts"] if a["id"] != args[1]]
        save(data)
        return f"Removed {args[1]}."
    if args[0] == "login" and len(args) == 2:
        a = next((a for a in data["accounts"] if a["id"] == args[1]), None)
        if not a:
            return f"No account {args[1]}."
        PENDING.append(a)
        return f"Signing in {a['id']}."
    return __doc__


def selftest():
    claude = {"id": "c", "kind": "claude"}
    codex = {"id": "x", "kind": "codex", "models": ["gpt-6-luna", "gpt-6.1-sol"]}
    assert choose(claude, "search") == ("sonnet", "medium")
    assert choose(claude, "edit", "heavy") == ("opus", "high")
    assert choose(claude, "search", effort="minimal") == ("sonnet", "low")
    assert choose(codex, "search", "light") == ("gpt-6-luna", "low")
    assert choose(codex, "edit", "max") == (None, "xhigh")
    now = time.time()
    pool = [{"id": "a", "used": 5, "day": today()}, {"id": "b", "used": 1, "day": today()}, {"id": "off", "enabled": False},
            {"id": "lim", "exhausted_until": now + 60}, {"id": "old", "used": 9, "day": "2000-01-01"}]
    assert [a["id"] for a in order(pool, {})] == ["old", "b", "a"]
    assert [a["id"] for a in ready([{"id": "out", "needs_login": True}, {"id": "in"}])] == ["in"]
    saved = {k: os.environ.pop(k, None) for k in ("CLAUDE_CONFIG_DIR", ROLES)}
    mine = [{"id": "me", "config_dir": str(HOME / ".claude-me") + os.sep}, {"id": "x", "config_dir": str(HOME / ".claude-x")}, {"id": "cx"}]
    os.environ["CLAUDE_CONFIG_DIR"] = str(HOME / ".claude-me")
    assert "CLAUDE_CONFIG_DIR" not in child_env({"config_dir": str(HOME / ".claude")}) and child_env({"config_dir": str(HOME / ".claude-x")})["CLAUDE_CONFIG_DIR"] == str(HOME / ".claude-x")
    assert [a["id"] for a in ready(mine)] == ["x", "cx"]
    os.environ[ROLES] = "off"
    assert len(ready(mine)) == 3
    del os.environ["CLAUDE_CONFIG_DIR"], os.environ[ROLES]
    assert [a["id"] for a in ready([{"id": "main", "config_dir": str(HOME / ".claude")}, {"id": "cx"}])] == ["cx"]
    os.environ.update({k: v for k, v in saved.items() if v is not None})
    assert AUTH.search("API Error: 401 OAuth token has expired") and AUTH.search("Please run /login") and not AUTH.search("ok")
    assert [a["id"] for a in order(pool, {"old": 1})] == ["b", "a", "old"]
    mixed = [{"id": "o", "kind": "opencode"}, {"id": "g", "kind": "grok"}, {"id": "x", "kind": "codex"}, {"id": "c", "kind": "claude", "used": 9, "day": today()}]
    assert [a["id"] for a in order(mixed, {"c": 1}, "heavy")] == ["c", "x", "g"] and order(mixed, {}, "standard")[0]["id"] == "o"
    assert "--effort" in argv_for(claude, "search", "haiku", "low") and "Bash Read Grep Glob" in argv_for(claude, "search", "haiku", "low")
    assert argv_for(codex, "run", None, "low")[-3:] == ["-s", "workspace-write", "-"]
    assert argv_for(claude, "note", "sonnet", "medium")[-3:] == ["--tools", "", "--strict-mcp-config"]
    grok = {"id": "g", "kind": "grok"}
    assert choose(grok, "edit", "heavy") == ("grok-4.6", "high")
    assert "--always-approve" in argv_for(grok, "edit", "grok-4.6", "high") and "--tools" not in argv_for(grok, "edit", "grok-4.6", "high")
    assert argv_for(grok, "search", "grok-4.6", "low")[-2:] == ["--tools", "read_file,grep,list_dir,run_terminal_cmd"]
    assert argv_for(grok, "search", "grok-4.6", "low", (), "a.md", (), ["mcp__py__send_x"])[-4:] == ["--agent", "a.md", "--deny", "MCPTool(py__send_x)"]
    assert AUTH.search("Error: Not signed in. To authenticate")
    oc = {"id": "o", "kind": "opencode", "model": "opencode/glm-5.3-flash-free", "data_dir": "/d"}
    assert choose(oc, "search", "max") == ("opencode/glm-5.3-flash-free", "high") and account_env(oc) == {"XDG_DATA_HOME": "/d"}
    assert argv_for(oc, "search", *choose(oc, "search"))[-6:] == ["medium", "--agent", "plan", "--auto", "--format", "json"] and "build" in argv_for(oc, "edit", "m", "low")
    pickle = {"kind": "opencode", "model": "opencode/big-pickle"}
    assert blocked(pickle, {}) and not blocked(pickle, {"opencode_allow_training": True}) and not blocked(oc, {})
    assert not blocked({"kind": "opencode", "model": "openrouter/nvidia/nemotron-x"}, {})
    assert argv_for(codex, "edit", None, "low", ["serviceminder"])[-5:-3] == ["-c", "mcp_servers.serviceminder.enabled=false"]
    tmp = DIR / "selftest-transcript.jsonl"
    DIR.mkdir(exist_ok=True)
    tmp.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "message": {"content": "old prompt"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"content": "fix it"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "name": "Read", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": "file body"}]}]}},
        {"type": "user", "isMeta": True, "message": {"content": "hook text"}}]) + "\nnot json", encoding="utf-8")
    text, calls = transcript(tmp)
    tmp.unlink()
    assert calls == 1 and "User: fix it" in text and "Result: file body" in text and "hook text" not in text
    assert transcript(None) == ("", 0)
    assert run_tree([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], 30)[1].strip() == "''"
    with file_lock():
        assert (DIR / "files.lock").exists()
    assert not (DIR / "files.lock").exists()
    old = DIR / "session-selftest-old.json"
    old.write_text("{}", encoding="utf-8")
    os.utime(old, (0, 0))
    prune()
    assert not old.exists()
    assert reads_only("cat a.py | grep -n x") and reads_only("cd src && git log --oneline -3 2>/dev/null") and reads_only("Get-Content f; ls")
    assert reads_only('grep -n "a\\|b" f | head') and not reads_only("grep x f > 'o.txt'")
    a, e = mcp_for("codex", {"p y": {"url": "https://p/", "headers": {"Authorization": "Bearer t"}}, "sm": {"command": "a", "env": {"K": "v"}}}, {"p y": ["get"], "sm": ["q"]})
    assert a == ["-c", 'mcp_servers.p_y.url="https://p/"', "-c", 'mcp_servers.p_y.env_http_headers={"Authorization" = "FRUGAL_MCP_0_0"}', "-c", 'mcp_servers.p_y.enabled_tools=["get"]',
                 "-c", 'mcp_servers.sm.command="a"', "-c", "mcp_servers.sm.args=[]", "-c", 'mcp_servers.sm.env_vars=["K"]', "-c", 'mcp_servers.sm.enabled_tools=["q"]'] and e == {"FRUGAL_MCP_0_0": "Bearer t", "K": "v"}
    assert json.loads(mcp_for("opencode", {"py": {"url": "https://p/"}}, {"py": ["get x"]})[1]["OPENCODE_CONFIG_CONTENT"])["permission"] == {"py_*": "deny", "py_get_x": "allow"}
    assert reads_only("A=1 sed -n 1,5p f && echo --- && head f") and reads_only("find . -name '*.py' | wc -l")
    assert not any(reads_only(c) for c in ("sed -i s/a/b/ f", "cat a > b", "npm test", "find . -delete", "echo hi", "python - <<EOF", "cat $(ls)", "git commit -m x"))
    assert nudge(EXPLORE_BUDGET + 1, 0).startswith(f"frugal: the last prompt ran {EXPLORE_BUDGET + 1}") and nudge(9, 1) == "" and nudge(EXPLORE_BUDGET, 0) == ""
    cfg = Path(tempfile.mkdtemp())
    (cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"sm": {"command": "a"}}, "projects": {
        str(cfg).replace("\\", "/"): {"mcpServers": {"p": {"command": "b"}}}, "elsewhere": {"mcpServers": {"q": {"command": "c"}}}}}), encoding="utf-8")
    saved_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    saved_deck = os.environ.pop("FRUGAL_DECK_MCP", None)
    os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
    a = argv_for(claude, "search", "sonnet", "low", (), "m.json", [mcp_rule("sm"), mcp_rule("my server")], [mcp_rule("py", "send reply")])
    assert a[a.index("--disallowedTools") + 1] == "mcp__py__send_reply" and "--disallowedTools" not in argv_for(claude, "search", "sonnet", "low")
    assert a[-5:-2] == ["--strict-mcp-config", "--mcp-config", "m.json"] and a[-2] == "--plugin-dir" and Path(a[-1], ".claude-plugin").is_dir() and "--strict-mcp-config" in argv_for(claude, "edit", "sonnet", "low") and a[a.index("--allowedTools") + 1] == "Bash Read Grep Glob mcp__sm mcp__my_server"
    d = tempfile.mkdtemp()
    assert work_dir({"cwd": "x", "check": f"cd {d} && t"}) == "x" and work_dir({"check": f'cd "{d}" && t'}) == d and work_dir({"check": f"cd {d}; t"}) != d
    assert work_dir({"check": "cd /nonexistent-dir-xyz && t"}) == (os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if os.name == "nt":
        assert Path(work_dir({"check": "cd /" + d[0].lower() + d[2:].replace("\\", "/") + " && t"})) == Path(d)
        assert system_dir(os.path.join(os.environ["SystemRoot"], "System32")) and not system_dir(d)
    assert heavy("cd x && npm test") and not heavy("python - <<'EOF'\nnpm test\nEOF")
    assert heavy("python bench.py run") and not heavy("cat core.md && grep -n t8 bench.py") and not heavy("ssh vps 'cd app && go test ./...'")
    assert not heavy("git commit -m \"fix: npm test flake\"") and not heavy("node -e \"s = s.replace('cargo test', x)\" && git diff --stat")
    assert heavy("bash -c 'npm test'") and heavy("cmd /c \"cargo build\"") and heavy("cd \"C:\\My Dir\" && cargo test") and heavy("echo 'x' ; npm test")
    assert not heavy("cat build.gradle.kts gradle.properties jest.config.js; ls ~/.gradle/caches") and not heavy("which tsc npx 2>&1 | head -2")
    assert not heavy("cat gradle.properties | head; find . -path ./gradle -prune -o -name \"*.yml\" -print") and heavy("gradle build")
    assert heavy("./gradlew.bat -q compileJava") and heavy("S=\"x\"\nnpx -y -p typescript tsc -p \"$S\"") and heavy("node_modules/.bin/jest")
    # edits: the one past the budget goes to delegate once per prompt, and never after this prompt delegated
    was_active, sid = globals()["active"], "selftest-edits"
    globals()["active"] = lambda: True
    try:
        state(sid).write_text(json.dumps({"edits": 0}), encoding="utf-8")
        ev = {"session_id": sid, "tool_name": "Edit", "tool_input": {"file_path": "a"}}
        assert all(gate(ev)[0] is None for _ in range(EDIT_BUDGET)) and 'kind "edit"' in gate(ev)[0] and gate(ev) == (None, None)
        assert gate({**ev, "tool_name": "Bash", "tool_input": {"command": "npm test", "run_in_background": True}}) == (None, None)
        assert outcome("frugal: check `x` exited 1, failed:\nboom\n\nAgent notes, not a test result:\nDone by g", {"account": "g"}) == "failed"
        state(sid).write_text(json.dumps({"edits": EDIT_BUDGET, "delegated": 1}), encoding="utf-8")
        assert gate({**ev, "tool_name": "Write"}) == (None, None) and gate({**ev, "agent_id": "x"}) == (None, None)
        state(sid).write_text(json.dumps({"edits": EDIT_BUDGET}), encoding="utf-8")
        assert gate({**ev, "tool_input": {"file_path": "C:\\Users\\u\\.claude-x\\projects\\p\\memory\\m.md"}}) == (None, None)
        assert gate({**ev, "tool_input": {"file_path": "C:\\Users\\u\\AppData\\Local\\Temp\\claude\\a\\b\\scratchpad\\t.js"}}) == (None, None)
        assert 'in "task"' in gate(ev)[0]
        # Agent: the same brief is sent back twice, the third goes through
        ag = {"session_id": sid, "tool_name": "Agent", "tool_input": {"prompt": "p"}}
        assert '"mcp"' in gate(ag)[0] and "third time" in gate(ag)[0] and gate(ag) == (None, None)
        assert delegate({"kind": "edit"}).startswith('frugal: put the brief in "task"')
    finally:
        globals()["active"] = was_active
        state(sid).unlink(missing_ok=True)
    # MCP listing against a local server: Cloudflare (error 1010) refuses urllib's default agent, so it must send its own.
    import http.server
    agents = []

    class Mcp(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            agents.append(self.headers.get("User-Agent"))
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": [{"name": "get_x"}]}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    mcp = http.server.HTTPServer(("127.0.0.1", 0), Mcp)
    threading.Thread(target=mcp.serve_forever, daemon=True).start()
    try:
        assert list_tools({"url": f"http://127.0.0.1:{mcp.server_port}/"}) == [{"name": "get_x"}]
        assert agents == ["frugal"] * 3
    finally:
        mcp.shutdown()
    # delegate's check, with a stub agent that always claims success
    prompts, g = [], globals()
    real = {k: g[k] for k in ("run_cli", "load", "save")}
    real_list_tools = list_tools
    assert list_tools({"command": sys.executable, "args": ["-c", 'import json, os, sys\nfor line in sys.stdin:\n    m = json.loads(line)\n    if m.get("method") == "tools/list":\n        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": [{"name": os.environ["T"]}]}}), flush=True)\n    elif "id" in m:\n        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {}}), flush=True)\n'], "env": {"T": "get_y"}}) == [{"name": "get_y"}]
    assert list_tools({"command": "frugal-no-such-command"}) is None
    g.update(run_cli=lambda argv, prompt, cwd, env, timeout=600: (prompts.append(prompt), (0, "PASS: all green", ""))[1],
             load=lambda: {"accounts": [{"id": "stub", "kind": "claude"}]}, save=lambda data: None)
    envs = []
    try:
        r = brief("run the tests", "run", "MSYS_NO_PATHCONV=1 echo ran-$((1+1))", None, None, ".")  # case 1: bash syntax cmd.exe rejects
        assert r.startswith("frugal: check") and "exited 0, passed" in r and "ran-2" in r and not prompts, r
        r = brief("fix x", "edit", "echo ok", None, None, ".")  # case 2: the agent gets the check
        assert "echo ok" in prompts[-1] and "exited 0, passed" in r, r
        r = brief("run the tests", "run", "echo boom; exit 1", None, None, ".")  # case 3: the agent's PASS never becomes the result
        head, _, notes = r.partition("Agent notes")
        assert "exited 1, failed" in head and "boom" in head and "PASS" not in head and "PASS" in notes, r
        assert "exit 1" in prompts[-1] and "boom" in prompts[-1]
        n = len(prompts)
        r = brief("run the tests", "run", "no-such-command-frugal", None, None, ".")  # missing tool: do it here, no agent
        assert "exited 127" in r and "Do it here" in r and len(prompts) == n, r
        r = brief("fix x", "edit", "echo nope; exit 1", None, None, ".")  # a failing check never reads as passed
        assert "passed" not in r and "Do it here" in r, r
        # a Claude account answers in JSON: the reply is its result and the job keeps the cost
        g["run_cli"] = lambda argv, prompt, cwd, env, timeout=600: (0, json.dumps({"result": "found it", "total_cost_usd": 0.25}), "")
        job = {}
        r = brief("look", "search", None, None, None, ".", job=job)
        assert r.endswith("found it") and job["cost"] == 0.25 and job["account"] == "stub" and outcome(r, job) == "done", (r, job)
        assert outcome("frugal: no secondary account finished this. Do it here.", {}) == "not delegated"
        assert job["level"] == "standard" and job["tokens"] == 0, job
        # a long edit brief runs on heavy; a timed-out edit returns the files it changed, not the ones already dirty
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)
            Path(repo, "old.txt").write_text("dirty", encoding="utf-8")
            g["run_cli"] = lambda argv, prompt, cwd, env, timeout=600: (Path(cwd, "new.txt").write_text("x", encoding="utf-8"), (124, "", "timed out"))[1]
            job = {}
            r = brief("x" * 3000, "edit", None, None, None, repo, job=job)
            assert "timed out after changing new.txt," in r and "old.txt" not in r and job["level"] == "heavy", (r, job)
        # each CLI's usage: tokens and cost add up across tries, the reply is the bare answer
        job = {}
        assert cli_reply("grok", json.dumps({"text": "ok", "usage": {"total_tokens": 100}, "total_cost_usd": 0.5}), "", job) == ("ok", "")
        oc = "\n".join(json.dumps(e) for e in [{"type": "text", "part": {"type": "text", "text": "ok"}},
                                               {"type": "step_finish", "part": {"type": "step-finish", "tokens": {"total": 20}, "cost": 0}}])
        log = "codex\nok\ntokens used\n11,590\nERROR x"
        assert cli_reply("opencode", oc, "", job) == ("ok", "") and cli_reply("codex", "ok", log, job) == ("ok", log)
        assert job == {"tokens": 11710, "cost": 0.5}, job
        assert cli_reply("opencode", "Error: boom", "", {}) == ("Error: boom", "") and cli_reply("grok", "{}", "", {}) == ("", "{}")
        # Claude-only MCP: other agents lose the server's token and grok its Claude MCP servers; a note never goes to grok
        os.environ["FRUGALTEST_SERVICEMINDER_TOKEN"] = "x"
        assert set(session_mcp(str(cfg))) == {"sm", "p"}
        # in a Deck chat: the account's servers its lists pass, plus the Deck's
        os.environ["FRUGAL_DECK_MCP"] = json.dumps({"mcpServers": {"d": {"type": "http", "url": "https://d/"}},
                                                    "allowed": [{"serverName": "sm"}, {"serverCommand": ["b"]}], "denied": [{"serverName": "p"}]})
        assert set(session_mcp(str(cfg))) == {"sm", "d"}
        del os.environ["FRUGAL_DECK_MCP"]
        (cfg / ".credentials.json").write_text(json.dumps({"mcpOAuth": {
            "py|1": {"serverName": "py", "serverUrl": "https://p/", "accessToken": "tok", "refreshToken": "REFRESH", "expiresAt": 2000_000},
            "old|1": {"serverName": "old", "serverUrl": "https://o/", "accessToken": "t2", "expiresAt": 1000_000}}}), encoding="utf-8")
        s, exp = with_auth({"py": {"type": "http", "url": "https://p/"}, "old": {"url": "https://o/"}, "sm": {"command": "a"},
                            "own": {"url": "https://p/", "headers": {"authorization": "x"}}}, now=1000)
        assert s["py"]["headers"] == {"Authorization": "Bearer tok"} and "REFRESH" not in json.dumps(s) and exp == ["old"]
        assert s["sm"] == {"command": "a"} and s["own"]["headers"] == {"authorization": "x"}
        g["list_tools"] = lambda conf: None if "bad" in conf.get("url", "") else [{"name": "get_x", "annotations": {"readOnlyHint": True}}, {"name": "send_x"}]
        assert mcp_rules({"py": {"url": "https://p/"}, "sm": {"command": "a"}, "b": {"url": "https://bad/"}}) == (["mcp__py__get_x", "mcp__sm__get_x"], ["mcp__py__send_x", "mcp__sm__send_x"], ["b"], {"py": ["get_x"], "sm": ["get_x"]})
        g["list_tools"] = real_list_tools
        (cfg / "mcp-needs-auth-cache.json").write_text(json.dumps({"py": {}, "x": {}}), encoding="utf-8")
        clear_needs_auth(cfg, ["py"])
        assert json.loads((cfg / "mcp-needs-auth-cache.json").read_text(encoding="utf-8")) == {"x": {}}
        os.environ["FRUGAL_DECK_MCP"] = "{}"
        g.update(run_cli=lambda argv, prompt, cwd, env, timeout=600: (envs.append((argv[0], env, argv)), (0, "ok", ""))[1],
                 load=lambda: {"claude_only_mcp": ["serviceminder"], "accounts": [{"id": "gk", "kind": "grok"}, {"id": "oc", "kind": "opencode", "model": "m"}, {"id": "cl", "kind": "claude"}]})
        assert brief("look", "search", None, None, None, ".").startswith("Done by") and brief("note", "note", None, None, None, ".") == "ok"
        (k1, e1, a1), (k2, e2, _) = envs[-2:]
        assert k1 in ("grok", "opencode") and "FRUGALTEST_SERVICEMINDER_TOKEN" not in e1 and e1.get("GROK_CLAUDE_MCPS_ENABLED", "false") == "false"
        assert k2 == "claude" and "FRUGALTEST_SERVICEMINDER_TOKEN" in e2 and "--mcp-config" not in a1
        assert "FRUGAL_DECK_MCP" not in e1 and "FRUGAL_DECK_MCP" not in e2
        del os.environ["FRUGAL_DECK_MCP"]
        # A task naming an MCP server reaches only the account kinds on its mcp_kinds list, with only that server; Claude alone when
        # the server is Claude-only; an unknown name runs nowhere.
        accts = [{"id": "gk", "kind": "grok"}, {"id": "oc", "kind": "opencode", "model": "m"}, {"id": "cl", "kind": "claude"}]
        g["load"] = lambda: {"mcp_kinds": {"sm": ["opencode"]}, "accounts": accts}
        assert brief("look up a customer", "search", None, None, None, str(cfg), ["sm"]).startswith("Done by") and envs[-1][0] == "opencode"
        assert list(json.loads(envs[-1][1]["OPENCODE_CONFIG_CONTENT"])["mcp"]) == ["sm"]
        g["load"] = lambda: {"mcp_kinds": {"sm": ["grok"]}, "accounts": accts}
        assert brief("look up a customer", "search", None, None, None, str(cfg), ["sm"]).startswith("Done by") and envs[-1][0] == "grok"
        argv = envs[-1][2]
        assert argv[argv.index("--agent") + 1].endswith(".md") and not os.path.exists(argv[argv.index("--agent") + 1])
        g["load"] = lambda: {"mcp_kinds": {"sm": []}, "accounts": accts}
        assert "no secondary account" in brief("look up a customer", "search", None, None, None, str(cfg), ["sm"])
        g["load"] = lambda: {"claude_only_mcp": ["sm"], "accounts": [{"id": "gk", "kind": "grok"}, {"id": "oc", "kind": "opencode", "model": "m"}, {"id": "cl", "kind": "claude"}]}
        assert brief("look up a customer", "search", None, None, None, str(cfg), ["sm"]).startswith("Done by") and envs[-1][0] == "claude"
        argv = envs[-1][2]
        assert "--mcp-config" in argv and "mcp__sm" in argv[argv.index("--allowedTools") + 1] and "mcp__p" not in argv[argv.index("--allowedTools") + 1]
        assert not os.path.exists(argv[argv.index("--mcp-config") + 1])
        n = len(envs)
        assert "no MCP server named nope" in brief("x", "search", None, None, None, str(cfg), ["nope"]) and len(envs) == n
    finally:
        os.environ.pop("FRUGALTEST_SERVICEMINDER_TOKEN", None)
        g.update(real)
        if saved_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = saved_cfg
        if saved_deck is None:
            os.environ.pop("FRUGAL_DECK_MCP", None)
        else:
            os.environ["FRUGAL_DECK_MCP"] = saved_deck
        shutil.rmtree(cfg)
    # serve: a delegate runs in its own thread with progress, a ping is answered meanwhile, and a cancel kills its agent with no reply
    sleeper, ended, logged = [sys.executable, "-c", "import time; time.sleep(30)"], [], []

    def stub_cli(argv, prompt, cwd, env, timeout=600):
        if "slow" in prompt:
            try:
                run_tree(sleeper, 60)
            finally:
                ended.append(time.time())
        return 0, "ok", ""

    class Out:
        def __init__(self):
            self.lines = []

        def write(self, s):
            self.lines += [json.loads(x) for x in s.splitlines() if x]

        def flush(self):
            pass

    def wait(cond, secs=10):
        end = time.time() + secs
        while not cond():
            assert time.time() < end, outp.lines
            time.sleep(0.05)

    real = {k: g[k] for k in ("run_cli", "load", "save", "active", "log_job", "PROGRESS_EVERY")}
    g.update(run_cli=stub_cli, load=lambda: {"accounts": [{"id": "stub", "kind": "claude"}]}, save=lambda data: None,
             active=lambda: True, log_job=logged.append, PROGRESS_EVERY=0.2)
    r, w = os.pipe()
    inp, wr, outp = os.fdopen(r, encoding="utf-8"), os.fdopen(w, "w", encoding="utf-8"), Out()
    try:
        req = TL.req = Request()
        threading.Timer(0.5, req.cancel).start()
        t = time.time()
        try:
            run_tree(sleeper, 60)
            raise AssertionError("run_tree outlived its cancel")
        except Cancelled:
            assert time.time() - t < 15 and not req.procs
        finally:
            TL.req = None
        server = threading.Thread(target=serve, args=(inp, outp), daemon=True)
        server.start()

        def put(msg):
            wr.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
            wr.flush()

        put({"id": 1, "method": "tools/call", "params": {"name": "delegate", "arguments": {"kind": "search", "task": "slow"}, "_meta": {"progressToken": "t1"}}})
        put({"id": 2, "method": "ping"})
        wait(lambda: any(m.get("id") == 2 for m in outp.lines))
        wait(lambda: sum(m.get("method") == "notifications/progress" for m in outp.lines) >= 2)
        put({"method": "notifications/cancelled", "params": {"requestId": 1}})
        wait(lambda: ended)
        put({"id": 3, "method": "tools/call", "params": {"name": "delegate", "arguments": {"kind": "search", "task": "quick"}}})
        wait(lambda: any(m.get("id") == 3 for m in outp.lines))
        progress = [m["params"] for m in outp.lines if m.get("method") == "notifications/progress"]
        assert [p["progress"] for p in progress] == list(range(1, len(progress) + 1)) and {p["progressToken"] for p in progress} == {"t1"}
        assert progress[-1]["message"].startswith("stub: ") and len(ended) == 1 and not any(m.get("id") == 1 for m in outp.lines)
        reply = next(m for m in outp.lines if m.get("id") == 3)["result"]["content"][0]["text"]
        assert reply.startswith("Done by stub") and sorted(e["status"] for e in logged if "end" in e) == ["cancelled", "done"], (reply, logged)
        wr.close()
        server.join(10)
        assert not server.is_alive()
    finally:
        if not wr.closed:
            wr.close()
        g.update(real)
    # A failed provider goes last; a timed-out try moves up to heavy: a strong account and 20 minutes.
    real = {k: g[k] for k in ("run_cli", "load", "save", "log_job", "run_check", "touched")}
    accounts = [{"id": "o1", "kind": "opencode", "model": "opencode/glm-5.3-flash-free"}, {"id": "o2", "kind": "opencode", "model": "opencode/glm-5.3-flash-free"},
                {"id": "x", "kind": "codex"}, {"id": "c", "kind": "claude"}]
    tries, replies = [], []
    g.update(run_cli=lambda argv, prompt, cwd, env, timeout=600: (tries.append((argv[0], timeout)), replies.pop(0))[1],
             load=lambda: {"accounts": [dict(a) for a in accounts]}, save=lambda data: None, log_job=lambda entry: None)
    try:
        replies[:] = [(1, "", "server error"), (0, "ok", "")]
        assert brief("look", "search", None, "standard", None, ".").startswith("Done by x") and [t[0] for t in tries] == ["opencode", "codex"], tries
        tries.clear()
        job, replies[:] = {}, [(124, "", "timed out"), (1, "", "boom"), (0, "ok", "")]
        assert brief("big", "search", None, "standard", None, ".", job=job).startswith("Done by x"), tries
        assert tries == [("opencode", 600), ("claude", 1200), ("codex", 1200)] and [f["exit"] for f in job["failures"]] == [124, 1], (tries, job)
        # A check that fails the same way after two accounts' work stops there and names what they left in the tree.
        tries.clear()
        replies[:] = [(0, "ok", "")] * 4
        seen = []
        g.update(run_check=lambda check, cwd, timeout=300: (1, "No such file: other.py"),
                 touched=lambda cwd: (seen.append(1), {} if len(seen) == 1 else {"new.py": 1})[1])
        r = brief("copy", "edit", "python -m py_compile new.py other.py", "standard", None, ".")
        assert len(tries) == 2 and "Changed, untested: new.py;" in r, (tries, r)
    finally:
        g.update(real)
    return "selftest ok"


if __name__ == "__main__":
    # Windows pipes default to the ANSI code page: a brief with ” or Á closed the server ("Connection closed") and garbled every other accent.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    if not (os.environ.get("LC_ALL") or os.environ.get("LANG")):
        os.environ["LANG"] = "C.UTF-8"  # bash -c skips the profile that sets it; grep -P then fails on every check
    cmd = sys.argv[1:]
    if not cmd:
        serve()
    elif cmd[0] == "hook-stop":
        if (note := hook_stop(json.loads(sys.stdin.read() or "{}"))):
            # asyncRewake: exit 2 wakes Claude with stderr, so the note reaches the user while the session is idle
            print("frugal heads-up, from a side agent that read this session. Show it to the user as one line starting with "
                  f"\"Heads up:\" in their language, then stop; do not act on it unless they ask.\nNote: {note}", file=sys.stderr)
            sys.exit(2)
    elif cmd[0] in ("hook-prompt", "hook-pretool"):
        event = json.loads(sys.stdin.read() or "{}")
        if cmd[0] == "hook-prompt":
            print(hook_prompt(event))
        else:
            reason, note = gate(event)
            if reason:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
            elif note:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note}}))
    elif cmd[0] == "selftest":
        print(selftest())
    else:
        with file_lock():
            print(cli(cmd), flush=True)
        for a in PENDING:
            print(login(a))
