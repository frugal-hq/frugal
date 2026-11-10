"""frugal accounts: secondary Claude, Codex, Grok and OpenCode logins that take delegated work.

One file, stdlib only, four roles:
  server.py                 MCP server (stdio) exposing `delegate` while an account is set up
  server.py hook-prompt     UserPromptSubmit hook: the delegation rule, then a one-line reminder
  server.py hook-pretool    PreToolUse hook: sends test runs, long explorations (shell reads too) and subagents to delegate
  server.py hook-stop       Stop hook (Claude Desktop only): a side agent reads the session and may wake Claude with a heads-up
  server.py list | add <claude|codex|grok> <id> | add opencode <id> <provider/model> | opencode-training <on|off> | remove <id> | login <id> | selftest
Nothing happens while ~/.frugal/accounts.json lists no account, so frugal without accounts is unchanged.
"""
import concurrent.futures, contextlib, datetime, json, os, re, shutil, signal, subprocess, sys, tempfile, threading, time, tomllib, traceback
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
HEAVY_RUN = re.compile(r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck)\b|\b(pytest|jest|vitest|tsc|gradlew?|mvn)\b|\bpython\S*\s+\S*bench\.py\b"
                       r"|\b(cargo|go|dotnet)\s+(test|build)\b|\bclaude\s+plugin\s+test\b", re.I)
CODEX_RULES = (
    "Work rules: report results, not process; no filler, preamble or closing summary. Keep exact terms, code, commands, errors, "
    "numbers and every not, never and only. Verify APIs, versions and flags; never guess. For code: reuse what the codebase has, "
    "then the stdlib or native features, then installed dependencies; never add one for a few lines. Minimum code: no unrequested "
    "features, files, abstractions or config. Fix bugs at the root cause, and fix siblings with the same bug. Never cut validation, "
    "security or error handling that prevents data loss."
)
EXPLORE_BUDGET = 3
# Shell commands that only read. A chain of them counts against EXPLORE_BUDGET as Read and Grep do: in one day 816 shell reads went uncounted.
READ_ONLY = re.compile(r"(cat|head|tail|less|grep|egrep|fgrep|rg|ls|dir|find|tree|wc|stat|file|du|awk|sed|jq|sort|uniq|cut|column|nl|diff|cmp"
                       r"|basename|dirname|realpath|which|where|git\s+(log|show|diff|status|grep|ls-files|blame|branch|rev-parse)"
                       r"|Get-Content|Get-ChildItem|Select-String|Test-Path)(\s|$)", re.I)
NEUTRAL = re.compile(r"(cd|echo|printf|true)(\s|$)")
TIMEOUT = {"heavy": 1200, "max": 1800}  # seconds per agent run; heavy edits ran past 10 minutes and started over
HEADS_UP = DIR / "heads-up.json"
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
        except FileExistsError:
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
    """This session's MCP servers, user scope plus the project's. Only a Claude delegate gets them, and only the ones a task names:
    which agents may reach a server's data is each project's rule, and frugal never hands these to Codex, Grok or OpenCode."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        data = json.loads((Path(folder) / ".claude.json" if folder else HOME / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = dict(data.get("mcpServers") or {})
    for project, conf in (data.get("projects") or {}).items():
        if same(project) == same(cwd):
            servers.update(conf.get("mcpServers") or {})
    return servers


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


def order(accounts, busy):
    day = today()
    return sorted(ready(accounts), key=lambda a: busy.get(a["id"], 0) * 1000 + (a.get("used", 0) if a.get("day") == day else 0))


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


def argv_for(a, kind, model, effort, mcp_off=(), mcp_file=None, mcp_names=()):
    if a["kind"] == "claude" and kind == "note":
        return ["claude", "-p", "--model", model, "--effort", effort, "--tools", "", "--strict-mcp-config"]  # no tools: it only reads the brief
    if a["kind"] == "claude":
        # Claude Code names a server's tools mcp__<name>__<tool>, with characters outside [A-Za-z0-9_-] turned into _.
        allowed = " ".join(["Bash Read Grep Glob" if kind in ("search", "run") else "Bash Read Grep Glob Edit Write",
                            *(f"mcp__{re.sub(r'[^A-Za-z0-9_-]', '_', n)}" for n in mcp_names)])
        tools = ["--allowedTools", allowed] if kind in ("search", "run") else ["--permission-mode", "acceptEdits", "--allowedTools", allowed]
        # Strict: without it every delegated run starts all of the user's MCP servers (WSL, Python, Node) and leaves them running.
        return ["claude", "-p", "--model", model, "--effort", effort, *tools, "--strict-mcp-config", *(["--mcp-config", mcp_file] if mcp_file else [])]
    if a["kind"] == "grok":
        # A headless grok refuses every call that needs approval and still exits 0, so it always approves; search keeps read tools and the shell, as Claude's does.
        tools = ["--tools", "read_file,grep,list_dir,run_terminal_cmd"] if kind == "search" else []
        return ["grok", "--no-auto-update", "-m", model, "--effort", effort, "--always-approve", *tools]
    if a["kind"] == "opencode":
        # --auto approves what is not denied; the plan agent denies edits, so search stays read-only (tested on opencode 1.19.5)
        return ["opencode", "run", "-m", model, "--variant", effort, "--agent", "plan" if kind == "search" else "build", "--auto"]
    sandbox = ["-s", "workspace-write"] if kind in ("edit", "run") else []
    off = [x for n in mcp_off for x in ("-c", f"mcp_servers.{n}.enabled=false")]  # only servers its config has: an unknown one breaks codex
    return ["codex", "exec", "--skip-git-repo-check", *(["-m", model] if model else []), "-c", f"model_reasoning_effort={effort}", *off, *sandbox, "-"]


def run_tree(cmd, timeout, input=None, **kw):
    # On timeout kill the whole tree: subprocess.run kills only the .cmd shim or shell, and the
    # grandchild keeps the pipes open, so communicate() never returns. None means timed out.
    # Never inherit stdin: in the MCP server it carries the protocol, and a command that reads it would eat requests.
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt", **kw)
    try:
        out, err = p.communicate(input, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # a process that escaped the tree still holds the pipes; give up on it
        return None


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


def run_check(check, cwd):
    sh = bash()
    if not sh:
        return 127, "bash not found; on Windows it comes with Git for Windows"
    r = run_tree([sh, "-c", check], 300, cwd=cwd)
    if r is None:
        return 124, "check timed out after 5 minutes"
    return r[0], (r[1] + r[2]).strip()


# The command could not run on this machine (missing tool, no permission, timeout); the code was never tested.
ENV_FAIL = re.compile(r"command not found|is not recognized as an internal|permission denied while trying to connect to the docker"
                      r"|cannot connect to the docker daemon|docker daemon is not running", re.I)


def env_failure(code, out):
    return code in (124, 126, 127) or (code != 0 and bool(ENV_FAIL.search(out)))


def do_here(check, code, out):
    return (f"frugal: check `{check}` exited {code} and could not run here:\n{out[-800:]}\n"
            "Do it here: run the same command again in your shell and it goes through.")


def run_brief(text, check, level, effort, cwd):
    # The check runs once, here; its exit code and output are the result. An agent only explains a failure, from that output.
    code, out = run_check(check, cwd)
    if env_failure(code, out):
        return do_here(check, code, out)
    if code == 0:
        return f"frugal: check `{check}` exited 0, passed:\n{out[-300:]}"
    notes = brief(f"{text}\n\nThe command `{check}` already ran and exited {code}. The end of its output:\n{out[-8000:]}\n\n"
                  "Find the cause of each failure from this output and the code. Do not run the command again.",
                  "search", None, level, effort, cwd)
    return f"frugal: check `{check}` exited {code}, failed:\n{out[-800:]}\n\nAgent notes, not a test result:\n{notes}"


BUSY = {}


def relogin(ids):
    return "".join(f"frugal: secondary account {i} is logged out; tell the user to run `/frugal:accounts login {i}`.\n" for i in ids)


def brief(text, kind, check, level, effort, cwd, mcp=()):
    if check and kind == "run":
        return run_brief(text, check, level, effort, cwd)
    found = session_mcp(cwd) if mcp else {}
    if (missing := [n for n in mcp if n not in found]):
        return f"frugal: this session has no MCP server named {', '.join(missing)}. Do it here."
    servers = {n: found[n] for n in mcp}
    raw = kind == "note"  # a heads-up check wants the bare answer, or "" when no account answered
    failure, tried, relog = "", [], []
    if check:
        text += f"\n\nDone when this bash command exits 0: {check}"
    for n in range(1, MAX_TRIES + 1):
        with LOCK:
            data = load()
            # A heads-up note carries the session transcript, which never goes to Grok or OpenCode.
            # A task that needs this session's MCP servers goes only to Claude accounts.
            pool = [a for a in order(data["accounts"], BUSY) if a["id"] not in tried and not (raw and a["kind"] in ("grok", "opencode"))
                    and not (servers and a["kind"] != "claude") and not blocked(a, data)]
            if not pool:
                break
            a = pool[0]
            BUSY[a["id"]] = BUSY.get(a["id"], 0) + 1
        tried.append(a["id"])
        model, eff = choose(a, kind, level, effort)
        prompt = (f"{text}\n\nA previous attempt failed: {failure}" if failure else text) + \
            ("" if raw else "\n\nReply in under 150 words: what you found or changed, with file paths.")
        if raw:
            pass  # the side agent gets only the brief
        elif a["kind"] == "claude":
            prompt = "/frugal:frugal " + prompt  # loads frugal's rules in the delegated session
        else:
            prompt = f"{CODEX_RULES}\n\n{prompt}"  # Codex cannot load the skill, so it gets the essentials
        env = {**os.environ, DELEGATED: "1", **account_env(a)}
        only = data.get("claude_only_mcp", [])  # MCP servers no other agent may reach
        if a["kind"] != "claude":
            # frugal: a token is found by the server's name in the variable (SERVICEMINDER_MCP_TOKEN); read bearer_token_env_var if one is named otherwise.
            env = {k: v for k, v in env.items() if not any(n.upper() in k.upper() for n in only)}
        if a["kind"] == "grok":
            env.update(GROK_CLAUDE_MCPS_ENABLED="false", GROK_CLAUDE_HOOKS_ENABLED="false")  # grok loads Claude's MCP servers and hooks by default
        off = [n for n in only if n in codex_mcp()] if a["kind"] == "codex" else []
        mcp_file = None
        if servers:  # a file, not an argument: server configs can carry tokens, and a command line is visible to every process
            fd, mcp_file = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": servers}, f)
        try:
            code, out, err = run_cli(argv_for(a, kind, model, eff, off, mcp_file, list(servers)), prompt, cwd, env, TIMEOUT.get(level, 600))
        finally:
            with LOCK:
                BUSY[a["id"]] -= 1
            if mcp_file:
                os.unlink(mcp_file)
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
        label = f"{a['id']} ({', '.join(filter(None, [model, f'{eff} effort']))})"
        if code != 0 or not out:
            failure = (err or out or f"exit {code}")[-400:]
            continue
        if check:
            c, tail = run_check(check, cwd)
            if env_failure(c, tail):
                return f"{notice}{do_here(check, c, tail)}\n\nChanges by {label}, untested:\n{out[:2000]}"
            if c != 0:
                failure = f"check `{check}` failed: {tail[-400:]}"
                continue
            return f"{notice}Done by {label}; check `{check}` exited 0, passed:\n{tail[-300:]}\n\nAgent notes:\n{out[:2000]}"
        if raw:
            return out
        return f"{notice}Done by {label}.\n{out[:2000]}"
    if raw:
        return ""
    return f"{relogin(relog)}frugal: no secondary account finished this{f' ({failure})' if failure else ''}. Do it here."


SPEC = {
    "name": "delegate",
    "description": "Runs bounded work on the user's secondary accounts so this account's limits last: searches, reading or summarizing files, "
                   "test or build runs, edits you can brief exactly. Independent pieces go in \"tasks\" and run in parallel. "
                   "Keep design and the final check here; \"do it here\" means do it yourself.",
    "inputSchema": {"type": "object", "required": ["kind"], "properties": {
        "task": {"type": "string", "description": "Goal, exact paths, done-when."},
        "tasks": {"type": "array", "items": {"type": "string"}, "description": "Independent briefs, run in parallel."},
        "kind": {"type": "string", "enum": ["search", "edit", "run"], "description": "search: read-only. edit: changes files. run: runs commands and reports."},
        "check": {"type": "string", "description": "Bash command (Git Bash on Windows) that exits 0 when done right. With kind run: the command to run, once."},
        "cwd": {"type": "string", "description": "Directory the work happens in (sandbox root, check runs there). Default: the session's project dir."},
        "level": {"type": "string", "enum": LEVELS, "description": "light: searches, reads. standard: edits, test runs. heavy: hard multi-file work. max: after heavy failed."},
        "effort": {"type": "string", "enum": EFFORTS[:-1], "description": "Overrides the effort of the level."},
        "mcp": {"type": "array", "items": {"type": "string"},
                "description": "MCP servers of this session the task needs (the <name> in mcp__<name>__ tools). Only a Claude account takes it."},
    }},
}


def delegate(args):
    briefs = [*(args.get("tasks") or []), *([args["task"]] if args.get("task") else [])]
    if not briefs:
        return "frugal: give a task or tasks."
    cwd = args.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    with concurrent.futures.ThreadPoolExecutor(len(briefs)) as ex:
        out = list(ex.map(lambda b: brief(b, args.get("kind", "search"), args.get("check"), args.get("level"), args.get("effort"), cwd, args.get("mcp") or ()), briefs))
    return out[0] if len(out) == 1 else "\n\n".join(f"## Task {i}\n{r}" for i, r in enumerate(out, 1))


def serve():
    def send(msg):
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, method = req.get("id"), req.get("method")
        if rid is None:
            continue  # notifications
        if method == "initialize":
            result = {"protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "frugal-accounts", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [SPEC] if active() else []}
        elif method == "tools/call":
            p = req.get("params", {})
            try:
                text = delegate(p.get("arguments") or {}) if p.get("name") == "delegate" and active() else "frugal: no secondary account is set up."
            except Exception as e:  # one bad call must not close the server for the rest of the session
                with open(DIR / "errors.log", "a", encoding="utf-8") as f:
                    traceback.print_exc(file=f)
                text = f"frugal: delegate failed ({type(e).__name__}: {e}). Do it here."
            result = {"content": [{"type": "text", "text": text}]}
        elif method == "ping":
            result = {}
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})
            continue
        send({"jsonrpc": "2.0", "id": rid, "result": result})


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
    state(sid).write_text(json.dumps({**s, "announced": ids, "reads": 0, "delegated": 0, "agent": False}), encoding="utf-8")
    if first:
        return (f"frugal: secondary accounts {ids} are set up. Delegate every bounded task with {TOOL}, level per task; "
                "keep here only design, judgment calls and the final check.")
    return nudge(s.get("reads", 0), s.get("delegated", 0), ids)


def nudge(reads, delegated, ids):
    # The same line on every prompt went unread (one session got it 120 times and delegated 14 times in 34 prompts);
    # this one shows only after a prompt that explored here, and says how much.
    if reads > EXPLORE_BUDGET and not delegated:
        return f"frugal: the last prompt ran {reads} reads and searches here and delegated none. Send bounded pieces to {ids} with {TOOL}."
    return ""


def heavy(cmd):
    head = re.split(r"<<-?\s*['\"]?\w+", cmd)[0]  # a heredoc body is data, not a command run here
    # ssh runs on another machine and may change it there or prompt for a key; CLAUDE_CONFIG_DIR= already uses another account
    return bool(HEAVY_RUN.search(head)) and not re.search(r"\bssh\s", head) and "CLAUDE_CONFIG_DIR=" not in head


def reads_only(cmd):
    """True when each command of a chain or pipeline only reads (cat, grep, ls, git log), with no redirect into a file."""
    if "<<" in cmd or "`" in cmd or "$(" in cmd:
        return False
    s = re.sub(r"\d?>\s*(/dev/null|&\d|\$null)", "", cmd)
    if ">" in s or re.search(r"\bsed\b[^|;&]*\s(-i|--in-place)", s) or re.search(r"\bfind\b[^|;&]*\s-(delete|exec|ok)", s):
        return False
    parts = [re.sub(r"^([A-Za-z_]\w*=\S*\s+)+", "", p.strip()) for p in re.split(r"&&|\|\||;|\|", s)]
    parts = [p for p in parts if p and not NEUTRAL.match(p)]
    return bool(parts) and all(READ_ONLY.match(p) for p in parts)


def gate(event):
    """The reason to send this tool call to delegate, or None to let it run."""
    if not active() or event.get("agent_id"):
        return None
    sid, tool, inp = event.get("session_id", ""), event.get("tool_name", ""), event.get("tool_input") or {}
    s = read_state(sid)
    reason = None
    def count_read():
        s["reads"] = s.get("reads", 0) + 1
        if s["reads"] == EXPLORE_BUDGET + 1:
            return (f"frugal: {EXPLORE_BUDGET} reads and searches this prompt already. Hand the rest of this exploration to {TOOL} "
                    "(kind \"search\", pieces in \"tasks\") and work from its summary. If you need the raw text here, call again.")
        return None
    if tool in ("Bash", "PowerShell"):
        cmd = inp.get("command", "")
        if heavy(cmd) and cmd not in s.get("waived", []):
            s["waived"] = [*s.get("waived", []), cmd][-20:]
            reason = (f"frugal: send this to {TOOL} with kind \"run\", the exact command as \"check\" and a brief asking for the failures; "
                      "it runs the command once in bash and returns its real exit code and output. If it says \"Do it here\", "
                      "run the same command again and it goes through.")
        elif reads_only(cmd):
            reason = count_read()
    elif tool in ("Read", "Grep", "Glob"):
        reason = count_read()
    elif tool == TOOL:
        s["delegated"] = s.get("delegated", 0) + 1
    elif tool == "Agent" and not s.get("agent"):
        s["agent"] = True
        reason = (f"frugal: a subagent here runs on the main account. Give this to {TOOL} instead (one brief per piece in \"tasks\"). "
                  "If it needs this session's own tools, call again and it goes through.")
    DIR.mkdir(exist_ok=True)
    state(sid).write_text(json.dumps(s), encoding="utf-8")
    return reason


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
    r = run_tree([exe, *cmd[1:]], 60, env={**os.environ, **account_env(a)}) if exe else None
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
    assert [a["id"] for a in ready(mine)] == ["x", "cx"]
    os.environ[ROLES] = "off"
    assert len(ready(mine)) == 3
    del os.environ["CLAUDE_CONFIG_DIR"], os.environ[ROLES]
    assert [a["id"] for a in ready([{"id": "main", "config_dir": str(HOME / ".claude")}, {"id": "cx"}])] == ["cx"]
    os.environ.update({k: v for k, v in saved.items() if v is not None})
    assert AUTH.search("API Error: 401 OAuth token has expired") and AUTH.search("Please run /login") and not AUTH.search("ok")
    assert [a["id"] for a in order(pool, {"old": 1})] == ["b", "a", "old"]
    assert "--effort" in argv_for(claude, "search", "haiku", "low") and "Bash Read Grep Glob" in argv_for(claude, "search", "haiku", "low")
    assert argv_for(codex, "run", None, "low")[-3:] == ["-s", "workspace-write", "-"]
    assert argv_for(claude, "note", "sonnet", "medium")[-3:] == ["--tools", "", "--strict-mcp-config"]
    grok = {"id": "g", "kind": "grok"}
    assert choose(grok, "edit", "heavy") == ("grok-4.6", "high")
    assert "--always-approve" in argv_for(grok, "edit", "grok-4.6", "high") and "--tools" not in argv_for(grok, "edit", "grok-4.6", "high")
    assert argv_for(grok, "search", "grok-4.6", "low")[-2:] == ["--tools", "read_file,grep,list_dir,run_terminal_cmd"]
    assert AUTH.search("Error: Not signed in. To authenticate")
    oc = {"id": "o", "kind": "opencode", "model": "opencode/glm-5.3-flash-free", "data_dir": "/d"}
    assert choose(oc, "search", "max") == ("opencode/glm-5.3-flash-free", "high") and account_env(oc) == {"XDG_DATA_HOME": "/d"}
    assert argv_for(oc, "search", *choose(oc, "search"))[-4:] == ["medium", "--agent", "plan", "--auto"] and "build" in argv_for(oc, "edit", "m", "low")
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
    assert reads_only("A=1 sed -n 1,5p f && echo --- && head f") and reads_only("find . -name '*.py' | wc -l")
    assert not any(reads_only(c) for c in ("sed -i s/a/b/ f", "cat a > b", "npm test", "find . -delete", "echo hi", "python - <<EOF", "cat $(ls)", "git commit -m x"))
    assert nudge(EXPLORE_BUDGET + 1, 0, "x").startswith("frugal: the last prompt ran 4") and nudge(9, 1, "x") == "" and nudge(EXPLORE_BUDGET, 0, "x") == ""
    cfg = Path(tempfile.mkdtemp())
    (cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"sm": {"command": "a"}}, "projects": {
        str(cfg).replace("\\", "/"): {"mcpServers": {"p": {"command": "b"}}}, "elsewhere": {"mcpServers": {"q": {"command": "c"}}}}}), encoding="utf-8")
    saved_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
    a = argv_for(claude, "search", "sonnet", "low", (), "m.json", ["sm", "my server"])
    assert a[-3:] == ["--strict-mcp-config", "--mcp-config", "m.json"] and "--strict-mcp-config" in argv_for(claude, "edit", "sonnet", "low") and a[a.index("--allowedTools") + 1] == "Bash Read Grep Glob mcp__sm mcp__my_server"
    assert heavy("cd x && npm test") and not heavy("python - <<'EOF'\nnpm test\nEOF")
    assert heavy("python bench.py run") and not heavy("cat core.md && grep -n t8 bench.py") and not heavy("ssh vps 'cd app && go test ./...'")
    # delegate's check, with a stub agent that always claims success
    prompts, g = [], globals()
    real = {k: g[k] for k in ("run_cli", "load", "save")}
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
        # Claude-only MCP: other agents lose the server's token and grok its Claude MCP servers; a note never goes to grok
        os.environ["FRUGALTEST_SERVICEMINDER_TOKEN"] = "x"
        assert set(session_mcp(str(cfg))) == {"sm", "p"}
        g.update(run_cli=lambda argv, prompt, cwd, env, timeout=600: (envs.append((argv[0], env, argv)), (0, "ok", ""))[1],
                 load=lambda: {"claude_only_mcp": ["serviceminder"], "accounts": [{"id": "gk", "kind": "grok"}, {"id": "oc", "kind": "opencode", "model": "m"}, {"id": "cl", "kind": "claude"}]})
        assert brief("look", "search", None, None, None, ".").startswith("Done by") and brief("note", "note", None, None, None, ".") == "ok"
        (k1, e1, a1), (k2, e2, _) = envs[-2:]
        assert k1 in ("grok", "opencode") and "FRUGALTEST_SERVICEMINDER_TOKEN" not in e1 and e1.get("GROK_CLAUDE_MCPS_ENABLED", "false") == "false"
        assert k2 == "claude" and "FRUGALTEST_SERVICEMINDER_TOKEN" in e2 and "--mcp-config" not in a1
        # A task naming an MCP server skips the others and reaches Claude with only that server; an unknown name runs nowhere.
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
        shutil.rmtree(cfg)
    return "selftest ok"


if __name__ == "__main__":
    # Windows pipes default to the ANSI code page: a brief with ” or Á closed the server ("Connection closed") and garbled every other accent.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
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
        elif (reason := gate(event)):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
    elif cmd[0] == "selftest":
        print(selftest())
    else:
        with file_lock():
            print(cli(cmd), flush=True)
        for a in PENDING:
            print(login(a))
