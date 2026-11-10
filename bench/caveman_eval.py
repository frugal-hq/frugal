#!/usr/bin/env python3
"""caveman's own eval (evals/ in the caveman plugin, MIT) with a frugal arm and a quality judge.

Each dev question runs through `claude -p` with a replaced system prompt: "Answer concisely." alone (the control),
plus caveman's SKILL.md, plus frugal's SKILL.md. caveman's README says the honest delta is skill vs control.
Output tokens come from Claude Code's own usage report. A sonnet judge scores each answer blind, 0-3.

  python caveman_eval.py --model opus --runs 2
Prompts and caveman's SKILL.md are read from the installed plugin, not copied here.
"""
import argparse, concurrent.futures, json, os, re, shutil, statistics, subprocess, time
from pathlib import Path

CAVEMAN = sorted((Path.home() / ".claude/plugins/cache/caveman/caveman").glob("*"))[-1]
FRUGAL = Path(__file__).resolve().parents[1]
PROMPTS = [l.strip() for l in (CAVEMAN / "evals/prompts/en.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
TERSE = "Answer concisely."
ARMS = {
    "terse": TERSE,
    "caveman": TERSE + "\n\n" + (CAVEMAN / "skills/caveman/SKILL.md").read_text(encoding="utf-8"),
    "frugal": TERSE + "\n\n" + (FRUGAL / "skills/frugal/SKILL.md").read_text(encoding="utf-8"),
}
JUDGE = (
    "You grade an answer to a developer's question. Score how well it lets the developer understand and act: "
    "3 = correct and complete enough to act on; 2 = correct but missing a step or caveat that matters; "
    "1 = partly wrong, or too compressed to follow; 0 = wrong or unusable. Ignore length and style unless they "
    'hurt understanding. Respond with ONLY this JSON: {"score": <0-3>, "why": "<one line>"}'
)
CLAUDE = shutil.which("claude") or "claude"
TMP = os.environ.get("TEMP", "/tmp")


def claude(prompt, system, model):
    # system prompt from a file: claude is a .cmd on Windows, and cmd.exe cuts argv at the first newline
    f = Path(TMP) / f"cave-eval-sys-{abs(hash(system))}.txt"
    if not f.exists(): f.write_text(system, encoding="utf-8")
    r = subprocess.run([CLAUDE, "-p", "--model", model, "--system-prompt-file", str(f), "--tools", "",
                        "--setting-sources", "local", "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=TMP)
    return json.loads(r.stdout)


def judge(question, answer):
    j = claude(f"{JUDGE}\n\nQUESTION:\n{question}\n\nANSWER:\n{answer}", "You are a strict grader.", "sonnet")
    m = re.search(r'"score"\s*:\s*(\d)', j.get("result", ""))
    return int(m.group(1)) if m else None


def cell(arm, i, rep, model):
    out = claude(PROMPTS[i], ARMS[arm], model)
    text = out.get("result", "")
    return {"arm": arm, "prompt": i, "rep": rep, "output_tokens": out["usage"]["output_tokens"],
            "cost": out.get("total_cost_usd"), "chars": len(text), "score": judge(PROMPTS[i], text), "answer": text}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opus")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--arms", default="terse,caveman,frugal")
    ap.add_argument("--variant", action="append", default=[], help="name=path of a frugal SKILL.md variant; add name to --arms")
    a = ap.parse_args()
    for v in a.variant:
        name, path = v.split("=", 1)
        ARMS[name] = TERSE + "\n\n" + Path(path).read_text(encoding="utf-8")
    arms = a.arms.split(",")
    jobs = [(arm, i, r) for r in range(a.runs) for i in range(len(PROMPTS)) for arm in arms]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
        for f in concurrent.futures.as_completed([ex.submit(cell, *j, a.model) for j in jobs]):
            try: rows.append(f.result())
            except Exception as e: print("cell failed:", e)
            print(f"  [{len(rows)}/{len(jobs)}]", flush=True)
    out = FRUGAL / "bench" / f"caveman_eval_{a.model}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"model": a.model, "caveman": CAVEMAN.name, "rows": rows}, indent=1), encoding="utf-8")
    print(f"\n{'arm':8} {'n':>3} {'median out tok':>15} {'mean out tok':>13} {'mean chars':>11} {'mean score':>11} {'scores<3':>9}")
    for arm in arms:
        r = [x for x in rows if x["arm"] == arm]
        s = [x["score"] for x in r if x["score"] is not None]
        print(f"{arm:8} {len(r):>3} {statistics.median(x['output_tokens'] for x in r):>15.0f} "
              f"{statistics.mean(x['output_tokens'] for x in r):>13.0f} {statistics.mean(x['chars'] for x in r):>11.0f} "
              f"{sum(s) / max(len(s), 1):>11.2f} {sum(1 for v in s if v < 3):>9}")
    print("wrote", out)


if __name__ == "__main__":
    main()
