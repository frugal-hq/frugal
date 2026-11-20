"""Zero-token checks of hooks/session-count.sh: what it injects for a given transcript. Needs `sh` (Git Bash on Windows).

Usage: python bench/hook_test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOADED = {"skill": "frugal"}
CORE = {"toolUseResult": "1\t# Frugal: core (tasks over 3 tool calls)"}
SESSION = {"attachment": {"hookEvent": "UserPromptSubmit", "content": "x\n# Frugal: session\n"}}
CODE = {"r": "# Frugal: code"}
COMPACT = {"type": "system", "subtype": "compact_boundary"}


def usage(tokens):
    return {"message": {"usage": {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": tokens}}}


def notice(k):
    return {"attachment": {"hookEvent": "UserPromptSubmit", "content": f"frugal: the context is about {k}k tokens"}}


def run(tmp, n, prompt, *lines):
    sid = f"s{abs(hash((n, prompt, json.dumps(lines))))}"
    path = os.path.join(tmp, sid + ".jsonl").replace("\\", "/")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(x, separators=(",", ":")) + "\n" for x in lines)  # compact, as Claude Code writes them
    with open(os.path.join(tmp, "count-" + sid), "w") as f:
        f.write(str(n - 1))
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": ROOT, "CLAUDE_PLUGIN_DATA": tmp}
    stdin = json.dumps({"session_id": sid, "transcript_path": path, "prompt": prompt}, ensure_ascii=False)
    return subprocess.run(["sh", os.path.join(ROOT, "hooks", "session-count.sh")], input=stdin, env=env,
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def has(out, *parts):
    return all(p in out for p in parts)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        cases = [
            # Rules: the first round loads what is missing; later rounds get one line; a compaction reloads them.
            ("round 10 loads both", has(run(tmp, 10, "hi", LOADED), "# Frugal: core", "# Frugal: session")),
            ("round 10 skips core already read", (lambda o: "# Frugal: session" in o and "# Frugal: core" not in o)(run(tmp, 10, "hi", LOADED, CORE))),
            ("round 25 with both loaded: one line", (lambda o: "still apply" in o and "# Frugal" not in o)(run(tmp, 25, "hi", LOADED, CORE, SESSION))),
            ("compaction reloads off-round", has(run(tmp, 12, "hi", LOADED, CORE, SESSION, COMPACT), "# Frugal: core", "# Frugal: session")),
            ("loaded after compaction: nothing", run(tmp, 13, "hi", LOADED, COMPACT, CORE, SESSION) == ""),
            ("inactive session: nothing", run(tmp, 25, "fix the bug in a.py", CORE) == ""),
            # Context notices: 150k with the handoff block, then one line every 50k.
            ("150k notice with handoff", has(run(tmp, 11, "hi", LOADED, usage(160000)), "about 160k", "**Handoff.**")),
            ("200k notice one line", (lambda o: "about 210k" in o and "**Handoff.**" not in o)(run(tmp, 11, "hi", LOADED, notice(160), usage(210000)))),
            ("no notice inside a 50k step", run(tmp, 11, "hi", LOADED, notice(160), usage(190000)) == ""),
            ("compaction re-arms the notice", has(run(tmp, 11, "hi", LOADED, CORE, SESSION, notice(160), COMPACT, usage(210000)), "about 210k", "**Handoff.**")),
            # Code rules: clear signals only, once per context.
            ("code prompt loads code rules", "# Frugal: code" in run(tmp, 3, "fix the bug in a.py", LOADED)),
            ("common words are not code", run(tmp, 3, "add a reminder and build a travel plan", LOADED) == ""),
            ("code rules already in context", run(tmp, 3, "fix the bug in a.py", LOADED, CODE) == ""),
            ("code rules again after compaction", "# Frugal: code" in run(tmp, 3, "fix the bug in a.py", LOADED, CODE, COMPACT)),
            ("spanish code prompt", "# Frugal: code" in run(tmp, 3, "arregla esta función", LOADED)),
        ]
    failed = [name for name, ok in cases if not ok]
    print(f"{len(cases) - len(failed)}/{len(cases)} passed" + "".join(f"\nFAIL {n}" for n in failed))
    raise SystemExit(1 if failed else 0)
