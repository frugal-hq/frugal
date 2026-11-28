# Runs on every user prompt; prints nothing unless /frugal is active in this session.
# - First code-looking prompt (clear signals only; a miss costs one Read, since SKILL.md has the model read code.md): injects code.md, so the model has the code rules without spending a turn on Read.
# - 10th prompt, every 15th after it, and the prompt after a compaction: injects core.md and session.md unless already
#   in context (a skill cannot count messages reliably; a hook can); with both loaded, a round gets one reminder line.
# - Context past 150k tokens, then every 50k: the handoff notice, with the handoff rule the first time (message count is a poor proxy for what each message re-sends).
# - Pasted handoff: a /frugal line inside pasted text is not run as a command, so tell the model to load the skill until it does.
# Keeps small per-session marker files in CLAUDE_PLUGIN_DATA; prune by age if that ever matters.
import json
import os
import re
import sys
import tempfile

import codemap

CODE = re.compile(
    r'/frugal(:frugal)? [^\\"\x00-\x1f]*\bcod(e|ing)\b'
    r'|\b(implement\w*|refactor\w*|endpoints?|tracebacks?|exceptions?|stack ?traces?|compil\w*|migrat\w*|bugs?|crash\w*'
    r'|regex\w*|functions?|unit tests?|failing tests?|tests? fail\w*|pull requests?|c(o|ó)digo|funci(o|ó)n\w*'
    r'|python|javascript|typescript|node|nodejs|react|vue|svelte|angular|nextjs|django|flask|fastapi|rust|golang|kotlin'
    r'|swift|java|php|ruby|bash|powershell|sql|html|css|scripts?|components?|frontend|backend|api|apis|rest api|cli'
    r'|command-line|class|classes|dataclass\w*|decorators?|modules?|library|parser|scraper|websockets?|database|schema'
    r'|componente\w*|aplicaci(o|ó)n web)\b|\bc(\+\+|#)(?!\w)|```'
    r'|\.(py|ts|tsx|js|jsx|go|rs|java|kt|cs|rb|php|cpp|c|h|sql|css|html|sh)\b', re.I)
VERB = re.compile(r'(/frugal(:frugal)? )?(add|implement|build|create|make|write|fix|refactor|change|update|remove|rename'
                  r'|agrega|crea|implementa|arregla|corrige|cambia)\b', re.I)
PROJECT = {"package.json", "pyproject.toml", "requirements.txt", "setup.py", "go.mod", "Cargo.toml", "pom.xml",
           "build.gradle", "Gemfile", "composer.json"}
SRC = r'\.(?:py|ts|tsx|js|jsx|go|rs|java|cs|rb|php|kt|cpp|c|h|sql|sh|css|html)'
SCOPED = re.compile(r'`[A-Za-z_][\w.]*\([^`]*\)`|\bin (the existing )?`[\w./-]+' + SRC + r'`|\bin the existing [\w./-]+' + SRC + r'\b')
EXTS = (".csproj", ".sln", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cs", ".rb", ".php")

raw = sys.stdin.buffer.read().decode("utf-8")
data = json.loads(raw)
sid = data.get("session_id")
# Background-task notifications arrive as prompts too; only count what the user typed.
if not sid or "<task-notification>" in raw:
    sys.exit(0)
prompt = data.get("prompt") or ""
d = os.environ.get("CLAUDE_PLUGIN_DATA") or tempfile.gettempdir()
os.makedirs(d, exist_ok=True)
try:
    with open(data.get("transcript_path") or "", encoding="utf-8", errors="replace") as f:
        text = f.read()
except OSError:
    text = ""
out = sys.stdout.buffer


def cat(p):
    with open(p, "rb") as f:
        return f.read()


# A plugin reinstall wipes CLAUDE_PLUGIN_DATA, so never count below the typed prompts in the transcript.
cmd = re.compile(r'"(content|text)":"<(command-name|local-command)')
typed = sum(1 for l in text.split("\n") if '"type":"user"' in l and '"origin":{"kind":"human"}' in l
            and '"isMeta":true' not in l and not cmd.search(l))
cf = os.path.join(d, "count-" + sid)
try:
    with open(cf) as f:
        n = int(f.read() or 0) + 1
except OSError:
    n = 1
n = max(n, typed)
with open(cf, "w") as f:
    f.write(str(n))
loaded = bool(re.search(r'command-name>/frugal|"skill":"frugal', text))
# A pasted handoff counts too: "/frugal" starting a line in the prompt or (JSON-escaped) in the transcript.
pasted = "\n/frugal" in prompt or "\\n/frugal" in text
active = "/frugal" in prompt or loaded or pasted
skill = os.environ.get("CLAUDE_PLUGIN_ROOT", "").replace("\\", "/") + "/skills/frugal"
if pasted and not loaded:
    a = re.findall(r'\n/frugal([^\\"\x00-\x1f]*)', prompt)
    m = re.search(r'(?m)^.*\\n/frugal([^\\"\n]*)', text)
    a = a[-1] if a else m.group(1) if m else ""
    if a.startswith(":frugal"):
        a = a[7:]
    out.write(f'frugal: the pasted text has a /frugal line, which does not run as a command. Before anything else, call Skill frugal:frugal with args "{a.strip(" ")}", then answer the user.\n'.encode())
rnd = n in (10, 25, 40, 55, 70, 85, 100)
# A rules file counts as loaded when its header (from a Read or an earlier injection) follows the last compaction.
cb = text.rfind('"subtype":"compact_boundary"')


def seen(name):
    return text.rfind("# Frugal: " + name) > cb


session = skill + "/modules/session.md"
missing = [p for name, p in (("core", skill + "/core.md"), ("session", session)) if not seen(name)]
# Rounds and the prompts after a compaction load what is missing; a pasted copy is re-sent on every later
# message, so a round with both loaded gets one line.
load = missing if rnd or cb >= 0 else []
# Context of the last reply: input plus cached tokens from its usage record.
u = re.findall(r'"usage":\{"input_tokens":(\d+),"cache_creation_input_tokens":(\d+),"cache_read_input_tokens":(\d+)', text)
ctx = sum(map(int, u[-1])) if u else 0
# Notice levels: 1 at 150k, then one more every 50k.
level = 0 if ctx < 150000 else (ctx - 150000) // 50000 + 1
# Last level whose notice reached the model, read from the transcript: a marker file is spent even when
# Claude Code drops that run's output. A compaction boundary re-arms it.
k = 0
for m in re.finditer(r'"subtype":"compact_boundary"|"hookEvent":"UserPromptSubmit","content":"[^"\\\n]*frugal: the context is about (\d+)k', text):
    k = int(m.group(1) or 0)
fired = 0 if k < 150 else (k - 150) // 50 + 1
if level > fired and active:
    if fired == 0:
        out.write(f"frugal: the context is about {ctx // 1000}k tokens, re-sent on every message. If work remains, end this reply or the next at a natural breakpoint with the handoff block below.\n".encode())
        if session not in load:
            s = cat(session)
            m = re.search(rb"(?m)^\*\*Handoff\.\*\*", s)
            if m:
                out.write(s[m.start():])
    else:
        out.write(f"frugal: the context is about {ctx // 1000}k tokens, re-sent on every message. If work remains, end this reply or the next at a natural breakpoint with the frugal handoff block.\n".encode())
if (load or rnd) and active:
    if load:
        out.write(f"frugal: this is the user's message {n}. The session rules below are loaded; apply them.\n".encode())
        for p in load:
            out.write(cat(p))
    else:
        out.write(f"frugal: this is the user's message {n}. The frugal core and session rules loaded earlier in this chat still apply: terse replies, fewer calls, a handoff when the context grows.\n".encode())
cwd = data.get("cwd") or ""
rules = b""
# Scoped change to a named function or file: the short code-lite.md instead of code.md.
if active and SCOPED.search(prompt):
    if not seen("code") and not seen("scoped code"):
        rules = b"frugal code rules (loaded by a hook, short version for a scoped change; do not Read code.md):\n" + cat(skill + "/code-lite.md")
elif active and not seen("code") and (CODE.search(prompt) or VERB.match(prompt) and os.path.isdir(cwd) and any(
        f in PROJECT or f.endswith(EXTS) for f in os.listdir(cwd))):
    rules = b"frugal code rules (loaded by a hook; do not Read code.md):\n" + cat(skill + "/code.md")
if rules:
    out.write(rules)
    # With the code rules, once per context: what already exists, so reuse costs no search.
    if os.path.isdir(cwd) and text.rfind("Codebase map (") <= cb:
        out.write(codemap.build(cwd).encode())
