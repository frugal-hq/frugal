"""A/B/C token benchmark for frugal.

Conditions (caveman and ponytail at the level in %APPDATA%/<plugin>/config.json, ultra here; humanizer off everywhere):
  A  plain Claude Code
  B  caveman + ponytail
  C  caveman + ponytail, prompt prefixed with /frugal
  D  same as C, kept for runs made after installing frugal 0.2
  E  /frugal alone
  F  caveman alone
  G  ponytail alone
  R  /frugal delegate (delegation module forced on)
  T  /frugal tight (experimental in 0.4, removed in 1.0; E is plain /frugal on the plugin from 0.4 on)

Usage:
  python bench.py run [--reps 2] [--workers 3] [--model claude-opus-5-5]
  python bench.py report
"""
import argparse, csv, json, os, re, shutil, statistics as st, subprocess, sys, tempfile, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
RES = HERE / "results"

TASKS = {
    "t1_bug": "Running `python test_inventory.py` fails. Find and fix the bug in the inventory package. Do not modify the test.",
    "t2_analysis": "sales.csv has monthly revenue per product for 2025. Which product had the largest month-over-month revenue drop in absolute terms, between which two months, and how much was the drop? Answer in one short paragraph.",
    "t3_email": "Draft a reply to the customer email in email.txt, from the support team at Acme Reports. Save it to reply.txt. Do not promise refund amounts or timelines you do not know; use placeholders where needed.",
    # long session: one user message per turn, same session via --resume
    "t4_long": [
        "List the files in this folder and say in one line what each is for.",
        "Run `python test_inventory.py` and tell me the error.",
        "Find and fix the bug in the inventory package. Do not modify the test.",
        "Add SKU C300 to the catalog: price 7.25, bulk_min 50, bulk_discount 0.15.",
        "Add an assert to test_inventory.py for order_total([(\"C300\", 50)]) with the value the rules give, then run the tests.",
        "Change TAX to 0.0825 and update the expected values in the test so it passes.",
        "Using sales.csv, give total 2025 revenue per product as a table.",
        "Which product had the largest month-over-month revenue drop in absolute terms, between which two months, and how much?",
        "Which month had the highest total revenue across all products, and what was the total?",
        "Write the per-product totals to totals.csv with columns product,total_usd.",
        "Add order_count_by_sku(lines) to inventory/orders.py, returning a dict of SKU to total quantity. Add one assert for it to the test.",
        "Run the tests again and confirm they pass.",
        "Draft a short email to customers announcing SKU C300 and its bulk discount. Save it to announce.txt.",
        "Review orders.py and pricing.py for any other bug. Report only, do not edit.",
        "With the current rules, what is the order total for 30 x B200 and 12 x A100? Reply with the number only.",
        "Summarize everything done in this session in 5 bullets.",
    ],
    # heavy: big log (subagent bait), code from scratch (ponytail), long explanation (caveman)
    "t5_heavy": [
        "app.log is last night's server log. Exactly one request returned HTTP 500. Find its request id and the root cause, and quote the exception line.",
        "Implement a token bucket rate limiter in ratelimit.py: class RateLimiter(rate, capacity) with allow(now) -> bool, where now is a timestamp in seconds passed by the caller. The bucket starts full with capacity tokens, refills at rate tokens per second up to capacity, and each allowed call consumes one token. Add tests in test_ratelimit.py and run them.",
        "Explain in detail, for a new developer, how the inventory package computes an order total.",
    ],
    # delegation bait: 30 contracts (~80k tokens) whose terms grep alone cannot normalize
    "t6_contracts": "The contracts/ folder has 30 vendor agreements. Write vendor_terms.csv with columns vendor,notice_days,auto_renews where notice_days is the termination-for-convenience notice period in days (6 months = 180) and auto_renews is yes or no. Then tell me which vendors need 90 or more days of notice.",
}
# delegation bait that a script cannot shortcut: every contract has to be read and judged
TASKS["t8_review"] = ("The contracts/ folder has 30 vendor agreements. Read each one and write risk_review.csv with columns "
                      "vendor,biggest_risk where biggest_risk is one sentence of at most 20 words naming the clause that is "
                      "riskiest for Customer and why. Then tell me the three vendors you would renegotiate first.")
# delegation case: ~165k tokens of contracts, each with one planted risk that has to be read and judged
TASKS["t9_review150"] = ("The contracts/ folder has 150 vendor agreements. Read each one and write risk_review.csv with columns "
                         "vendor,biggest_risk where biggest_risk is one sentence of at most 20 words naming the clause that is "
                         "riskiest for Customer and why. Then tell me the five vendors you would renegotiate first.")
TASKS["t0_ok"] = "Reply with only the word ok."  # fixed cost of having a plugin active
TASKS["t7_long40"] = TASKS["t4_long"] + [
    "Add SKU D400 to the catalog: price 19.99, bulk_min 5, bulk_discount 0.2.",
    "What is the order total for 5 x D400? Reply with the number only.",
    "Add catalog_skus() to inventory/catalog.py, returning the SKUs sorted alphabetically, with one assert for it in the test.",
    "Run the tests.",
    "Using sales.csv, what was Echo's average monthly revenue in 2025, rounded to the nearest dollar?",
    "Which product fell the least from 2025-01 to 2025-12, and by how much?",
    "Write monthly_totals.csv with columns month,total_usd for every month of 2025.",
    "Which quarter of 2025 had the highest total revenue, and what was it?",
    "Draft a two-sentence internal note to the sales team about the top quarter. Save it to note.txt.",
    "Rename TAX to TAX_RATE everywhere and run the tests.",
    "What does order_count_by_sku([(\"A100\", 2), (\"A100\", 3), (\"B200\", 1)]) return?",
    "Make line_total raise ValueError when qty is not a positive integer. Add an assert for it in the test and run the tests.",
    "Summarize the changes to inventory/ so far in 4 bullets.",
    "What is the order total for 30 x B200 and 12 x A100 now? Reply with the number only.",
    "Add a README.md in this folder explaining how to run the tests, in under 10 lines.",
    "Which SKU has the highest bulk discount?",
    "Change D400's price to 18.50.",
    "Is there any SKU whose bulk_min is above 40? Name it.",
    "Add an assert to the test that catalog_skus() includes D400.",
    "Run the tests again.",
    "List every file you created in this session.",
    "Which month had the lowest total revenue across all products, and what was the total?",
    "Draft a short reply to a customer asking whether D400 has a bulk discount. Save it to d400_reply.txt.",
    "Summarize everything done in this session in 6 bullets.",
]
T5_HIDDEN = """
from ratelimit import RateLimiter
r = RateLimiter(1, 3)
assert [r.allow(0) for _ in range(4)] == [True, True, True, False]
assert r.allow(1) and not r.allow(1)
assert [r.allow(10) for _ in range(4)] == [True, True, True, False]
"""
# humanizer is opt-in and interactive, so it stays out of the bench; AskUserQuestion has no user in -p
PLUGINS = {"A": (0, 0, 0), "B": (1, 1, 0), "C": (1, 1, 0), "D": (1, 1, 0), "E": (0, 0, 1), "F": (1, 0, 0), "G": (0, 1, 0),
           "T": (0, 0, 1), "R": (0, 0, 1), "P": (0, 0, 1), "V": (0, 0, 0)}  # (caveman, ponytail, frugal plugin); P: plugin on, not invoked
PREFIX = {"C": "/frugal ", "D": "/frugal ", "E": "/frugal:frugal ", "T": "/frugal:frugal tight ", "R": "/frugal:frugal delegate ", "V": "/frugal:frugal "}  # V: a frugal variant from FRUGAL_VARIANT_DIR via --plugin-dir  # C, D: loose-skill era


def cond_settings(cond):
    cav, pony, fru = PLUGINS[cond]
    return json.dumps({"enabledPlugins": {"caveman@caveman": bool(cav), "ponytail@ponytail": bool(pony),
                                          "frugal@frugal": bool(fru)},
                       "skillOverrides": {"humanizer": "off", "frugal:humanizer": "off"}})


def run_one(job, model):
    cond, task, rep = job
    out = RES / f"{cond}_{task}_{rep}"
    if (out / "stream.jsonl").exists():
        return
    work = Path(tempfile.mkdtemp(prefix="frugal-bench-")) / task
    shutil.copytree(FIX / task, work)
    msgs = TASKS[task] if isinstance(TASKS[task], list) else [TASKS[task]]
    sid, stream, rcs = str(uuid.uuid4()), "", []
    for i, msg in enumerate(msgs):
        prompt = (PREFIX.get(cond, "") if i == 0 else "") + msg
        cmd = [shutil.which("claude"), "-p", prompt, "--model", model, "--output-format", "stream-json",
               "--verbose", "--permission-mode", "bypassPermissions"]
        if len(msgs) == 1:
            cmd += ["--no-session-persistence"]
        else:
            cmd += ["--session-id", sid] if i == 0 else ["--resume", sid]
        cmd += ["--settings", cond_settings(cond), "--disallowedTools", "AskUserQuestion"]
        if cond == "V":
            cmd += ["--plugin-dir", os.environ["FRUGAL_VARIANT_DIR"]]
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=900)
        stream += p.stdout
        rcs.append(p.returncode)
        if "hit your session limit" in p.stdout:  # not saved, so a later run with the same --out retries it
            shutil.rmtree(work.parent, ignore_errors=True)
            print(f"limit {out.name}", flush=True)
            return
    out.mkdir(parents=True, exist_ok=True)
    (out / "stream.jsonl").write_text(stream, encoding="utf-8")
    shutil.copytree(work, out / "workdir", dirs_exist_ok=True)
    shutil.rmtree(work.parent, ignore_errors=True)
    print(f"done {out.name} rc={max(rcs)}", flush=True)


def check(task, d, texts):
    final_text = texts[-1]
    if task == "t0_ok":
        return "ok" in final_text.lower()
    if task == "t5_heavy":
        hidden = subprocess.run([sys.executable, "-c", T5_HIDDEN], cwd=d, capture_output=True).returncode == 0
        return all(["req-103712" in texts[0], "EUR" in texts[0], hidden,
                    "bulk" in texts[2].lower(), "tax" in texts[2].lower()])
    if task == "t6_contracts":
        a = json.loads((HERE / "answer_t6.json").read_text())
        try:
            got = {r["vendor"].strip(): r for r in csv.DictReader(open(d / "vendor_terms.csv", encoding="utf-8"))}

            def row_ok(vendor, want):
                r = got.get(vendor)
                if not r:
                    return False
                n = int(float(r["notice_days"]))
                days_ok = 180 <= n <= 184 if want["notice_days"] == 180 else n == want["notice_days"]
                return days_ok and r["auto_renews"].strip().lower() == want["auto_renews"]
            return all(row_ok(v, w) for v, w in a.items())
        except (OSError, KeyError, ValueError):
            return False
    if task == "t8_review":
        a = json.loads((HERE / "answer_t6.json").read_text())
        try:
            got = {r["vendor"].strip(): r["biggest_risk"].strip() for r in csv.DictReader(open(d / "risk_review.csv", encoding="utf-8"))}
        except (OSError, KeyError):
            return False
        return all(v in got and 3 <= len(got[v].split()) <= 25 for v in a)
    if task == "t9_review150":  # the planted risk must be the one named, for at least 90% of vendors
        a = json.loads((HERE / "answer_t9.json").read_text())
        keys = {"liability": r"liabil|indemn", "price": r"increase|rate|pric", "exclusivity": r"exclusiv|other provider",
                "ip": r"intellectual|ownership|owns|licen|ip", "data": r"data", "termination": r"terminat|penalt|early"}
        try:
            got = {r["vendor"].strip(): r["biggest_risk"].strip() for r in csv.DictReader(open(d / "risk_review.csv", encoding="utf-8"))}
        except (OSError, KeyError):
            return False
        hits = sum(v in got and bool(re.search(keys[k], got[v], re.I)) for v, k in a.items())
        return hits >= 0.9 * len(a)
    if task == "t7_long40":
        t = [x.replace(",", "") for x in texts]
        test_ok = subprocess.run([sys.executable, "test_inventory.py"], cwd=d, capture_output=True).returncode == 0
        src = "".join(p.read_text() for p in (d / "inventory").glob("*.py"))
        files = all((d / f).exists() for f in ("totals.csv", "announce.txt", "monthly_totals.csv", "note.txt", "README.md", "d400_reply.txt"))
        return all([test_ok, files, "TAX_RATE" in src, "18.5" in src, "86.56" in t[17], "9756" in t[20],
                    "Delta" in t[21] and "1037" in t[21], "209060" in t[23], "269.54" in t[29], "D400" in t[31],
                    "C300" in t[33], "2025-08" in t[37] or "August" in t[37], "54380" in t[37]])
    if task == "t4_long":
        a = json.loads((HERE / "answer_t4.json").read_text())
        t = [x.replace(",", "") for x in texts]
        test_ok = subprocess.run([sys.executable, "test_inventory.py"], cwd=d, capture_output=True).returncode == 0
        test = (d / "test_inventory.py").read_text()
        try:
            totals = {r["product"]: int(float(r["total_usd"])) for r in csv.DictReader(open(d / "totals.csv"))}
        except (OSError, KeyError, ValueError):
            totals = {}
        return all([test_ok, "C300" in test, "order_count_by_sku" in test, totals == a["totals"],
                    a["drop_product"] in t[7] and str(a["drop"]) in t[7],
                    (a["top_month"] in t[8] or "January" in t[8]) and str(a["top_month_total"]) in t[8],
                    a["order_total"] in t[14], (d / "announce.txt").exists()])
    if task == "t1_bug":
        test_ok = subprocess.run([sys.executable, "test_inventory.py"], cwd=d, capture_output=True).returncode == 0
        untouched = (d / "test_inventory.py").read_bytes() == (FIX / task / "test_inventory.py").read_bytes()
        return test_ok and untouched
    if task == "t2_analysis":
        a = json.loads((HERE / "answer_t2.json").read_text())
        t = final_text.replace(",", "")
        return a["product"] in t and str(a["drop"]) in t
    reply = d / "reply.txt"
    if not reply.exists():
        return False
    t = reply.read_text(encoding="utf-8", errors="ignore").lower()
    return all(k in t for k in ("4471", "4472", "export")) and "user" in t


def parse(run_dir):
    tools, results = {}, []
    for line in (run_dir / "stream.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            for c in e["message"].get("content", []):
                if c.get("type") == "tool_use":
                    name = c["name"] + (":" + c["input"].get("skill", "") if c["name"] == "Skill" else "")
                    tools[name] = tools.get(name, 0) + 1
        if e.get("type") == "result":
            results.append(e)
    return tools, results


def report():
    rows = []
    for d in sorted(p for p in RES.iterdir() if p.is_dir()):
        cond, task, rep = d.name.split("_", 1)[0], "_".join(d.name.split("_")[1:3]), d.name.split("_")[-1]
        tools, rs = parse(d)
        if len(rs) != (len(TASKS[task]) if isinstance(TASKS[task], list) else 1):
            print("missing results:", d.name, len(rs))
            continue
        tot = lambda f: sum(f(r) for r in rs)
        u = lambda r, k: r.get("usage", {}).get(k, 0)
        inp = lambda r: u(r, "input_tokens") + u(r, "cache_creation_input_tokens") + u(r, "cache_read_input_tokens")
        rows.append(dict(cond=cond, task=task, rep=rep, ok=check(task, d / "workdir", [r.get("result", "") for r in rs]),
                         out=tot(lambda r: u(r, "output_tokens")), inp=tot(inp),
                         turns=tot(lambda r: r.get("num_turns", 0)), cost=rs[-1].get("total_cost_usd", 0),  # cumulative across --resume calls
                         sec=tot(lambda r: r.get("duration_ms", 0)) / 1000,
                         tools=sum(tools.values()), skills=",".join(k for k in tools if k.startswith("Skill")),
                         per_msg=[dict(cost=r.get("total_cost_usd", 0) - (rs[i - 1].get("total_cost_usd", 0) if i else 0),
                                       inp=inp(r), out=u(r, "output_tokens")) for i, r in enumerate(rs)]))
    print("| cond | task | rep | ok | output tok | input tok (incl cache) | turns | tool calls | cost USD | sec | skills |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['cond']} | {r['task']} | {r['rep']} | {r['ok']} | {r['out']} | {r['inp']} | {r['turns']} | {r['tools']} | {r['cost']:.3f} | {r['sec']:.0f} | {r['skills']} |")
    print("\n| cond | runs | passed | median output tok | median input tok | median cost USD | total cost USD |")
    print("|---|---|---|---|---|---|---|")
    for c in "ABCDEFGPRTV":
        g = [r for r in rows if r["cond"] == c and r["task"] != "t4_long"]
        if g:
            print(f"| {c} | {len(g)} | {sum(r['ok'] for r in g)} | {st.median(r['out'] for r in g):.0f} | {st.median(r['inp'] for r in g):.0f} | "
                  f"{st.median(r['cost'] for r in g):.3f} | {sum(r['cost'] for r in g):.3f} |")
    fixed = [r for r in rows if r["task"] == "t0_ok" and r["rep"] != "1"]  # rep 1 pays the cache write
    if fixed:
        base = [r for r in fixed if r["cond"] == "A"]
        print("\n| cond | t0_ok warm runs | median input tok | vs A | median cost USD | vs A |")
        print("|---|---|---|---|---|---|")
        for c in "ABCDEFGPRTV":
            g = [r for r in fixed if r["cond"] == c]
            if g:
                i, k = st.median(r["inp"] for r in g), st.median(r["cost"] for r in g)
                di = f"{i - st.median(r['inp'] for r in base):+.0f}" if base else "-"
                dk = f"{k - st.median(r['cost'] for r in base):+.4f}" if base else "-"
                print(f"| {c} | {len(g)} | {i:.0f} | {di} | {k:.4f} | {dk} |")
    long = [r for r in rows if r["task"] == "t4_long"]
    if long:
        print("\n| t4_long msg | " + " | ".join(f"{c} median cost USD | {c} median input tok" for c in "ABCDEFGPRTV") + " |")
        print("|---|" + "---|---|" * 11)
        for i in range(len(TASKS["t4_long"])):
            cells = []
            for c in "ABCDEFGPRTV":
                g = [r["per_msg"][i] for r in long if r["cond"] == c]
                cells += [f"{st.median(m['cost'] for m in g):.3f}", f"{st.median(m['inp'] for m in g):.0f}"] if g else ["-", "-"]
            print(f"| {i + 1} | " + " | ".join(cells) + " |")
    (RES / "rows.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--conds", default="ABC", help="D = C after swapping in a new frugal version; E = plugins off + /frugal")
    ap.add_argument("--tasks", default=",".join(TASKS), help="comma-separated task names")
    ap.add_argument("--out", default="results", help="results folder under bench/")
    a = ap.parse_args()
    RES = HERE / a.out
    if a.cmd == "report":
        report()
    else:
        RES.mkdir(exist_ok=True)
        # interleave conditions so time-of-day drift hits all three equally
        jobs = [(c, t, r) for r in range(1, a.reps + 1) for t in a.tasks.split(",") for c in a.conds]
        with ThreadPoolExecutor(a.workers) as ex:
            list(ex.map(lambda j: run_one(j, a.model), jobs))
