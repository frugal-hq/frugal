# Codebase map for the first code prompt: top-level names per folder, so reuse costs no search.
# frugal: regex, not a parser; nested or dynamic definitions are missed.
import os
import re
import subprocess

BUDGET = 2000      # chars, about 500 tokens
MIN_FILES = 8      # fewer source files: the model sees them without a map
MAX_FILES = 4000
SKIP_DIR = re.compile(r'(^|/)(node_modules|\.git|dist|build|out|vendor|venv|\.venv|__pycache__|coverage|\.next|target'
                      r'|migrations|versions|fixtures|tests?|__tests__|spec)(/|$)')
SKIP_FILE = re.compile(r'(^|/)(test_[^/]*|[^/]*(_test|\.test|\.spec|\.gen|\.min|\.d)\.[a-z]+)$')
SHARED = re.compile(r'util|helper|lib|common|shared|service|component|hook|core|model|schema|api', re.I)
PY = [r'^(?:async\s+)?def\s+([A-Za-z]\w*)', r'^class\s+([A-Za-z]\w*)']
JS = [r'^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)']
JVM = [r'^\s*(?:public\s+)?(?:static\s+)?(?:final\s+)?(?:class|interface|enum|record)\s+(\w+)']
LANG = {"py": PY, "js": JS, "jsx": JS, "mjs": JS, "cjs": JS, "ts": JS, "tsx": JS,
        "go": [r'^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)', r'^type\s+([A-Z]\w*)'],
        "rs": [r'^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type)\s+(\w+)'],
        "rb": [r'^\s*(?:def|class|module)\s+([A-Za-z][\w.]*)'],
        "java": JVM, "kt": JVM, "swift": JVM, "cs": JVM, "php": JVM}
LANG = {k: [re.compile(p, re.M) for p in v] for k, v in LANG.items()}
EXPORT_LIST = re.compile(r'^export\s*\{([^}]*)\}', re.M)


def files(root):
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, timeout=2, check=True).stdout
        return out.splitlines()[:MAX_FILES]
    except (OSError, subprocess.SubprocessError):
        found = []
        for d, dirs, names in os.walk(root):
            rel = os.path.relpath(d, root).replace("\\", "/")
            dirs[:] = [x for x in dirs if not SKIP_DIR.search(rel + "/" + x + "/")]
            found += [x if rel == "." else rel + "/" + x for x in names]
            if len(found) >= MAX_FILES:
                break
        return found[:MAX_FILES]


def names(text, ext):
    got = [m.group(1) for p in LANG[ext] for m in p.finditer(text)]
    if ext in ("js", "jsx", "mjs", "cjs", "ts", "tsx"):
        got += [n.split(" as ")[-1].strip() for m in EXPORT_LIST.finditer(text) for n in m.group(1).split(",")]
    return list(dict.fromkeys(n.removeprefix("type ") for n in got if n and not n.startswith("_")))


def build(root):
    src = [f for f in files(root) if f.rsplit(".", 1)[-1] in LANG and not SKIP_DIR.search(f) and not SKIP_FILE.search(f)]
    if len(src) < MIN_FILES:
        return ""
    by = {}
    for f in src:
        try:
            if os.path.getsize(os.path.join(root, f)) > 200_000:
                continue
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as h:
                found = names(h.read(), f.rsplit(".", 1)[-1])
        except OSError:
            continue
        d = f.rsplit("/", 1)[0] + "/" if "/" in f else "./"
        by.setdefault(d, []).append(found)
    lines = []
    for d, per_file in sorted(by.items(), key=lambda x: (not SHARED.search(x[0]), x[0])):
        # Round-robin over files: each file's main name first, so a folder of components lists every component.
        n = list(dict.fromkeys(x for i in range(max(map(len, per_file))) for f in per_file if i < len(f) for x in [f[i]]))
        if n:
            lines.append(f"{d}: {', '.join(n[:16])}{' ...' if len(n) > 16 else ''}")
    out, size = [], 0
    for line in lines:
        if size + len(line) > BUDGET:
            continue
        out.append(line)
        size += len(line) + 1
    return "Codebase map (what already exists; reuse it, read a file only for its details):\n" + "\n".join(out) + "\n" if out else ""


if __name__ == "__main__":
    import sys
    import tempfile
    if len(sys.argv) > 1:
        print(build(sys.argv[1]), end="")
        sys.exit()
    with tempfile.TemporaryDirectory() as t:
        os.makedirs(t + "/src/utils")
        os.makedirs(t + "/tests")
        for i in range(8):
            with open(f"{t}/src/m{i}.py", "w") as h:
                h.write(f"def f{i}():\n    pass\nclass C{i}:\n    def method(self): pass\ndef _private(): pass\n")
        with open(t + "/src/utils/fmt.ts", "w") as h:
            h.write("export function money(c) {}\nconst x = 1\nexport { x as y, z }\n")
        with open(t + "/tests/test_a.py", "w") as h:
            h.write("def test_hidden(): pass\n")
        m = build(t)
        assert m.index("src/utils/: money, y, z") < m.index("src/: f0, f1"), m
        assert "method" not in m and "_private" not in m and "test_hidden" not in m, m
        os.remove(f"{t}/src/m0.py")
        os.remove(f"{t}/src/m1.py")
        assert build(t) == "", "under MIN_FILES: no map"
        print("ok")
