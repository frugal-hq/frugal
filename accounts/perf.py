"""Performance modes for one Claude Code chat. Stdlib only; Windows APIs via ctypes.

~/.frugal/performance.json is shared with Frugal Deck:
{"mode": "light"|"balanced"|"performance"|"custom",
 "custom": {"cpu": int, "ram": int, "gpu": int, "parallel": int}}.
A missing file means balanced. custom is used only when ~/.frugal-deck/settings.json exists.
Percentages are of the whole machine. parallel is how many shell commands run at once; 0 means no limit.
"""
import ctypes, json, os, re, signal, subprocess, sys, tempfile, threading, time
from ctypes import wintypes
from pathlib import Path

HOME = Path.home()
DIR = HOME / ".frugal"
PERF = DIR / "performance.json"
DECK = HOME / ".frugal-deck" / "settings.json"
GIB = 1024 ** 3
RESERVE_RAM = 2 * GIB  # performance always leaves this much physical RAM unused
# CpuRate is cycles per 10,000. The API accepts 1..10000; 0 is rejected.
CPU_RATE_ENABLE = 0x1
CPU_RATE_HARD_CAP = 0x4
JOB_CPU_RATE = 15  # JobObjectCpuRateControlInformation
JOB_BASIC_LIMIT = 2  # JobObjectBasicLimitInformation
JOB_LIMIT_PRIORITY = 0x00000020
BELOW_NORMAL = 0x00004000
STILL_ACTIVE = 259
PRESETS = {
    "light": {"cpu": 10, "ram": 10, "gpu": 0, "parallel": 1, "priority": "below_normal"},
    "balanced": {"cpu": 40, "ram": 40, "gpu": 30, "parallel": 3, "priority": "normal"},
    "performance": {"cpu": 90, "ram": 85, "gpu": 90, "parallel": 0, "priority": "normal"},
}
MODES = ("light", "balanced", "performance", "custom")
SHELLS = {"bash", "sh", "cmd", "pwsh", "powershell"}
REAP_NAMES = {"bash", "sh", "cmd", "conhost", "node", "python", "pwsh", "powershell", "git", "uv"}
MARKERS = ("shell-snapshots", ".claude", "claude-code", "CLAUDE_")
_logged = False
_WIN = None


def norm_exe(name):
    n = (name or "").lower()
    return n[:-4] if n.endswith(".exe") else n


def is_claude(row):
    name = norm_exe(row.get("name") or "")
    if name == "claude":
        return True
    return name == "node" and "claude-code" in (row.get("cmd") or "")


def deck_installed(path=None):
    return (DECK if path is None else Path(path)).is_file()


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ints(block):
    return isinstance(block, dict) and all(type(block.get(k)) is int for k in ("cpu", "ram", "gpu", "parallel"))


def load_budget(path=None, deck=None):
    """The mode in effect and its budgets. A missing file, a bad file, or custom with no Deck is balanced."""
    data = _read_json(PERF if path is None else path)
    mode = data.get("mode")
    if mode == "custom":
        if (DECK if deck is None else Path(deck)).is_file() and _ints(data.get("custom")):
            c = data["custom"]
            return {"mode": "custom", "cpu": c["cpu"], "ram": c["ram"], "gpu": c["gpu"], "parallel": c["parallel"], "priority": "normal"}
        mode = "balanced"
    if mode not in PRESETS:
        mode = "balanced"
    return {**PRESETS[mode], "mode": mode}


def cpu_rate(budget, cores):
    """JOBOBJECT_CPU_RATE_CONTROL_INFORMATION.CpuRate. performance leaves one logical core, and never passes 90%."""
    if budget["mode"] == "performance" and cores and cores > 1:
        rate = min(9000, (cores - 1) * 10000 // cores)
    else:
        rate = int(budget["cpu"]) * 100
    return min(10000, max(1, rate))


def effective(budget, cores, ram_bytes):
    """Budgets actually applied. performance also keeps 2 GB of RAM free when that is tighter than 85%."""
    out = dict(budget)
    if budget["mode"] != "performance":
        return out
    out["cpu"] = cpu_rate(budget, cores) / 100
    if ram_bytes:
        cap = min(ram_bytes * 85 // 100, max(0, ram_bytes - RESERVE_RAM))
        out["ram"] = cap * 100 / ram_bytes
    return out


def should_wait(budget, shells, tree_bytes, ram_bytes):
    """True while another shell must not start. performance never waits. RAM waits only when the tree exceeds ram%."""
    if budget.get("mode") == "performance":
        return False
    if budget.get("parallel", 0) > 0 and shells >= budget["parallel"]:
        return True
    ram = budget.get("ram") or 0
    return bool(ram and ram_bytes and tree_bytes * 100 > ram_bytes * ram)


def find_ancestor(rows, pid, max_up=8):
    """Pid of the claude.exe (or node running claude-code) at most max_up parents up, or None."""
    by = {r["pid"]: r for r in rows}
    cur = pid
    for _ in range(max_up):
        row = by.get(cur)
        if not row:
            return None
        parent = by.get(row["ppid"])
        if not parent:
            return None
        if is_claude(parent):
            return parent["pid"]
        cur = parent["pid"]
    return None


def own_chain(rows, pid, ancestor):
    """This process and its parents up to, but not including, the chat. The hook's own shell is in here."""
    by = {r["pid"]: r for r in rows}
    skip, cur = set(), pid
    while cur and cur != ancestor:
        skip.add(cur)
        row = by.get(cur)
        if not row:
            break
        cur = row["ppid"]
    return skip


def busy_count(rows):
    """Pure: count shells busy from sampled rows of {pid, cpu_before, cpu_after, age_s}."""
    n = 0
    for r in rows:
        if (r["cpu_after"] - r["cpu_before"]) >= 0.01 or r["age_s"] < 5:
            n += 1
    return n


def shell_count(rows, ancestor, skip, parallel=0):
    candidates = [r for r in rows if r["ppid"] == ancestor and r["pid"] not in skip and norm_exe(r["name"]) in SHELLS]
    if parallel <= 0 or len(candidates) < parallel:
        return len(candidates)
    # sample each candidate's whole process tree CPU time twice 0.5s apart
    def tree_cpu(pid):
        if os.name != "nt":
            return None
        total = 0.0
        for p in descendants(rows, pid):
            h = _open(p, 0x1000)
            if not h:
                continue
            k = win()
            try:
                created, exit_t, kernel, user = _FT(), _FT(), _FT(), _FT()
                if k.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exit_t), ctypes.byref(kernel), ctypes.byref(user)):
                    kt = (kernel.dwHighDateTime << 32) | kernel.dwLowDateTime
                    ut = (user.dwHighDateTime << 32) | user.dwLowDateTime
                    total += (kt + ut) / 1e7
            finally:
                k.CloseHandle(h)
        return total
    before = [(r, tree_cpu(r["pid"]), age_of(r["pid"], r)) for r in candidates]
    time.sleep(0.5)
    sampled = []
    for r, cpu_b, age in before:
        cpu_a = tree_cpu(r["pid"]) if cpu_b is not None else None
        if cpu_b is None or cpu_a is None:
            # unavailable: treat as busy
            sampled.append({"pid": r["pid"], "cpu_before": 0.0, "cpu_after": 0.01, "age_s": age if age is not None else 0})
            continue
        sampled.append({"pid": r["pid"], "cpu_before": cpu_b, "cpu_after": cpu_a, "age_s": age if age is not None else 0})
    return busy_count(sampled)


def descendants(rows, root):
    by_ppid = {}
    for r in rows:
        by_ppid.setdefault(r["ppid"], []).append(r["pid"])
    out, stack, seen = [], [root], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        stack.extend(reversed(by_ppid.get(pid, [])))
    return out


def reaper_pids(rows):
    """Orphaned Claude Code shells and their descendants.

    A row matches when its exe is one of REAP_NAMES, its parent pid is not in the snapshot, and its command line
    contains a Claude Code marker. Descendants of a match are included even when their own parent is still alive.
    """
    alive = {r["pid"] for r in rows}
    matched = []
    for r in rows:
        if norm_exe(r["name"]) not in REAP_NAMES or r["ppid"] in alive:
            continue
        if any(m in (r.get("cmd") or "") for m in MARKERS):
            matched.append(r["pid"])
    out = []
    for pid in matched:
        out.extend(descendants(rows, pid))
    return list(dict.fromkeys(out))


def selftest():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        perf, deck = root / "performance.json", root / "settings.json"
        assert load_budget(perf, deck) == {**PRESETS["balanced"], "mode": "balanced"}
        perf.write_text("{", encoding="utf-8")
        assert load_budget(perf, deck)["mode"] == "balanced"
        perf.write_text(json.dumps({"mode": "nope"}), encoding="utf-8")
        assert load_budget(perf, deck)["mode"] == "balanced"
        perf.write_text(json.dumps({"mode": "light"}), encoding="utf-8")
        light = load_budget(perf, deck)
        assert light == {**PRESETS["light"], "mode": "light"}
        perf.write_text(json.dumps({"mode": "custom", "custom": {"cpu": 5, "ram": 6, "gpu": 7, "parallel": 2}}), encoding="utf-8")
        assert load_budget(perf, deck)["mode"] == "balanced"  # custom with no Deck file
        deck.write_text("{}", encoding="utf-8")
        custom = load_budget(perf, deck)
        assert custom["mode"] == "custom" and (custom["cpu"], custom["ram"], custom["gpu"], custom["parallel"]) == (5, 6, 7, 2)
        assert custom["priority"] == "normal"
        perf.write_text(json.dumps({"mode": "custom", "custom": {"cpu": True, "ram": 1, "gpu": 1, "parallel": 1}}), encoding="utf-8")
        assert load_budget(perf, deck)["mode"] == "balanced"
        perf.write_text(json.dumps({"mode": "performance"}), encoding="utf-8")
        full = load_budget(perf, deck)
        assert (full["cpu"], full["ram"], full["gpu"], full["parallel"]) == (90, 85, 90, 0)
        assert cpu_rate(full, 8) == 8750 and cpu_rate(full, 16) == 9000 and cpu_rate(full, 1) == 9000
        assert cpu_rate(light, 8) == 1000 and cpu_rate({**PRESETS["balanced"], "mode": "balanced"}, 2) == 4000
        eight = effective(full, 8, 8 * GIB)
        assert eight["cpu"] == 87.5 and abs(eight["ram"] - 75) < 0.05
        wide = effective(full, 16, 32 * GIB)
        assert wide["cpu"] == 90 and abs(wide["ram"] - 85) < 0.05
        assert effective(light, 8, 32 * GIB)["cpu"] == 10
    assert should_wait(light, 1, 0, 10000) and not should_wait(light, 0, 0, 10000)
    assert not should_wait(light, 0, 1000, 10000) and should_wait(light, 0, 1001, 10000)
    assert not should_wait(full, 50, 10 ** 15, 1000)
    balanced = {**PRESETS["balanced"], "mode": "balanced"}
    assert not should_wait(balanced, 2, 0, 10000) and should_wait(balanced, 3, 0, 10000)
    nolimit = {**balanced, "parallel": 0}
    assert not should_wait(nolimit, 9, 0, 10000)
    assert should_wait(nolimit, 0, 4001, 10000) and not should_wait(nolimit, 0, 4000, 10000)
    rows = [
        {"pid": 1, "ppid": 0, "name": "claude.exe", "cmd": ""},
        {"pid": 2, "ppid": 100, "name": "bash.exe", "cmd": r"C:\Users\a\.claude\shell-snapshots\x.sh"},
        {"pid": 3, "ppid": 2, "name": "git.exe", "cmd": "git status"},
        {"pid": 4, "ppid": 100, "name": "bash.exe", "cmd": "bash -lc echo hi"},
        {"pid": 5, "ppid": 1, "name": "node.exe", "cmd": "node claude-code"},
        {"pid": 6, "ppid": 100, "name": "notepad.exe", "cmd": r"C:\Users\a\.claude\x"},
        {"pid": 7, "ppid": 100, "name": "Python.EXE", "cmd": "set CLAUDE_CONFIG_DIR=x && python"},
        {"pid": 8, "ppid": 7, "name": "conhost.exe", "cmd": ""},
        {"pid": 9, "ppid": 4, "name": "git.exe", "cmd": "git status"},
    ]
    assert reaper_pids(rows) == [2, 3, 7, 8], reaper_pids(rows)
    assert find_ancestor([{"pid": 1, "ppid": 0, "name": "Claude.EXE", "cmd": ""}, {"pid": 2, "ppid": 1, "name": "python.exe", "cmd": ""}], 2) == 1
    assert find_ancestor([{"pid": 1, "ppid": 0, "name": "node.exe", "cmd": "/opt/@anthropic-ai/claude-code/cli.js"}, {"pid": 2, "ppid": 1, "name": "python.exe", "cmd": ""}], 2) == 1
    assert find_ancestor([{"pid": 1, "ppid": 0, "name": "node.exe", "cmd": "node app.js"}, {"pid": 2, "ppid": 1, "name": "python.exe", "cmd": ""}], 2) is None
    chain = [{"pid": 1, "ppid": 0, "name": "claude.exe"}] + [{"pid": i + 2, "ppid": i + 1, "name": f"x{i}.exe"} for i in range(9)]
    assert find_ancestor(chain, 2) == 1 and find_ancestor(chain, 11) is None
    tree = [{"pid": 1, "ppid": 0, "name": "claude.exe"}, {"pid": 2, "ppid": 1, "name": "bash.exe"}, {"pid": 3, "ppid": 2, "name": "python.exe"},
            {"pid": 4, "ppid": 1, "name": "bash.exe"}, {"pid": 5, "ppid": 1, "name": "node.exe"}]
    assert shell_count(tree, 1, own_chain(tree, 3, 1)) == 1
    assert busy_count([{"pid": 1, "cpu_before": 0.0, "cpu_after": 0.0, "age_s": 10.0}]) == 0
    assert busy_count([{"pid": 2, "cpu_before": 0.0, "cpu_after": 0.02, "age_s": 10.0}]) == 1
    assert busy_count([{"pid": 3, "cpu_before": 0.0, "cpu_after": 0.0, "age_s": 2.0}]) == 1


def log_once(msg):
    global _logged
    if _logged:
        return
    _logged = True
    print(msg, file=sys.stderr)


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fmt_pct(value):
    number = float(value)
    return f"{int(number)}%" if number == int(number) else f"{number:.1f}%"


def fmt_bytes(n):
    if not n:
        return "0 B"
    if n >= GIB:
        return f"{n / GIB:.1f} GB"
    return f"{n / 1024 ** 2:.1f} MB"


def fmt_age(seconds):
    if seconds is None:
        return "?"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _gpu_text(gpus, empty):
    if not gpus:
        return empty
    return ", ".join(f"{name} {fmt_bytes(size)}" for name, size in gpus)


# --- process table. Windows calls stay in the functions below; the decisions above never see them. ---

class _PE(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _CPU(ctypes.Structure):
    _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]


class _BASIC(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _MEM(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class _MSTAT(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _US(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]


class _FT(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


def win():
    """kernel32 / psapi / ntdll, bound once. Structures above match the sizes checked on Windows x64."""
    global _WIN
    if _WIN is not None:
        return _WIN
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, rest in (
        ("CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        ("Process32FirstW", [wintypes.HANDLE, ctypes.POINTER(_PE)], wintypes.BOOL),
        ("Process32NextW", [wintypes.HANDLE, ctypes.POINTER(_PE)], wintypes.BOOL),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        ("GetCurrentProcess", [], wintypes.HANDLE),
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("GetExitCodeProcess", [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ("GetProcessTimes", [wintypes.HANDLE, ctypes.POINTER(_FT), ctypes.POINTER(_FT), ctypes.POINTER(_FT), ctypes.POINTER(_FT)], wintypes.BOOL),
        ("GlobalMemoryStatusEx", [ctypes.POINTER(_MSTAT)], wintypes.BOOL),
    ):
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, rest
    ps = ctypes.WinDLL("psapi", use_last_error=True)
    ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MEM), wintypes.DWORD]
    ps.GetProcessMemoryInfo.restype = wintypes.BOOL
    nt = ctypes.WinDLL("ntdll", use_last_error=True)
    nt.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    nt.NtQueryInformationProcess.restype = ctypes.c_long
    k.psapi, k.ntdll = ps, nt
    _WIN = k
    return k


def process_rows():
    if os.name == "nt":
        return _win_rows()
    if sys.platform.startswith("linux"):
        return _linux_rows()
    if sys.platform == "darwin":
        return _darwin_rows()
    return []


def _win_rows():
    k = win()
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    pe, rows = _PE(), []
    pe.dwSize = ctypes.sizeof(_PE)
    try:
        ok = k.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            rows.append({"pid": pe.th32ProcessID, "ppid": pe.th32ParentProcessID, "name": pe.szExeFile})
            ok = k.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k.CloseHandle(snap)
    return rows


def _linux_rows():
    rows = []
    for ent in os.listdir("/proc"):
        if not ent.isdigit():
            continue
        try:
            raw = Path("/proc", ent, "stat").read_bytes()
        except OSError:
            continue
        rpar = raw.rfind(b")")
        rest = raw[rpar + 2:].split()
        if rpar < 0 or len(rest) < 2:
            continue
        try:
            ppid = int(rest[1])
        except ValueError:
            continue
        rows.append({"pid": int(ent), "ppid": ppid, "name": raw[raw.find(b"(") + 1:rpar].decode("utf-8", "replace")})
    return rows


def _darwin_rows():
    try:
        out = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,rss=,etime=,comm="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid, ppid, rss = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        rows.append({"pid": pid, "ppid": ppid, "name": Path(parts[4]).name, "ws": rss * 1024, "age": _etime(parts[3])})
    return rows


def _etime(text):
    try:
        days, _, rest = text.partition("-")
        day_n = int(days) if rest else 0
        clock = rest or days
        parts = [int(p) for p in clock.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        sec = parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        sec = parts[0] * 60 + parts[1]
    elif len(parts) == 1:
        sec = parts[0]
    else:
        return None
    return day_n * 86400 + sec


def _open(pid, access):
    return win().OpenProcess(access, False, pid)


def working_set(pid, row=None):
    if row and row.get("ws") is not None:
        return row["ws"]
    if os.name == "nt":
        return _win_times(pid)[0]
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc", str(pid), "status").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return 0
    return 0


def age_of(pid, row=None):
    if row and row.get("age") is not None:
        return row["age"]
    if os.name == "nt":
        return _win_times(pid)[1]
    if sys.platform.startswith("linux"):
        try:
            return max(0, time.time() - Path("/proc", str(pid)).stat().st_mtime)
        except OSError:
            return None
    return None


def _win_times(pid):
    """(working set bytes, age seconds or None). One open for both."""
    # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ. GetProcessMemoryInfo accepts the limited right.
    h = _open(pid, 0x1000 | 0x0010)
    if not h:
        return 0, None
    k = win()
    try:
        mem = _MEM()
        mem.cb = ctypes.sizeof(_MEM)
        ws = mem.WorkingSetSize if k.psapi.GetProcessMemoryInfo(h, ctypes.byref(mem), mem.cb) else 0
        created, exit_t, kernel, user = _FT(), _FT(), _FT(), _FT()
        age = None
        if k.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exit_t), ctypes.byref(kernel), ctypes.byref(user)):
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            age = max(0, time.time() - (ticks / 10000000 - 11644473600))
        return ws, age
    finally:
        k.CloseHandle(h)


def cmdline(pid):
    if os.name == "nt":
        return _win_cmd(pid)
    if sys.platform.startswith("linux"):
        try:
            return Path("/proc", str(pid), "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except OSError:
            return ""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.stdout or "").strip()


def _win_cmd(pid):
    # ProcessCommandLineInformation (60) needs only PROCESS_QUERY_LIMITED_INFORMATION, since Windows 8.1.
    h = _open(pid, 0x1000)
    if not h:
        return ""
    k = win()
    try:
        size = ctypes.c_ulong(0)
        k.ntdll.NtQueryInformationProcess(h, 60, None, 0, ctypes.byref(size))
        if not size.value or size.value > 1_000_000:
            return ""
        buf = ctypes.create_string_buffer(size.value)
        status = k.ntdll.NtQueryInformationProcess(h, 60, buf, size.value, ctypes.byref(size))
        if status < 0:
            return ""
        us = _US.from_buffer(buf)
        n = us.Length // 2
        if not us.Buffer or n <= 0 or n > 32768:
            return ""
        return ctypes.wstring_at(us.Buffer, n)
    except (ValueError, OSError):
        return ""
    finally:
        k.CloseHandle(h)


def physical_ram():
    if os.name == "nt":
        st = _MSTAT()
        st.dwLength = ctypes.sizeof(_MSTAT)
        return st.ullTotalPhys if win().GlobalMemoryStatusEx(ctypes.byref(st)) else 0
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2)
            return int(out.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return 0
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError):
        return 0


def dedicated_gpus():
    """(name, dedicated bytes) for each GPU with at least 1 GiB of dedicated memory. Integrated graphics are left out."""
    if os.name == "nt":
        found = _gpus_registry()
        return _gpus_cim() if found is None else found
    return _gpus_posix()


def _qword(key, name):
    import winreg
    try:
        val, typ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if typ in (winreg.REG_QWORD, winreg.REG_DWORD):
        return int(val)
    if typ == winreg.REG_BINARY and isinstance(val, bytes) and len(val) >= 8:
        return int.from_bytes(val[:8], "little")
    return None


def _gpus_registry():
    """None when the display class cannot be opened, so the caller can fall back to CIM. qwMemorySize is dedicated bytes."""
    import winreg
    path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return None
    out, i = [], 0
    try:
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, sub) as key:
                    try:
                        name, _ = winreg.QueryValueEx(key, "DriverDesc")
                    except OSError:
                        continue
                    size = _qword(key, "HardwareInformation.qwMemorySize")
            except OSError:
                continue
            if isinstance(name, str) and size is not None and size >= GIB:
                out.append((name.strip(), size))
    finally:
        root.Close()
    return out


def _gpus_cim():
    """wmic, then Get-CimInstance. AdapterRAM is a uint32 and counts shared memory, so integrated names are dropped."""
    text = ""
    try:
        r = subprocess.run(["wmic", "path", "Win32_VideoController", "get", "Name,AdapterRAM", "/format:csv"], capture_output=True, text=True, timeout=15)
        text = r.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    rows = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = next((i for i, ln in enumerate(lines) if "AdapterRAM" in ln), None)
    if header is not None:
        cols = [c.strip() for c in lines[header].split(",")]
        try:
            ni, ri = cols.index("Name"), cols.index("AdapterRAM")
        except ValueError:
            ni = ri = None
        if ni is not None:
            for ln in lines[header + 1:]:
                parts = [p.strip() for p in ln.split(",")]
                if len(parts) > max(ni, ri) and parts[ri].isdigit():
                    rows.append((parts[ni], int(parts[ri])))
    if not rows:
        script = "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + '|' + $_.AdapterRAM }"
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return []
        for ln in (r.stdout or "").splitlines():
            name, sep, ram = ln.rpartition("|")
            if sep and ram.strip().isdigit():
                rows.append((name.strip(), int(ram.strip())))
    out = []
    for name, ram in rows:
        low = name.lower()
        integrated = "intel" in low or "microsoft" in low or "basic display" in low or low in ("amd radeon(tm) graphics", "amd radeon graphics")
        if ram >= GIB and name and not integrated:
            out.append((name, ram))
    return out


def _gpus_posix():
    out = []
    drm = Path("/sys/class/drm")
    if drm.is_dir():
        for mem in drm.glob("card[0-9]/device/mem_info_vram_total"):
            try:
                size = int(mem.read_text().strip())
            except (OSError, ValueError):
                continue
            if size < GIB:
                continue
            name = mem.parents[1].name
            product = mem.parent / "product_name"
            try:
                name = product.read_text(encoding="utf-8", errors="replace").strip() or name
            except OSError:
                pass
            out.append((name, size))
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r and r.returncode == 0:
        for ln in r.stdout.splitlines():
            name, sep, mem = ln.rpartition(",")
            if sep and mem.strip().isdigit() and int(mem.strip()) >= 1024:
                out.append((name.strip(), int(mem.strip()) * 1024 ** 2))
    return out


def _public_name(name):
    name = " ".join((name or "").split())
    if not name or ":\\" in name or name.startswith("/") or name.startswith("~"):
        return ""
    return name


def _read_cap(path, limit=1_000_000):
    try:
        return Path(path).read_bytes()[:limit].decode("utf-8", "replace")
    except OSError:
        return ""


def acf_name(text):
    m = re.search(r'"name"\s+"((?:\\.|[^"\\])*)"', text)
    return _public_name(m.group(1).replace("\\\\", "\\").replace('\\"', '"')) if m else ""


def installed_games():
    """Game names from a Steam libraryfolders.vdf and from Epic manifests. Names only."""
    names = []
    for vdf in _steam_vdfs():
        text = _read_cap(vdf)
        for raw in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"', text):
            root = Path(raw.replace("\\\\", "\\").replace('\\"', '"'))
            folder = root if root.name.lower() == "steamapps" else root / "steamapps"
            if not folder.is_dir():
                continue
            for mf in folder.glob("appmanifest_*.acf"):
                if name := acf_name(_read_cap(mf)):
                    names.append(name)
    for folder in _epic_dirs():
        if not folder.is_dir():
            continue
        for item in folder.glob("*.item"):
            try:
                data = json.loads(_read_cap(item))
            except ValueError:
                continue
            if isinstance(data, dict) and (name := _public_name(data.get("DisplayName") if isinstance(data.get("DisplayName"), str) else "")):
                names.append(name)
    return sorted(set(names), key=str.lower)


def _steam_vdfs():
    found = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam, _ = winreg.QueryValueEx(key, "SteamPath")
                found.append(Path(steam) / "steamapps" / "libraryfolders.vdf")
        except OSError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                found.append(Path(os.environ[env]) / "Steam" / "steamapps" / "libraryfolders.vdf")
    found.append(HOME / ".steam" / "steam" / "steamapps" / "libraryfolders.vdf")
    found.append(HOME / ".local" / "share" / "Steam" / "steamapps" / "libraryfolders.vdf")
    found.append(HOME / "Library" / "Application Support" / "Steam" / "steamapps" / "libraryfolders.vdf")
    seen, out = set(), []
    for path in found:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _epic_dirs():
    dirs = []
    if os.environ.get("ProgramData"):
        dirs.append(Path(os.environ["ProgramData"]) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests")
    dirs.append(HOME / "Library" / "Application Support" / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests")
    return dirs


def ancestor_pid(rows, pid):
    """find_ancestor after filling command lines for node parents. At most 8 levels."""
    by = {r["pid"]: r for r in rows}
    cur = pid
    # fill node command lines lazily as we walk
    for _ in range(8):
        row = by.get(cur)
        if not row:
            return None
        parent = by.get(row["ppid"])
        if not parent:
            return None
        if norm_exe(parent["name"]) == "node" and "cmd" not in parent:
            parent["cmd"] = cmdline(parent["pid"])
        if is_claude(parent):
            return parent["pid"]
        cur = parent["pid"]
    return None


def pid_alive(pid):
    """False only when a complete snapshot shows the pid gone. A failed snapshot must not look like a dead chat."""
    rows = process_rows()
    ids = {r["pid"] for r in rows}
    if os.getpid() not in ids:
        return True
    return pid in ids


def _sample(pid):
    rows = process_rows()
    anc = ancestor_pid(rows, pid)
    if not anc:
        return 0, 0
    tree = sum(working_set(p) for p in descendants(rows, anc))
    budget_parallel = load_budget().get("parallel", 0)
    return shell_count(rows, anc, own_chain(rows, pid, anc), budget_parallel), tree


def hook_gate():
    try:
        return _hook_gate()
    except Exception as e:
        log_once(f"frugal: performance gate skipped ({type(e).__name__})")
        return ""


def _hook_gate():
    start, waited = time.monotonic(), False
    while True:
        budget = load_budget()
        if budget["mode"] == "performance" or not should_wait(budget, *_sample(os.getpid()), physical_ram()):
            break
        if time.monotonic() - start >= 600:
            break
        waited = True
        time.sleep(min(2, 600 - (time.monotonic() - start)))
    if not waited:
        return ""
    line = f"frugal: waited {int(time.monotonic() - start)} s for free resources (mode {load_budget()['mode']})"
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": line}})


def _budget_line(budget):
    pri = "below normal" if budget["priority"] == "below_normal" else "normal"
    return f"cpu: {fmt_pct(budget['cpu'])}  ram: {fmt_pct(budget['ram'])}  gpu: {fmt_pct(budget['gpu'])}  parallel: {int(budget['parallel'])}  priority: {pri}"


def status_text():
    budget = load_budget()
    cores, ram = os.cpu_count() or 0, physical_ram()
    lines = [f"mode: {budget['mode']}", _budget_line(effective(budget, cores, ram)),
             f"machine: {cores} cores, {fmt_bytes(ram)} RAM, {_gpu_text(dedicated_gpus(), 'no dedicated GPU')}"]
    rows = process_rows()
    anc = ancestor_pid(rows, os.getpid())
    if not anc:
        lines.append("chat: none")
        return "\n".join(lines)
    by = {r["pid"]: r for r in rows}
    shown = []
    for pid in descendants(rows, anc):
        row = by.get(pid)
        if not row:
            continue
        if os.name == "nt":
            ws, age = _win_times(pid)
        else:
            ws, age = working_set(pid, row), age_of(pid, row)
        shown.append((ws, pid, row["name"], age))
    shown.sort(key=lambda item: (-item[0], item[1]))
    lines.append("chat:")
    lines.extend(f"  {pid} {name} {ws / 1024 ** 2:.1f} MB {fmt_age(age)}" for ws, pid, name, age in shown)
    return "\n".join(lines)


def profile_text():
    cores, ram = os.cpu_count() or 0, physical_ram()
    scored = []
    for row in process_rows():
        scored.append((working_set(row["pid"], row), norm_exe(row["name"]) or row["name"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    names = list(dict.fromkeys(name for _, name in scored))[:15]  # one entry per program, biggest first
    games = [g for g in installed_games() if "Redistributable" not in g and "Authoring Tools" not in g]
    return "\n".join([
        f"cores: {cores}",
        f"ram: {fmt_bytes(ram)}",
        f"gpu: {_gpu_text(dedicated_gpus(), 'none')}",
        "processes: " + (", ".join(names) if names else "none"),
        "games: " + (", ".join(games) if games else "none"),
    ])


def set_mode(mode):
    if mode not in MODES:
        raise SystemExit("mode is light, balanced, performance or custom")
    if mode == "custom" and not deck_installed():
        raise SystemExit("custom is a Frugal Deck feature")
    cur = _read_json(PERF)
    data = {"mode": mode}
    if isinstance(cur.get("custom"), dict):
        data["custom"] = cur["custom"]
    _write(PERF, data)
    return f"mode: {mode}"


def _stragglers():
    rows = process_rows()
    alive = {r["pid"] for r in rows}
    for row in rows:
        if norm_exe(row["name"]) in REAP_NAMES and row["ppid"] not in alive:
            row["cmd"] = cmdline(row["pid"])
    return reaper_pids(rows), rows


def _format_stragglers(pids, rows):
    if not pids:
        return "none"
    by = {r["pid"]: r for r in rows}
    lines = []
    for pid in pids:
        row = by.get(pid, {})
        cmd = " ".join((row.get("cmd") or "").split())
        if len(cmd) > 160:
            cmd = cmd[:160] + "..."
        lines.append(f"{pid} {row.get('name') or '?'}" + (f" {cmd}" if cmd else ""))
    return "\n".join(lines)


def _terminate(pid):
    if pid in (0, 1, os.getpid()):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def reap(kill):
    pids, rows = _stragglers()
    text = _format_stragglers(pids, rows)
    if not kill or not pids:
        return text
    killed, failed = [], []
    for pid in reversed(pids):
        (killed if _terminate(pid) else failed).append(pid)
    line = f"killed {len(killed)}"
    if failed:
        line += " failed " + " ".join(str(pid) for pid in failed)
    return text + "\n" + line


def apply_limits(job, budget):
    """CPU hard cap, and below-normal priority for light. Never sets KILL_ON_JOB_CLOSE."""
    k = win()
    info = _CPU(CPU_RATE_ENABLE | CPU_RATE_HARD_CAP, cpu_rate(budget, os.cpu_count() or 1))
    ok = k.SetInformationJobObject(job, JOB_CPU_RATE, ctypes.byref(info), ctypes.sizeof(info))
    basic = _BASIC()
    if budget["priority"] == "below_normal":
        basic.LimitFlags = JOB_LIMIT_PRIORITY
        basic.PriorityClass = BELOW_NORMAL
    ok = bool(k.SetInformationJobObject(job, JOB_BASIC_LIMIT, ctypes.byref(basic), ctypes.sizeof(basic))) and bool(ok)
    if not ok:
        log_once(f"frugal: performance limits skipped ({ctypes.get_last_error()})")
    return ok


def _assign(job, pid):
    k = win()
    if pid == os.getpid():
        ok = k.AssignProcessToJobObject(job, k.GetCurrentProcess())
        return bool(ok), ctypes.get_last_error()
    # PROCESS_SET_QUOTA | PROCESS_TERMINATE, the rights AssignProcessToJobObject requires.
    h = _open(pid, 0x0100 | 0x0001)
    if not h:
        return False, ctypes.get_last_error()
    try:
        ok = k.AssignProcessToJobObject(job, h)
        return bool(ok), ctypes.get_last_error()
    finally:
        k.CloseHandle(h)


class Hold:
    def __init__(self, ancestor, job=None):
        self.ancestor, self.job = ancestor, job
        self.stop = threading.Event()
        self.descendants = []
        self.nice = None
        self.thread = threading.Thread(target=self._run, name="frugal-perf", daemon=True)
        self.thread.start()

    def _signature(self, budget):
        return budget["mode"], budget["cpu"], budget["ram"], budget["gpu"], budget["parallel"], budget["priority"], cpu_rate(budget, os.cpu_count() or 1)

    def _run(self):
        budget = load_budget()
        sig = self._signature(budget)
        while not self.stop.wait(30):
            try:
                budget = load_budget()
                new = self._signature(budget)
                if new != sig:
                    if self.job:
                        apply_limits(self.job, budget)
                    else:
                        self.nice = _renice(self.ancestor, budget, self.nice)
                    sig = new
                if not self.job:
                    self.descendants = _recorded(self.ancestor)
            except Exception:
                log_once("frugal: performance limits not updated")


def _renice(pid, budget, applied):
    """light -> nice 10. Any other mode restores 0 once the chat had been niced. Nothing else on macOS/Linux."""
    want = 10 if budget["mode"] == "light" else 0
    if applied == want or (applied is None and want == 0):
        return applied
    try:
        os.setpriority(os.PRIO_PROCESS, pid, want)
    except (OSError, AttributeError):
        log_once("frugal: renice skipped")
        return applied
    return want


def _recorded(ancestor):
    rows = process_rows()
    return [pid for pid in descendants(rows, ancestor) if pid not in (ancestor, os.getpid(), 0, 1)]


def guard(deck_env):
    """Limit this chat when it is not already managed by Frugal Deck. None when there is nothing to do."""
    if deck_env:
        return None
    try:
        rows = process_rows()
        anc = ancestor_pid(rows, os.getpid())
        if not anc:
            return None
        if os.name != "nt":
            hold = Hold(anc)
            hold.descendants = _recorded(anc)
            hold.nice = _renice(anc, load_budget(), None)
            return hold
        k = win()
        job = k.CreateJobObjectW(None, None)
        if not job:
            log_once(f"frugal: performance job skipped ({ctypes.get_last_error()})")
            return None
        apply_limits(job, load_budget())
        ok, err = _assign(job, anc)
        if not ok:
            k.CloseHandle(job)
            log_once(f"frugal: performance job skipped ({err})")
            return None
        for pid in descendants(rows, anc):
            if pid != anc:
                _assign(job, pid)  # a child that is already in some other job is left out; future children join this one
        return Hold(anc, job)
    except Exception:
        log_once("frugal: performance job skipped")
        return None


def release(hold):
    """On stdin EOF: if the chat is gone, kill what it left. Closing the job alone must not kill a live chat."""
    if not hold:
        return
    hold.stop.set()
    hold.thread.join(5)
    try:
        if pid_alive(hold.ancestor):
            return
        if hold.job:
            win().TerminateJobObject(hold.job, 1)
            return
        live = {r["pid"] for r in process_rows()}
        pids = set(hold.descendants)
        pids.update(pid for pid in descendants(process_rows(), hold.ancestor) if pid != hold.ancestor)
        for pid in pids:
            if pid in live:
                _terminate(pid)
    except Exception:
        log_once("frugal: performance reap skipped")
    finally:
        if hold.job:
            win().CloseHandle(hold.job)


def main(args):
    if args == ["status"]:
        return status_text()
    if args == ["profile"]:
        return profile_text()
    if len(args) == 2 and args[0] == "set":
        return set_mode(args[1])
    if args in (["reap"], ["reap", "--kill"]):
        return reap(kill=args[-1] == "--kill")
    raise SystemExit("perf status | set <mode> | profile | reap [--kill]")
