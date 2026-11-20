"""Zero-token benchmark: compares frugal versions on real Claude Code transcripts.

Usage: python bench/transcripts.py <projects dir> [...]   e.g. ~/.claude/projects
Each session is tagged with the frugal version whose skill it loaded ("dev" for --plugin-dir, "none" without
/frugal). Tasks differ between sessions, so read differences across many sessions, not single ones.
A tool result or hook injection costs its size once per later API call that re-sends it (until a compaction).
"""
import glob
import json
import os
import re
import statistics
import sys
from collections import defaultdict

VERSION = re.compile(r"cache[\\/]+frugal[\\/]+frugal[\\/]+(\d+\.\d+\.\d+)")
USED = re.compile(r"command-name>/frugal|\"skill\":\"frugal")
TRIM = 3000  # chars an output trimmer would keep


def text(c):
    if isinstance(c, str):
        return c
    return "".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""


def hooks(o, out):
    if isinstance(o, dict):
        if o.get("hookEvent") == "UserPromptSubmit" and isinstance(o.get("content"), str):
            out.append(len(o["content"]))
        for v in o.values():
            hooks(v, out)
    elif isinstance(o, list):
        for v in o:
            hooks(v, out)


def session(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    m = VERSION.search(raw)
    version = m.group(1) if m else "dev" if USED.search(raw) else "none"
    s = defaultdict(int)
    events, names, ids = [], {}, set()  # in order: ("call", input chars), ("cut", 0), (kind, chars)
    for line in raw.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("subtype") == "compact_boundary":
            events.append(("cut", 0))
        m = o.get("message") if isinstance(o.get("message"), dict) else {}
        content = m.get("content")
        if o.get("type") == "assistant":
            for c in content or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    names[c.get("id")] = c.get("name")
            u = m.get("usage")
            if u and m.get("id") not in ids:
                ids.add(m.get("id"))
                s["output"] += u.get("output_tokens") or 0
                events.append(("call", 4 * sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))))
        elif o.get("type") == "user" and not o.get("isMeta"):
            if isinstance(content, str) or (isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "text" for c in content)):
                if "<task-notification>" not in text(content):
                    s["prompts"] += 1
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    shell = names.get(c.get("tool_use_id")) in ("Bash", "PowerShell")
                    events.append(("shell" if shell else "tool", len(text(c.get("content")))))
        h = []
        hooks(o, h)
        events += [("hook", n) for n in h]
    later = 0
    for kind, n in reversed(events):
        if kind == "call":
            later += 1
            s["calls"] += 1
            s["input"] += n
        elif kind == "cut":
            later = 0
        else:
            s[kind] += n * later
            if kind == "shell":
                s["trim"] += max(0, n - TRIM) * later
    return version, s


def main(dirs):
    by = defaultdict(list)
    for d in dirs:
        for f in glob.glob(os.path.join(os.path.expanduser(d), "**", "*.jsonl"), recursive=True):
            if os.sep + "subagents" + os.sep in f or "/subagents/" in f:
                continue
            version, s = session(f)
            if s["calls"] and s["prompts"]:
                by[version].append(s)
    key = lambda v: (v in ("dev", "none"), tuple(int(x) for x in v.split(".")) if v[0].isdigit() else ())
    print(f"{'version':9} {'sessions':>8} {'calls/prompt':>12} {'k tok/prompt':>12} {'out/reply':>9} {'shell%':>7} {'tools%':>7} {'hook%':>6} {'trim%':>6}")
    for v in sorted(by, key=key):
        ss = by[v]
        tot = lambda k: sum(s[k] for s in ss)
        inp = max(1, tot("input"))
        per_prompt = statistics.median(s["input"] / 4 / s["prompts"] / 1000 for s in ss)
        print(f"{v:9} {len(ss):8} {tot('calls') / tot('prompts'):12.1f} {per_prompt:12.1f} {tot('output') / tot('calls'):9.0f}"
              f" {tot('shell') / inp:7.1%} {tot('tool') / inp:7.1%} {tot('hook') / inp:6.2%} {tot('trim') / inp:6.1%}")
    print("k tok/prompt: median per session of input tokens per typed prompt; trim%: input saved by cutting shell output to 3k chars")


if __name__ == "__main__":
    main(sys.argv[1:] or ["~/.claude/projects"])
