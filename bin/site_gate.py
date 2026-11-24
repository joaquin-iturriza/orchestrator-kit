#!/usr/bin/env python3
"""The site-side submission gate's GPU budget.

Every sbatch / condor_submit a `site run` or `site submit` command makes on a site reaches the scheduler through the
wrapper `site` writes to ~/.ccorch/bin (SCHED_WRAP): the wrapper refuses a submission that names no work item, then
execs this file, so launch scripts, sweep_manager, generators and `site submit` all pass here.

    python3 ~/.ccorch/bin/ccorch_gate.py <sbatch|condor_submit|condor_submit_dag> <its arguments...>

It reads the job's GPU-hours, wall time x GPUs x number of jobs (array tasks, condor queue count), from the command
line, the scheduler's environment input variables and the script's #SBATCH lines (or the submit file), and sums them
with the GPU-hours of the named items' jobs submitted in the last 60 min: on this site (~/.ccorch/gpu_window.jsonl,
written here under a lock) and on the other sites (CCORCH_WORK's budget.prior, from the laptop's ledger table
gpu_window). Above 30 GPU-h (budget.cap), or above 10 on a scarce site (Jean Zay, budget.cap_scarce), it refuses unless
the items' recorded estimate (items.est_gpu_hours, budget.est) covers the total. Otherwise it runs the real command,
stdout and stderr untouched, and on success records the submission here and in $CCORCH_GATE_LOG (read back by the
laptop). A job whose GPU request cannot be read is refused when anything in it mentions a GPU, else counted as 0.
Python 3.6 compatible (the sites' system python)."""
import fcntl, fnmatch, glob, json, os, re, shutil, subprocess, sys, time

WINDOW_S = 3600
DEFAULT_CAP, DEFAULT_CAP_SCARCE = 30.0, 10.0
SLURM_DEFAULT_H = 24.0                  # a GPU job with no --time: counted at a day (no site default is assumed)
CONDOR_FLAVOUR_H = {"espresso": 1 / 3.0, "microcentury": 1.0, "longlunch": 2.0, "workday": 8.0, "tomorrow": 24.0,
                    "testmatch": 72.0, "nextweek": 168.0}
CONDOR_DEFAULT_H = 1 / 3.0              # CERN's default flavour, espresso (20 min)
# sbatch options that take no value (everything else long takes one, as `--opt value` or `--opt=value`)
SB_FLAGS = {"--parsable", "--test-only", "--wait", "-W", "--hold", "-H", "--exclusive", "--requeue", "--no-requeue",
            "--quiet", "-Q", "--verbose", "-v", "--overcommit", "-O", "--contiguous", "--spread-job",
            "--use-min-nodes", "--ignore-pbs", "--usage", "--help", "-h", "--version", "-V", "--no-kill", "-k",
            "--get-user-env", "--oversubscribe", "-s", "--nice", "--x11", "--reboot"}
SB_SHORT = {"-t": "--time", "-a": "--array", "-G": "--gpus", "-N": "--nodes", "-n": "--ntasks", "-p": "--partition",
            "-J": "--job-name", "-A": "--account", "-c": "--cpus-per-task", "-o": "--output", "-e": "--error",
            "-q": "--qos", "-C": "--constraint", "-d": "--dependency", "-D": "--chdir", "-w": "--nodelist",
            "-x": "--exclude", "-L": "--licenses", "-M": "--clusters", "-i": "--input", "-b": "--begin",
            "-F": "--nodefile", "-m": "--distribution", "-S": "--core-spec"}
SB_ENV = {"SBATCH_TIMELIMIT": "--time", "SBATCH_GPUS": "--gpus", "SBATCH_GRES": "--gres",
          "SBATCH_GPUS_PER_NODE": "--gpus-per-node", "SBATCH_GPUS_PER_TASK": "--gpus-per-task",
          "SBATCH_TRES_PER_TASK": "--tres-per-task", "SBATCH_TRES_PER_NODE": "--tres-per-node",
          "SBATCH_GPUS_PER_SOCKET": "--gpus-per-socket", "SBATCH_ARRAY_INX": "--array"}


class Unreadable(Exception):
    pass


# ------------------------------------------------------------------ slurm
def slurm_hours(t):
    """[days-]hours[:minutes[:seconds]] or minutes[:seconds]... (sbatch --time), in hours."""
    t = (t or "").strip()
    if not t or t.upper() in ("UNLIMITED", "INFINITE"):
        raise Unreadable("--time %r" % t)
    days = 0
    if "-" in t:
        d, t = t.split("-", 1)
        days = int(d)
        parts = [int(x) for x in t.split(":")]
        h, m, s = (parts + [0, 0])[:3]
        return days * 24 + h + m / 60.0 + s / 3600.0
    parts = [int(x) for x in t.split(":")]
    if len(parts) == 1:
        return parts[0] / 60.0
    if len(parts) == 2:
        return parts[0] / 60.0 + parts[1] / 3600.0
    return parts[0] + parts[1] / 60.0 + parts[2] / 3600.0


def array_count(spec):
    spec = (spec or "").split("%")[0]
    n = 0
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if ":" in part:
            part, st = part.split(":", 1)
            step = int(st)
        if "-" in part:
            a, b = part.split("-", 1)
            n += len(range(int(a), int(b) + 1, step))
        else:
            int(part)
            n += 1
    if n <= 0:
        raise Unreadable("--array %r" % spec)
    return n


def _gpu_n(spec):
    """gpu[:type]:N or [type:]N -> N; a bare gpu or type -> 1."""
    tail = spec.split("(")[0].split(":")[-1]
    return int(tail) if tail.isdigit() else 1


def _tres_gpus(spec):
    """gres/gpu[:type]:N entries of a --tres-per-* value -> N (0 when it names no GPU)."""
    n = 0
    for part in spec.split(","):
        part = part.strip()
        if part.startswith("gres/gpu") or part.startswith("gpu"):
            n += _gpu_n(part.split("/", 1)[-1])
    return n


def _max_int(v):
    return max(int(x) for x in re.split(r"[-,]", v) if x.strip().isdigit())


def sbatch_options(args):
    """(options dict long-name -> value, script path or None, wrap?) from the sbatch command line."""
    opts, i = {}, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        if a.startswith("--"):
            k, eq, v = a.partition("=")
            if not eq and k not in SB_FLAGS:
                v = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            opts[k] = v if (eq or k not in SB_FLAGS) else True
        else:
            k = a[:2]
            if k in SB_FLAGS:
                opts[k] = True
            else:
                v = a[2:]
                if v.startswith("="):
                    v = v[1:]
                if not v:
                    v = args[i + 1] if i + 1 < len(args) else ""
                    i += 1
                opts[SB_SHORT.get(k, k)] = v
        i += 1
    script = args[i] if i < len(args) and "--wrap" not in opts else None
    return opts, script


def script_directives(text):
    """#SBATCH options of a batch script, up to its first command line (sbatch's own rule)."""
    out = []
    for line in text.splitlines()[1:] if text.startswith("#!") else text.splitlines():
        s = line.strip()
        if not s or (s.startswith("#") and not s.startswith("#SBATCH")):
            continue
        if not s.startswith("#SBATCH"):
            break
        out += s[len("#SBATCH"):].split("#")[0].split()
    return sbatch_options(out)[0]


def sbatch_gpu_hours(args, env, stdin_text=None):
    """(GPU-h, description) of one sbatch call."""
    opts, script = sbatch_options(args)
    text = stdin_text
    if script and text is None:
        try:
            with open(script) as f:
                text = f.read(200000)
        except (OSError, UnicodeDecodeError):
            text = ""
    o = dict(script_directives(text or ""))
    for k, v in SB_ENV.items():
        if env.get(k):
            o[v] = env[k]
    o.update(opts)
    if o.get("--test-only"):
        return 0.0, "sbatch --test-only: no job"
    nodes = _max_int(o["--nodes"]) if o.get("--nodes") else 1
    ntasks = int(o["--ntasks"]) if str(o.get("--ntasks", "")).isdigit() else 1
    gpus = 0
    if o.get("--gpus"):
        gpus = _gpu_n(o["--gpus"])
    elif o.get("--gpus-per-node"):
        gpus = _gpu_n(o["--gpus-per-node"]) * nodes
    elif o.get("--gpus-per-task"):
        gpus = _gpu_n(o["--gpus-per-task"]) * ntasks
    elif o.get("--gres"):
        gpus = sum(_gpu_n(g) for g in o["--gres"].split(",") if g.strip().startswith("gpu")) * nodes
    elif o.get("--tres-per-task"):
        gpus = _tres_gpus(o["--tres-per-task"]) * ntasks
    elif o.get("--tres-per-node"):
        gpus = _tres_gpus(o["--tres-per-node"]) * nodes
    if not gpus:
        # a GPU asked in a form not read here (--gpus-per-socket, an unknown option): never counted as none
        if any(re.search(r"gpu|tres", "%s %s" % (k, v), re.I) for k, v in o.items()):
            raise Unreadable("a GPU request in a form the gate does not count: %s" % ", ".join(
                "%s=%s" % (k, v) for k, v in sorted(o.items()) if re.search(r"gpu|tres", "%s %s" % (k, v), re.I)))
        return 0.0, "no GPU asked"
    hours = slurm_hours(o["--time"]) if o.get("--time") else SLURM_DEFAULT_H
    n = array_count(o["--array"]) if o.get("--array") else 1
    return gpus * hours * n, "%d job%s x %d GPU x %.2f h%s" % (n, "s" if n != 1 else "", gpus, hours,
                                                            "" if o.get("--time") else " (no --time: a day counted)")


# ------------------------------------------------------------------ condor
def condor_queue_count(stmt, sub_dir):
    """jobs of one `queue` statement."""
    s = stmt.strip()[len("queue"):].strip()
    m = re.match(r"^(\d+)\s*(.*)$", s, re.S)
    n, rest = (int(m.group(1)), m.group(2)) if m else (1, s)
    if not rest:
        return n
    m = re.search(r"\bin\s*\((.*)\)\s*$", rest, re.S) or re.search(r"\bin\s+(.*)$", rest, re.S)
    if m:
        return n * len([x for x in re.split(r"[,\s]+", m.group(1)) if x])
    m = re.search(r"\bmatching\s+(?:files\s+|dirs\s+)?(.*)$", rest, re.S)
    if m:
        return n * sum(len(glob.glob(os.path.join(sub_dir, p))) for p in m.group(1).split())
    m = re.search(r"\bfrom\s*\((.*)\)\s*$", rest, re.S)
    if m:
        return n * len([x for x in m.group(1).splitlines() if x.strip() and not x.strip().startswith("#")])
    m = re.search(r"\bfrom\s+(\S+)\s*$", rest)
    if m:
        p = os.path.join(sub_dir, m.group(1))
        with open(p) as f:
            return n * len([x for x in f if x.strip() and not x.strip().startswith("#")])
    raise Unreadable("queue statement %r" % stmt.strip()[:80])


def condor_gpu_hours(args, stdin_text=None):
    opts, files, assigns, i = {}, [], [], 0
    takes = {"-append", "-a", "-name", "-pool", "-batch-name", "-queue", "-q", "-addr", "-remote", "-dry-run",
             "-maxjobs", "-capability", "-factory", "-file", "-batch-id", "-i"}
    while i < len(args):
        a = args[i]
        if a.lower() in takes:
            opts.setdefault(a.lower(), []).append(args[i + 1] if i + 1 < len(args) else "")
            i += 2
            continue
        if a.startswith("-"):
            opts.setdefault(a.lower(), []).append(True)
        elif "=" in a and not os.path.exists(a):
            assigns.append(a)
        else:
            files.append(a)
        i += 1
    if "-dry-run" in opts:
        return 0.0, "condor_submit -dry-run: no job"
    sub_dir = os.getcwd()
    if files:
        with open(files[0]) as f:
            text = f.read(200000)
        sub_dir = os.path.dirname(os.path.abspath(files[0]))
    else:
        text = stdin_text or ""
    # logical lines (a trailing backslash continues), the -append lines before the queue statements
    lines = re.sub(r"\\\n", " ", text).splitlines()
    q = [l for l in lines if re.match(r"^\s*queue\b", l, re.I)]
    body = [l for l in lines if not re.match(r"^\s*queue\b", l, re.I)] + list(opts.get("-append", []) +
                                                                                opts.get("-a", [])) + assigns
    # a multi-line `queue ... from (` block: its lines up to `)` are queue items, not commands
    if any(re.search(r"\bfrom\s*\(\s*$", l) for l in q):
        txt, q = "\n".join(lines), []
        for m in re.finditer(r"^\s*queue\b[^\n]*?\bfrom\s*\(.*?^\s*\)", txt, re.M | re.S | re.I):
            q.append(m.group(0))
        body = [l for l in re.sub(r"^\s*queue\b[^\n]*?\bfrom\s*\(.*?^\s*\)", "", txt, flags=re.M | re.S | re.I)
                .splitlines() if not re.match(r"^\s*queue\b", l, re.I)]
        q += [l for l in lines if re.match(r"^\s*queue\b", l, re.I) and "(" not in l]
    kv = {}
    for l in body:
        m = re.match(r"^\s*(\+?[\w.]+)\s*=\s*(.*?)\s*$", l)
        if m:
            kv[m.group(1).lower()] = m.group(2).strip().strip('"')
    if opts.get("-queue") or opts.get("-q"):
        q = ["queue " + (opts.get("-queue") or opts.get("-q"))[-1]]
    gpus = int(kv["request_gpus"]) if kv.get("request_gpus", "").isdigit() else (1 if kv.get("request_gpus") else 0)
    if not gpus:
        return 0.0, "no GPU asked"
    if "+maxruntime" in kv and not kv["+maxruntime"].isdigit():
        raise Unreadable("+MaxRuntime %r" % kv["+maxruntime"])
    if kv.get("+maxruntime"):
        hours = int(kv["+maxruntime"]) / 3600.0
    elif kv.get("+jobflavour"):
        f = kv["+jobflavour"].strip('"').lower()
        if f not in CONDOR_FLAVOUR_H:
            raise Unreadable("+JobFlavour %r" % f)
        hours = CONDOR_FLAVOUR_H[f]
    else:
        hours = CONDOR_DEFAULT_H
    n = sum(condor_queue_count(s, sub_dir) for s in q) if q else 0
    return gpus * hours * n, "%d job%s x %d GPU x %.2f h" % (n, "s" if n != 1 else "", gpus, hours)


def dag_gpu_hours(args, depth=0):
    """a DAG's GPU-h: the sum over its JOB lines' submit files (SUBDAG EXTERNAL read the same way), each node once;
    a DAG or node file that cannot be read raises."""
    files = [a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or not args[i - 1].startswith("-"))
             and os.path.isfile(a)]
    if not files or depth > 5:
        raise Unreadable("no DAG file among %r" % (args,))
    total, n_nodes = 0.0, 0
    for dag in files:
        base = os.path.dirname(os.path.abspath(dag))
        with open(dag) as f:
            for line in f:
                w = line.split()
                if not w or w[0].startswith("#"):
                    continue
                kind = w[0].upper()
                if kind in ("JOB", "SUBDAG") and len(w) >= 3:
                    sub = w[3] if kind == "SUBDAG" and len(w) >= 4 and w[1].upper() == "EXTERNAL" else w[2]
                    d = base
                    if "DIR" in [x.upper() for x in w[3:]]:
                        d = os.path.join(base, w[[x.upper() for x in w].index("DIR") + 1])
                    path = sub if os.path.isabs(sub) else os.path.join(d, sub)
                    if kind == "SUBDAG":
                        h, _ = dag_gpu_hours([path], depth + 1)
                    else:
                        cwd = os.getcwd()
                        try:
                            os.chdir(d)
                            h, _ = condor_gpu_hours([path])
                        finally:
                            os.chdir(cwd)
                    total += h
                    n_nodes += 1
    return total, "DAG of %d node%s" % (n_nodes, "s" if n_nodes != 1 else "")


def gpu_hours(cmd, args, env, stdin_text=None):
    if cmd == "sbatch":
        return sbatch_gpu_hours(args, env, stdin_text)
    if cmd == "condor_submit":
        return condor_gpu_hours(args, stdin_text)
    if cmd == "condor_submit_dag":
        return dag_gpu_hours(args)
    raise Unreadable("unknown submit command %s" % cmd)


def mentions_gpu(args, stdin_text, depth=0):
    """anything in the command, its files or (a DAG's) node files that names a GPU."""
    blob = " ".join(args) + (stdin_text or "")
    for a in args:
        if os.path.isfile(a):
            try:
                with open(a) as f:
                    text = f.read(200000)
            except (OSError, UnicodeDecodeError):
                continue
            blob += text
            if depth < 3:
                for m in re.finditer(r"^\s*(?:JOB|SUBDAG\s+EXTERNAL)\s+\S+\s+(\S+)", text, re.M | re.I):
                    p = m.group(1) if os.path.isabs(m.group(1)) else os.path.join(os.path.dirname(os.path.abspath(a)),
                                                                                  m.group(1))
                    if mentions_gpu([p], None, depth + 1):
                        return True
    return re.search(r"gpu|gres|tres", blob, re.I) is not None


# ------------------------------------------------------------------ which items a job counts for
def job_names(cmd, args, stdin_text=None):
    """the names a job can be matched by against the items' globs: its job name / batch name, its script's or submit
    file's path components (a sweep's trial sits in <sweep>/jobs/), the DAG's directory."""
    names = []
    for i, a in enumerate(args):
        k, eq, v = a.partition("=")
        if k in ("--job-name", "-J", "-batch-name") and (eq or i + 1 < len(args)):
            names.append(v if eq else args[i + 1])
        elif a.startswith("-J") and len(a) > 2:
            names.append(a[2:])
        if os.path.isfile(a) or a.endswith((".sh", ".sub", ".dag")):
            names += [x for x in os.path.abspath(a).split(os.sep) if x]
            try:
                with open(a) as f:
                    m = re.search(r"^#SBATCH\s+(?:--job-name[= ]|-J\s*)(\S+)", f.read(200000), re.M)
                if m:
                    names.append(m.group(1))
            except (OSError, UnicodeDecodeError):
                pass
    return names


def counted_items(items, globs, names):
    """the named items this job counts for: with one item named, that item; with several (`--item '*'`, upkeep), the
    ones whose globs match one of the job's names (a sweep's trial counts for its sweep's item); when none matches,
    all of them (never less than the truth)."""
    if len(items) <= 1:
        return list(items)
    hit = [i for i in items if any(fnmatch.fnmatchcase(n, g) for g in (globs or {}).get(i) or [] for n in names)]
    return hit or list(items)


# ------------------------------------------------------------------ the window
def never_ran(jobids):
    """The Slurm jobs of `jobids` cancelled before they started: they used no GPU (counting them at
    their limit would hold GPU-h in the window that nothing spent). Unreadable: none (counted)."""
    if not jobids or not shutil.which("sacct"):
        return set()
    try:
        out = subprocess.run(["sacct", "-n", "-X", "-P", "-o", "JobID,State,Start", "-j", ",".join(sorted(jobids))],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    gone = set()
    for ln in out.splitlines():
        f = ln.split("|")
        if len(f) >= 3 and f[1].startswith("CANCELLED") and f[2] in ("Unknown", "None", ""):
            gone.add(f[0].split("_")[0])
    return gone


def unstarted_at(times):
    """Records without a job id (written by older versions): the submission times among `times` at which every job this
    user submitted (within 5 s) was cancelled before it started. Unreadable: none (counted)."""
    if not times or not shutil.which("sacct"):
        return set()
    start = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(min(times) - 60))
    try:
        out = subprocess.run(["sacct", "-n", "-X", "-P", "-S", start, "-E", "now", "-s", "CA,R,PD,CD,F,TO,NF,OOM",
                              "-o", "Submit,State,Start"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    jobs = []
    for ln in out.splitlines():
        f = ln.split("|")
        try:
            jobs.append((time.mktime(time.strptime(f[0], "%Y-%m-%dT%H:%M:%S")), f[1], f[2]))
        except (ValueError, IndexError):
            continue
    found = set()
    for t in times:
        near = [j for j in jobs if abs(j[0] - t) <= 5]
        if near and all(st.startswith("CANCELLED") and sd in ("Unknown", "None", "") for _, st, sd in near):
            found.add(t)
    return found


def ended_savings(jobids):
    """{jobid: GPU-h} that an ended Slurm job (COMPLETED, FAILED, CANCELLED, TIMEOUT, ...) did not use of the time limit
    it was counted at: (limit - elapsed) x GPUs, summed over an array's tasks (a batch that fails at
    start would otherwise hold the next submission at its full limit). A pending or running job saves nothing (counted
    at its limit); one cancelled unstarted has elapsed 0, so saves all. Unreadable: nothing saved (counted)."""
    if not jobids or not shutil.which("sacct"):
        return {}
    try:
        out = subprocess.run(["sacct", "-n", "-X", "-P", "-o", "JobID,State,ElapsedRaw,TimelimitRaw,ReqTRES,AllocTRES",
                              "-j", ",".join(sorted(jobids))], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_savings(out)


def parse_savings(out):
    ended = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE")
    saved = {}
    for ln in out.splitlines():
        f = ln.split("|")
        if len(f) < 6 or not f[1].startswith(ended):
            continue
        try:
            el, lim = float(f[2]) / 3600.0, float(f[3]) / 60.0
        except ValueError:
            continue
        g = 0
        for tres in (f[5], f[4]):
            m = re.search(r"gres/gpu(?::[\w.-]+)?=(\d+)", tres)
            if m:
                g = int(m.group(1))
                break
        if g and lim > el:
            j = f[0].split("_")[0]
            saved[j] = saved.get(j, 0.0) + (lim - el) * g
    return saved


def window(path, project, items, now):
    total = 0.0
    recs = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("project") == project and now - r.get("t", 0) <= WINDOW_S and set(r.get("items") or []) & \
                        set(items):
                    recs.append(r)
        saved = ended_savings({r["jobid"] for r in recs if r.get("jobid")})
        old = [r for r in recs if not r.get("jobid")]
        unstarted = unstarted_at([r["t"] for r in old]) if old else set()
        for r in recs:
            if not r.get("jobid") and r.get("t") in unstarted:
                continue
            total += max((r.get("gpu_h") or 0.0) - saved.get(r.get("jobid"), 0.0), 0.0)
    except OSError:
        pass
    return total


def prune(path, now):
    """keep the window file small: past 256 kB it is rewritten with the last day's records."""
    try:
        if os.path.getsize(path) < 256 * 1024:
            return
        keep = []
        with open(path) as f:
            for line in f:
                try:
                    if now - json.loads(line).get("t", 0) <= 24 * 3600:
                        keep.append(line)
                except ValueError:
                    pass
        with open(path + ".tmp", "w") as f:
            f.writelines(keep)
        os.rename(path + ".tmp", path)
    except OSError:
        pass


def main(argv):
    cmd, args = os.path.basename(argv[0]), argv[1:]
    env = os.environ
    work = json.loads(env.get("CCORCH_WORK") or "{}") or {}
    items = work.get("items") or []
    b = work.get("budget") or {}
    project = b.get("project") or ""
    site = env.get("CCORCH_SITE") or b.get("site") or ""
    real = shutil.which(cmd)
    if not real or os.path.dirname(os.path.realpath(real)) == os.path.dirname(os.path.realpath(__file__)):
        sys.stderr.write("site gate: no real %s on PATH\n" % cmd)
        return 1
    stdin_text = None
    if not sys.stdin.isatty() and ((cmd == "sbatch" and sbatch_options(args)[1] is None
                                    and "--wrap" not in sbatch_options(args)[0])
                                   or (cmd == "condor_submit" and not [a for a in args if os.path.isfile(a)])):
        stdin_text = sys.stdin.read()
    try:
        gh, what = gpu_hours(cmd, args, env, stdin_text)
        items = counted_items(items, b.get("globs"), job_names(cmd, args, stdin_text))
    except Exception as e:
        if mentions_gpu(args, stdin_text):
            sys.stderr.write("site gate: %s refused: its GPU request cannot be read (%s), so its GPU-hours cannot be "
                             "counted against the budget; write it out plainly (--time, --gpus/--gres, --array; "
                             "request_gpus, +JobFlavour, queue N)\n" % (cmd, e))
            return 1
        gh, what = 0.0, "no GPU asked (not readable: %s)" % e
    home = os.path.join(os.path.expanduser("~"), ".ccorch")
    if not os.path.isdir(home):
        os.makedirs(home)
    wpath = os.path.join(home, "gpu_window.jsonl")
    lock = open(wpath + ".lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX)                 # check, submit and record as one step
    try:
        now = time.time()
        if gh > 0:
            here = window(wpath, project, items, now)
            # the other sites' records, each once when it shares an item with this job
            prior = sum(h for its, h in b.get("prior_records") or [] if set(its) & set(items))
            total = here + prior + gh
            scarce = bool(b.get("scarce"))
            cap = float(b.get("cap", DEFAULT_CAP))
            cap_s = float(b.get("cap_scarce", DEFAULT_CAP_SCARCE))
            site_total = here + gh                  # on a scarce site its own hours count against its own cap
            over = []
            if total > cap:
                over.append("%.1f GPU-h for %s in the last 60 min with this job (%.1f on %s, %.1f elsewhere; limit %g)"
                            % (total, "+".join(items), here + gh, site or "this site", prior, cap))
            if scarce and site_total > cap_s:
                over.append("%.1f GPU-h on %s in the last 60 min with this job (limit %g there)" % (site_total, site,
                                                                                                  cap_s))
            ests = [(b.get("est") or {}).get(i) for i in items]
            est = sum(ests) if ests and all(e is not None for e in ests) else None
            if over and not (est is not None and est >= max(total, site_total if scarce else 0.0)):
                sys.stderr.write(
                    "site gate: %s refused (%s): %s, and the item's recorded estimate (%s) does not cover it. Above "
                    "the budget a decision in force must cover it (its cost, or the work by "
                    "name), else the user confirms first; record the covered total with `site item set %s %s "
                    "--est-gpu-hours <approved total>`, then submit again.\n" % (
                        cmd, what, "; ".join(over), "none" if est is None else "%g GPU-h" % est, project or "<project>",
                        items[0] if len(items) == 1 else "<item>"))
                return 1
        # the scheduler's answer is passed through and read for the job id (the window drops a job cancelled unstarted)
        p = subprocess.Popen([real] + args, stdin=subprocess.PIPE if stdin_text is not None else None,
                             stdout=subprocess.PIPE)
        out = p.communicate(stdin_text.encode() if stdin_text is not None else None)[0] or b""
        sys.stdout.write(out.decode(errors="replace"))
        sys.stdout.flush()
        rc = p.returncode
        if rc == 0 and gh > 0:
            rec = {"t": int(now), "project": project, "site": site, "items": items, "gpu_h": round(gh, 4),
                   "what": "%s %s" % (cmd, what)}
            m = re.search(r"Submitted batch job (\d+)|^(\d+)(?:;\S+)?\s*$", out.decode(errors="replace"), re.M) if cmd == "sbatch" else None
            if m:
                rec["jobid"] = m.group(1) or m.group(2)
            prune(wpath, now)
            with open(wpath, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
            if env.get("CCORCH_GATE_LOG"):
                try:
                    with open(env["CCORCH_GATE_LOG"], "a") as f:
                        f.write(json.dumps(rec, sort_keys=True) + "\n")
                except OSError:
                    pass
        return rc
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
