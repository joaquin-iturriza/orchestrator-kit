"""The supervisor's work that needs no model. Imported by ~/.local/bin/site; every function here is pure or takes
its side effects as arguments, so tests can run it on recorded states.

  sort_run_end     a run's end classed `clean` (finished cleanly), `limit` (a queue worker that hit its planned wall
                   limit with tasks left) or `broke` (anything else), with the log line that decided it
  queue_plan       how many workers of an approved queue to resubmit now, within its recorded limits, or why none
  predict_end      a batch's predicted wall time from its progress series, and whether it passes 2x its target

Enabled per project by projects.toml `[projects.<P>.run_ends] enabled = true` (default off).
"""
import json, re

DEFAULT_RULES = {
    "enabled": False,
    # job paths (fnmatch on the registry's job column) of queue workers: only these can end at a planned limit
    "workers": [],
    # a COMPLETED run is clean only with one of these in its log
    "end_markers": [r"RUN_SUMMARY .* rc=0\b", r"RESULT_JSON", r"RUN_DONE"],
    # a job script whose source matches one of these prints an end marker (itself, or through run.py / the sweep
    # driver, base_experiment's RUN_SUMMARY) and is held to it; any other script's COMPLETED (exit 0) is clean
    "marker_producers": [r"RUN_SUMMARY", r"RESULT_JSON", r"RUN_DONE", r"\brun\.py\b", r"drive\.py", r"sweep_manager"],
    # any of these anywhere in the log makes the run broke, whatever its state
    "error_patterns": [r"Traceback", r"IndexError", r"\bOOM\b", r"[Oo]ut of memory", r"oom.kill",
                       r"fail to reach target", r"No space left on device", r"\[FAIL\]", r"StaleLayout",
                       r"RUN_SUMMARY .* rc=[1-9]", r"\bexit [1-9]"],
    # the scheduler's own line when it kills a job at its wall limit (written at the end: a log missing it stopped
    # being written before the end, e.g. a full disk)
    "limit_line": r"CANCELLED AT \S+ DUE TO TIME LIMIT",
    # the worker's queue and its wall (the job's limit less 5 min), and its queue status lines
    "queue_line": r"^queue (\S+)\s+cores \d+\s+hours ([0-9.]+)",
    "status_line": r'^\{"todo": ',
    "wall_margin_h": 5 / 60.0,
    # a broke run whose cause is already known: its deciding line matches
    # `line`, `pool` (group 1) names the pool, and the pool is a key of one of `lists` (JSON files relative to the
    # project, a rebuild list): acked with that note; any other broke run stays an open event
    "known_causes": [],
}


def rules_of(pr):
    r = dict(DEFAULT_RULES)
    r.update((pr or {}).get("run_ends") or {})
    r.setdefault("local", (pr or {}).get("local"))
    return r


def _lines(log):
    head = (log.get("head") or "").splitlines()
    tail = (log.get("tail") or "").splitlines()
    match = [m[1] if isinstance(m, (list, tuple)) else m for m in (log.get("matches") or [])]
    return head, tail, match


def _first(pats, lines):
    for ln in lines:
        for p in pats:
            if re.search(p, ln):
                return ln.strip()
    return None


def _status(lines, rules):
    st = None
    for ln in lines:
        if re.search(rules["status_line"], ln.strip()):
            try:
                st = json.loads(ln.strip())
            except ValueError:
                pass
    return st


_BANNER = re.compile(r"^\s*(\*.*)?$")     # blank, or a login-notice line (CC-IN2P3's frame and text start with '*')


def _last_line(tail):
    """The log's last line that is not blank and not part of a login banner."""
    return next((l.strip() for l in reversed(tail) if not _BANNER.match(l)), "")


def prints_marker(job, rules):
    """True when the job script's source (in the local checkout) prints an end marker, or when it cannot be read
    (unknown scripts stay held to the marker)."""
    import os
    local = rules.get("local")
    if not job or not local:
        return True
    path = job if os.path.isabs(job) else os.path.join(os.path.expanduser(local), job)
    try:
        src = open(path, errors="replace").read()
    except OSError:
        return True
    return any(re.search(p, src) for p in rules.get("marker_producers") or [])


def sort_run_end(state, job, log, rules, queue=None):
    """(cls, line): cls 'clean' | 'limit' | 'diverged' | 'broke'; line: the log line (or the fact) that decided it.

    clean   COMPLETED, an end marker in the log, no error pattern anywhere in it
    limit   TIMEOUT of a queue worker (rules.workers) whose log runs to the scheduler's time-limit line, with no error
            pattern, whose queue still had tasks (its last status line; `queue` = the live counts when known) and,
            when the queue is registered with a wall (queue['wall_h']), whose own wall is that one
    broke   anything else: FAILED, OOM, NODE_FAIL, an error line, a log that stops before the end, a TIMEOUT of a job
            that is no queue worker, a COMPLETED run with no end marker
    """
    import fnmatch
    head, tail, match = _lines(log or {})
    if not (head or tail):
        return "broke", "no log found"
    every = head + match + tail
    err = _first(rules["error_patterns"], every)
    if state == "FAILED":
        # the training loop's own divergence stop (a plain run, or a DyHPO trial whose RESULT_JSON says failed) is a
        # result, not a breakage; any other error line in the log still makes it broke
        dv = _first([r"Training diverged.* at step \d+"], every)
        other = _first([p for p in rules["error_patterns"] if p not in (r"Traceback", r"\bexit [1-9]", r"RUN_SUMMARY .* rc=[1-9]")],
                       [l for l in every if "Training diverged" not in l])
        if dv and not other:
            return "diverged", dv
    if state == "COMPLETED":
        if err:
            return "broke", err
        mk = _first(rules["end_markers"], list(reversed(tail)) + match)
        if mk:
            return "clean", mk
        if not prints_marker(job, rules):
            return "clean", "COMPLETED (exit 0) of %s, a script that prints no end marker; last line: %s" % (
                job, _last_line(tail))
        return "broke", "COMPLETED with no end marker (%s); last line: %s" % (
            ", ".join(rules["end_markers"]), _last_line(tail))
    if state == "TIMEOUT":
        if not any(fnmatch.fnmatchcase(job or "", g) for g in rules["workers"]):
            return "broke", "TIMEOUT of %s, which is no queue worker" % (job or "?")
        if err:
            return "broke", err
        lim = _first([rules["limit_line"]], tail + match)
        if not lim:
            last = _last_line(tail)
            return "broke", "the log stops before the time limit (no scheduler time-limit line); last line: %s" % last
        st = dict(queue) if queue and "todo" in queue else _status(head + tail, rules)
        if not st:
            return "broke", "TIMEOUT with the queue's state unknown (no status line in the log)"
        if not st.get("todo"):
            return "broke", "TIMEOUT with no task left in the queue (%s)" % json.dumps(
                {k: st.get(k) for k in ("todo", "claimed", "failed")})
        if queue and queue.get("wall_h"):
            q = _first([rules["queue_line"]], head)
            if q:
                wall = float(re.search(rules["queue_line"], q).group(2)) + rules["wall_margin_h"]
                if abs(wall - float(queue["wall_h"])) > 0.05:
                    return "broke", "wall %.2f h is not the queue's stated %.2f h (%s)" % (wall, queue["wall_h"], q)
        return "limit", "%s; queue had %d to do" % (lim, st["todo"])
    return "broke", err or "ended %s; last line: %s" % (state, _last_line(tail))


def ack_note(run, state, cls, line):
    """The note a supervisor ack carries: the run, the class and the deciding log line (cmd_ack's rule: it names the
    run and says what its log showed)."""
    what = {"clean": "finished cleanly", "limit": "hit its planned wall limit with tasks left",
            "diverged": "diverged (a result, not a breakage; a sweep trial is recorded failed with its penalty)"}[cls]
    return "%s ended %s, %s (sorted by the supervisor, no model): %s" % (run, state, what, line[:300])


def queue_plan(q, live, layout, now):
    """How many workers of a registered queue to submit now, and why.

    q       the registration: max_workers, wall_h, total_hours, submitted_hours (spent so far), until (epoch or None)
    live    workers of the queue pending or running
    layout  `lhe_parts layout` output: {"current": bool, "why": str, "todo": n, "claimed_dead": n, "failed": n, ...}
            or None (unread)
    Returns (n, why, alert): alert is a reason for an ALERT to the owner (None when nothing is wrong).
    A failed task is never put back: the count only reaches the owner (alert), whatever n is.
    """
    if layout is None:
        return 0, "queue layout not read", None
    if not layout.get("current"):
        return 0, "layout stale", "queue %s: layout not current (%s); no workers resubmitted" % (
            q.get("queue") or q.get("id"), layout.get("why") or "?")
    if q.get("until") and now > q["until"]:
        return 0, "past its approved end", None
    # claims of workers that died (e.g. a last wave at its limit) are work too: a worker started now puts them back
    todo = int(layout.get("todo") or 0) + int(layout.get("claimed_dead") or 0)
    if todo <= 0:
        return 0, "nothing to do", None
    room = int(q["max_workers"]) - int(live)
    hours_left = float(q["total_hours"]) - float(q.get("submitted_hours") or 0)
    by_budget = int(hours_left // float(q["wall_h"])) if q["wall_h"] else 0
    n = max(0, min(room, by_budget, todo))
    if n == 0:
        why = "at its worker limit" if room <= 0 else "its total hours are spent"
        alert = None if room <= 0 else "queue %s: its %g approved worker-hours are spent with %d tasks to do" % (
            q.get("queue") or q.get("id"), float(q["total_hours"]), todo)
        return 0, why, alert
    return n, "%d live of %d, %d to do, %.0f h of %g left" % (live, q["max_workers"], todo, hours_left,
                                                                float(q["total_hours"])), None


MARK = "@@CCORCH_RUNENDS@@"


def log_read_cmd(find_cmd, rules, head=22, tail=60):
    """One shell command that finds a run's log (find_cmd prints candidate paths, newest first) and prints its head,
    its tail and every line matching an error, end-marker or limit pattern (at most 40), separated by MARK lines."""
    pats = rules["error_patterns"] + rules["end_markers"] + [rules["limit_line"]]
    ere = "|".join("(%s)" % p for p in pats).replace("'", "'\\''")
    return ("f=$(%s | head -1); [ -n \"$f\" ] || exit 0; head -n %d \"$f\"; echo %s; tail -n %d \"$f\"; echo %s; "
            "grep -n -m 40 -E '%s' \"$f\"" % (find_cmd, head, MARK, tail, MARK, ere))


def condor_log_read_cmd(pat, rules, head=22, tail=60):
    """log_read_cmd for an HTCondor run: its logs/ dir holds the scheduler's event log (<id>.log, written last, so
    newest by mtime) beside the job's .err and .out; the job's own output is stderr then stdout, read as one stream
    (the .log only when neither exists), so the end marker (RESULT_JSON, RUN_SUMMARY ...) on stdout is seen."""
    pats = rules["error_patterns"] + rules["end_markers"] + [rules["limit_line"]]
    ere = "|".join("(%s)" % p for p in pats).replace("'", "'\\''")
    return ("fs=$(ls %s.err %s.out 2>/dev/null); [ -n \"$fs\" ] || fs=$(ls -t %s 2>/dev/null | head -1); "
            "[ -n \"$fs\" ] || exit 0; cat $fs | head -n %d; echo %s; cat $fs | tail -n %d; echo %s; "
            "cat $fs | grep -n -m 40 -E '%s'" % (pat, pat, pat, head, MARK, tail, MARK, ere))


def parse_log_read(out):
    parts = out.split(MARK + "\n")
    if len(parts) < 3:
        return {}
    matches = []
    for ln in parts[2].splitlines():
        n, _, rest = ln.partition(":")
        if n.isdigit():
            matches.append([int(n), rest[:400]])
    return {"head": parts[0], "tail": parts[1], "matches": matches}


def queue_of(log, rules):
    """The queue name a worker's log names (basename of its `queue <path>` line), or None."""
    for ln in (log.get("head") or "").splitlines():
        m = re.search(rules["queue_line"], ln.strip())
        if m:
            return m.group(1).rstrip("/").rsplit("/", 1)[-1]
    return None


def known_cause(line, rules, read_list):
    """The ack note when a broke run's deciding line is a known cause on a rebuild list, else None. read_list(path)
    returns the keys of that JSON file (a dict's keys, a list's items), or an empty set when it cannot be read."""
    for k in rules.get("known_causes") or []:
        if not re.search(k.get("line") or "$^", line or ""):
            continue
        m = re.search(k.get("pool") or "$^", line or "")
        if not m:
            continue
        pool = m.group(1)
        for path in k.get("lists") or []:
            if pool in read_list(path):
                return "known cause: %s; %s is on the rebuild list %s (%s)" % (
                    (line or "")[:200], pool, path, k.get("why") or "")
    return None


def decide_run_end(run, state, job, item, log, rules, queue=None, collection=None, collect=None, read_list=None):
    """What the supervisor does with one run end: {"cls", "line", "ack": note} (closed, routine) or
    {"cls", "line", "event": msg} (an open RUN event for the entry's owner).
    collection: the entry's recorded collection {job, args} or None; collect(run, entry, args) -> (rc, text) runs
    tools/collect_run.py. A clean run of an entry with a collection matching its job is acked only when collect_run
    says nothing is off; otherwise its report is the event."""
    import fnmatch
    cls, line = sort_run_end(state, job, log, rules, queue)
    known = known_cause(line, rules, read_list) if cls == "broke" and read_list else None
    if known:
        return {"cls": "known", "line": line, "ack": "%s ended %s, %s" % (run, state, known)}
    if cls == "broke":
        return {"cls": cls, "line": line, "event": "%s ended %s, broke: %s. Read `site logs %s` and take the entry's "
                "next step" % (run, state, line[:300], run)}
    note = ack_note(run, state, cls, line)
    if cls == "clean" and collection and collect and fnmatch.fnmatchcase(job or "", collection.get("job") or ""):
        rc, text = collect(run, item, collection.get("args") or "")
        last = " ".join((text or "").split())[-300:]
        if rc:
            return {"cls": cls, "line": line, "event": "%s finished cleanly but its collection reported something off "
                    "(tools/collect_run.py exit %d): %s" % (run, rc, last)}
        note += "; collected: %s" % last
    return {"cls": cls, "line": line, "ack": note}


def predict_end(base, point, t0):
    """Predicted wall hours of a batch started at t0: base = (ts, done) its first progress point (history starts at
    the first supervisor pass after the target was recorded, possibly after t0), point = (ts, done, expected) now.
    The rate is measured over base -> point; the prediction is the time already spent since t0 plus what is left at
    that rate. None before any progress since base."""
    t, d, e = point
    span = (t - base[0]) / 3600.0
    rate = (d - base[1]) / span if span > 0 else 0
    if rate <= 0:
        return None
    return (t - t0) / 3600.0 + max(0, e - d) / rate


MIN_FRACTION = 0.05      # no prediction before 5% of the batch is done (one early part is no rate) ...
MIN_ELAPSED = 0.25       # ... or before a quarter of its target has passed


def overrun(series, t0, wall_h, factor=2.0):
    """The first point of series [(ts, done, expected)] at which the batch's predicted wall time passes factor x its
    target wall_h, as (hours since t0, predicted hours or None when it is simply past factor x unfinished); None while
    it does not. No prediction before MIN_ELAPSED of the target has passed or MIN_FRACTION of the batch is done."""
    pts = [p for p in series if p[0] >= t0]
    if not pts:
        return None
    base = pts[0]
    for t, d, e in pts:
        el = (t - t0) / 3600.0
        if el <= 0 or not e:
            continue
        if el > factor * wall_h and d < e:
            return el, None
        if (d - base[1]) / float(e) < MIN_FRACTION or (t - base[0]) / 3600.0 < MIN_ELAPSED * wall_h:
            continue
        p = predict_end(base, (t, d, e), t0)
        if p is not None and p > factor * wall_h:
            return el, p
    return None


# ------------------------------------------------------------------ glue: called by `site` with its own module (m)
QUEUE_SCHEMA = """CREATE TABLE IF NOT EXISTS queues (
  project TEXT, id TEXT, decision TEXT, item TEXT, site TEXT, job TEXT, max_workers INTEGER, wall_h REAL,
  total_hours REAL, submitted_hours REAL DEFAULT 0, until INTEGER, failed_seen INTEGER, last TEXT, status TEXT,
  created INTEGER, PRIMARY KEY (project, id))"""
QKEYS = ("project", "id", "decision", "item", "site", "job", "max_workers", "wall_h", "total_hours", "submitted_hours",
         "until", "failed_seen", "last", "status", "created")


def queues(c, project=None, qid=None):
    c.execute(QUEUE_SCHEMA)
    q, p = "SELECT %s FROM queues WHERE 1=1" % ",".join(QKEYS), []
    if project:
        q += " AND project=?"; p.append(project)
    if qid:
        q += " AND id=?"; p.append(qid)
    return [dict(zip(QKEYS, r)) for r in c.execute(q + " ORDER BY created", p)]


def read_log(m, sc, project, path, sched_id, rules):
    """The run's log as {head, tail, matches} through one ssh; None when the site could not be read."""
    import shlex
    pat = "*%s*" % sched_id
    if sc["scheduler"] == "condor":
        cwd = m.submit_cwd(sc, m.project_of(project), project, path)
        inner = "cd %s/logs && %s" % (shlex.quote(cwd), condor_log_read_cmd(pat, rules))
    else:
        find = ("ls -t $(find . -maxdepth 3 -name %s -newermt '-30 days' -not -path './.venv/*' 2>/dev/null) 2>/dev/null"
                % shlex.quote(pat))
        inner = "cd %s && %s" % (shlex.quote(path), log_read_cmd(find, rules))
    try:
        rc, out = m.run_ssh(sc, inner, timeout=120)
    except Exception:
        return None
    if rc and not out.strip():
        return None
    return parse_log_read(out)


def _collections(m, c, name, pr, cache):
    if "collections" not in cache:
        rules = rules_of(pr)
        rc, out = m._run_local(rules.get("collections_cmd") or "python3 tools/plan.py collections --json",
                               m.os.path.expanduser(pr["local"]), 120)
        try:
            cache["collections"] = {x["entry"]: x for x in json.loads(out.strip().splitlines()[-1])} if not rc else {}
        except (ValueError, IndexError):
            cache["collections"] = {}
    return cache["collections"]


def run_end(m, c, name, pr, rid, sc, st, item, log=print, cache=None):
    """One run end under the switch: sorted, then acked (clean, limit) or left as an open RUN event (broke). Returns
    the number of new open events, or None when the log could not be read (the caller then raises the plain event)."""
    import time as _t
    rules = rules_of(pr)
    job, path, sched_id = c.execute("SELECT job, remote_path, sched_id FROM runs WHERE id=?", (rid,)).fetchone()
    lg = read_log(m, sc, name, path, sched_id, rules)
    if lg is None:
        return None
    qn = queue_of(lg, rules)
    reg = queues(c, name, qn) if qn else []
    qinfo = {"wall_h": reg[0]["wall_h"]} if reg else None
    coll = _collections(m, c, name, pr, cache if cache is not None else {}).get(item)
    cwd = m.os.path.expanduser(pr["local"])
    collect = lambda run, entry, args: m._run_local("python3 tools/collect_run.py %s --entry %s %s" % (run, entry, args),
                                                    cwd, 1800)
    def read_list(rel):
        try:
            x = json.load(open(m.os.path.join(cwd, rel)))
        except (OSError, ValueError):
            return set()
        return set(x) if isinstance(x, (dict, list)) else set()
    act = decide_run_end(rid, st, job, item, lg, rules, qinfo, coll, collect, read_list)
    m.note_read(rid, "supervisor")
    if cache is not None:
        cache.setdefault("ended_runs", []).append(rid)      # every class: a batch can close on a broken run
    key = "%s:%s" % (rid, st)
    log("  [%s] %s ended %s: %s (%s)" % (name, rid, st, act["cls"], act["line"][:160]))
    if "ack" in act:
        if act["cls"] == "clean" and cache is not None:
            cache.setdefault("clean_runs", []).append(rid)
        now = int(_t.time())
        c.execute("INSERT INTO events (ts,project,item,kind,key,msg,acked,ack_note,tell) VALUES (?,?,?,?,?,?,?,?,?)",
                  (now, name, item, "RUN", key, "%s ended %s" % (rid, st), now, act["ack"], "routine"))
        c.commit()
        return 0
    fold = fold_reason(m, c, name, pr, rid, job, item, act["line"], cache)
    if fold:
        now = int(_t.time())
        c.execute("INSERT INTO events (ts,project,item,kind,key,msg,acked,ack_note,tell) VALUES (?,?,?,?,?,?,?,?,?)",
                  (now, name, item, "RUN", key, "%s ended %s" % (rid, st), now,
                   "%s ended %s, broke, %s (sorted by the supervisor, no model): %s" % (rid, st, fold, act["line"][:300]),
                   "routine"))
        c.commit()
        return 0
    return m.add_event(c, name, item, "RUN", key, act["event"])


FOLD_JOBS = ("sweep/trial_job.sh",)   # a search's trial: its driver records the failure, the search's end reports it
FOLD_WINDOW_H = 6


def _signature(line):
    """A failure's kind, comparable across runs: the deciding line without numbers, ids and paths."""
    return re.sub(r"[0-9a-f]{6,}|\d+|/\S+", "#", line or "")[:80]


def fold_reason(m, c, name, pr, rid, job, item, line, cache=None):
    """Why a broken run is folded into its batch's result instead of its own event, or None. Folded: a search trial, or a run an owner's wait names (its batch's RUN_DONE
    lists it). Never folded: a second failure of the same kind in the same item within FOLD_WINDOW_H, which is a
    pattern (a bug, a bad config), not an individual failure."""
    sig = _signature(line)
    since = int(__import__("time").time()) - FOLD_WINDOW_H * 3600
    for (note,) in c.execute("SELECT ack_note FROM events WHERE project=? AND item=? AND kind='RUN' AND ts>=? AND "
                             "ack_note LIKE '%broke, folded%'", (name, item, since)):
        if _signature(note.split("): ", 1)[-1]) == sig:
            return None
    if job in FOLD_JOBS:
        return "folded into its search (a trial)"
    if cache is not None and "owners" not in cache:
        cache["owners"] = read_owners(m, pr)
    owners = (cache or {}).get("owners") if cache is not None else read_owners(m, pr)
    if any(rid in (o.get("runs") or []) for o in (owners or {}).values()):
        return "folded into its batch (the batch's RUN_DONE lists it)"
    return None


def _hms(h):
    s = int(round(h * 3600))
    return "%02d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def queue_pass(m, c, name, pr, log=print):
    """Approved queues: read each one's layout and counts on its site, report new failed tasks (never put back), and
    resubmit workers within the queue's recorded limits while its layout is current."""
    import time as _t
    new = 0
    cwd = m.os.path.expanduser(pr["local"])
    for q in queues(c, name):
        if q["status"] != "active":
            continue
        blocked = m._launch_blocked(c, {"project": name, "decision": q["decision"], "item": q["item"]})
        rc, out = m._run_local("site --timeout 290 run %s %s -- python tools/lhe_parts.py layout --queue "
                               "'$SCRATCH/lhe_queue/%s'" % (q["site"], name, q["id"]), cwd, 320)
        lay = None
        for ln in reversed(out.splitlines()):
            if ln.startswith("{"):
                try:
                    lay = json.loads(ln)
                except ValueError:
                    pass
                break
        if lay is None:
            new += m.add_event(c, name, q["item"], "ALERT", "queue:%s:unread" % q["id"], "queue %s: its layout could "
                               "not be read (exit %d): %s" % (q["id"], rc, " ".join(out.split())[-200:]))
            continue
        lay["ts"] = int(_t.time())
        c.execute("UPDATE queues SET last=? WHERE project=? AND id=?", (json.dumps(lay), name, q["id"]))
        c.commit()
        failed = int(lay.get("failed") or 0)
        if q["failed_seen"] is not None and failed > q["failed_seen"]:
            new += m.add_event(c, name, q["item"], "RUN", "queue:%s:failed:%d" % (q["id"], failed),
                               "queue %s: %d task(s) failed since the last pass (%d failed in all); the supervisor "
                               "never puts a failed task back: read its failed/*.err and decide"
                               % (q["id"], failed - q["failed_seen"], failed))
        c.execute("UPDATE queues SET failed_seen=? WHERE project=? AND id=?", (failed, name, q["id"]))
        c.commit()
        if blocked:
            new += m.add_event(c, name, q["item"], "ALERT", "queue:%s:blocked" % q["id"], "queue %s: no workers "
                               "resubmitted: %s" % (q["id"], blocked))
            continue
        # every unfinished run of the item with the queue's job counts, the owner's own submissions included (their
        # notes need not name the queue): it can only under-count room, never over-submit
        live = c.execute("SELECT COUNT(*) FROM runs WHERE project=? AND item=? AND job=? AND state NOT IN (%s)"
                         % ",".join("?" * len(m.TERMINAL)), [name, q["item"], q["job"]] + list(m.TERMINAL)
                         ).fetchone()[0]
        n, why, alert = queue_plan(q, live, lay, _t.time())
        log("  [%s] queue %s: %s live, %s" % (name, q["id"], live, why))
        if alert:
            new += m.add_event(c, name, q["item"], "ALERT", "queue:%s:%s" % (q["id"], why), alert)
        done = []
        for _ in range(n):
            rc, out = m._run_local("site submit %s %s %s --no-sync --item %s --env LHE_QUEUE=%s --hdr=--time=%s "
                                   "--note 'queue:%s worker resubmitted by the supervisor (%s)'"
                                   % (q["site"], name, q["job"], q["item"], q["id"], _hms(q["wall_h"]), q["id"],
                                      q["decision"]), cwd, 300)
            rid = re.search(r"\b(%s-%s-[0-9a-f]{8})\b" % (re.escape(name), re.escape(q["site"])), out)
            if rc or not rid:
                new += m.add_event(c, name, q["item"], "ALERT", "queue:%s:submit" % q["id"], "queue %s: a worker "
                                   "resubmission failed (exit %d): %s" % (q["id"], rc, " ".join(out.split())[-200:]))
                break
            done.append(rid.group(1))
            c.execute("UPDATE queues SET submitted_hours=COALESCE(submitted_hours,0)+? WHERE project=? AND id=?",
                      (q["wall_h"], name, q["id"]))
            c.commit()
        if done:
            now = int(_t.time())
            c.execute("INSERT INTO events (ts,project,item,kind,key,msg,acked,ack_note,tell) VALUES (?,?,?,?,?,?,?,?,?)",
                      (now, name, q["item"], "LAUNCHED", "queue:%s:%d" % (q["id"], now), "queue %s: %d worker(s) "
                       "resubmitted: %s" % (q["id"], len(done), ", ".join(done)), now, "within %s's limits (%s)"
                       % (q["decision"], why), "routine"))
            c.commit()
    return new


def _history(m, name):
    return m.os.path.join(m.HOME, "progress.%s.json" % name)


def target_pass(m, c, name, pr, outs, log=print):
    """Each recorded wall-time target: the batch's progress appended to its series, an ALERT once its predicted wall
    time passes 2x the target."""
    import fnmatch, time as _t
    rules = rules_of(pr)
    cwd = m.os.path.expanduser(pr["local"])
    rc, out = m._run_local(rules.get("targets_cmd") or "python3 tools/plan.py targets --json", cwd, 120)
    try:
        targets = json.loads(out.strip().splitlines()[-1]) if not rc else []
    except (ValueError, IndexError):
        targets = []
    path = _history(m, name)
    try:
        hist = json.load(open(path))
    except (OSError, ValueError):
        hist = {}
    new = 0
    now = int(_t.time())
    for t in targets:
        key = "%s|%s" % (t["entry"], t["batch"])
        d = e = None
        reg = queues(c, name, t["batch"])
        if reg and reg[0]["status"] == "removed" and not t.get("units"):
            # a retired queue (site queue rm) is no longer worked: its last snapshot would re-raise the overrun forever
            continue
        if reg and reg[0]["last"] and not t.get("units"):
            L = json.loads(reg[0]["last"])
            d = int(L.get("done") or 0)
            e = sum(int(L.get(k) or 0) for k in ("todo", "claimed", "done", "failed"))
        elif t.get("units"):
            it = c.execute("SELECT check_cmd FROM items WHERE project=? AND id=?", (name, t["entry"])).fetchone()
            res = outs.get(it[0]) if it and it[0] else None
            if res:
                us = {u: v for u, v in (res.get("units") or {}).items()
                      if fnmatch.fnmatchcase(u, t["units"])}
                e = sum(v.get("expected", 0) for v in us.values())
                d = sum(min(v.get("done", 0), v.get("expected", 0)) for v in us.values())
        if e is None:
            # never the whole item in place of the batch: a batch is a registered queue or the item check's --units
            log("  [%s] target %s: no progress source (no registered queue of that name, no --units on an item "
                "check)" % (name, key))
            continue
        s = hist.setdefault(key, [])
        if e and d >= e:
            # a finished batch: an overrun was raised while it ran, if there was one; measuring it to now would
            # count the time since its last task ended
            continue
        if not s or s[-1][0] != now:
            s.append([now, d, e])
        hit = overrun([tuple(x) for x in s], t["start_ts"], t["wall_h"])
        if hit:
            el, p = hit
            new += m.add_event(c, name, t["entry"], "ALERT", "walltarget:%s" % key,
                               "batch %s of %s: %s, more than 2x its %g h target (%d of %d done after %.1f h)"
                               % (t["batch"], t["entry"], "past 2x its target unfinished" if p is None else
                                  "predicted to take %.1f h" % p, t["wall_h"], d, e, el))
    with open(path, "w") as f:
        json.dump(hist, f)
    return new


def stalled_pass(m, c, name, pr, outs=None, log=print):
    """Each STALLED entry of the project's plan (status_cmd, default `python3 tools/plan.py status --json`: a running
    entry with no live job, no working owner and no open ask) is an open STALLED event, so the waiter wakes the main
    session in a quiet spell; the event is acked once the entry is no longer stalled."""
    import time as _t
    rules = rules_of(pr)
    rc, out = m._run_local(rules.get("status_cmd") or "python3 tools/plan.py status --json",
                           m.os.path.expanduser(pr["local"]), 120)
    if rc:
        raise RuntimeError("status_cmd failed: %s" % out[-200:])
    rows = json.loads(out.strip().splitlines()[-1])
    stalled = {x["entry"]: x["why"] for x in rows if x["state"] == "STALLED"}
    # an entry the user's hold in force covers is paused on purpose, not stalled; an entry whose open question holds every next step is already WAITING ON YOU in the status
    for (cov,) in c.execute("SELECT covers FROM decisions WHERE project=? AND kind='hold' AND in_force=1", (name,)).fetchall():
        for x in (cov or "").split(","):
            stalled.pop(x.strip(), None)
    new = 0
    for e, why in stalled.items():
        new += m.add_event(c, name, e, "STALLED", "stalled:%s" % e,
                           "%s is STALLED: %s; resume its owner or launch one" % (e, why))
    for eid, item, key in c.execute("SELECT id, item, key FROM events WHERE project=? AND kind='STALLED' AND "
                                    "acked IS NULL", (name,)).fetchall():
        if item not in stalled:
            c.execute("UPDATE events SET acked=?, ack_note=?, tell='routine' WHERE id=?",
                      (int(_t.time()), "%s is no longer stalled" % item, eid))
    c.commit()
    return new


def project_pass(m, c, name, pr, outs, log=print):
    new = 0
    for f in (lambda: queue_pass(m, c, name, pr, log), lambda: target_pass(m, c, name, pr, outs, log),
              lambda: stalled_pass(m, c, name, pr, outs, log)):
        try:
            new += f()
        except Exception as e:
            new += m.add_event(c, name, None, "ALERT", "run_ends pass failed: %s" % type(e).__name__,
                               "the supervisor's queue/target step failed: %r" % e)
    return new


def cmd_queue(m, a):
    """site queue add|list|rm: an approved queue whose workers the supervisor keeps resubmitting within its limits."""
    import time as _t
    c = m.ldb()
    queues(c)
    if a.action == "list":
        for q in queues(c, a.project):
            last = json.loads(q["last"]) if q["last"] else {}
            print("  %-8s %-24s %-7s %-5s %-18s workers<=%d wall %gh hours %g/%g  %s" % (
                q["project"], q["id"], q["status"], q["decision"], q["item"], q["max_workers"], q["wall_h"],
                q["submitted_hours"] or 0, q["total_hours"],
                ("current" if last.get("current") else "NOT current: %s" % last.get("why")) if last else "unread"))
        return
    if not (a.project and a.id):
        m.die("site queue %s <project> <queue>" % a.action)
    if a.action == "rm":
        c.execute("UPDATE queues SET status='removed' WHERE project=? AND id=?", (a.project, a.id))
        c.commit()
        print("  queue %s/%s: no more resubmissions" % (a.project, a.id))
        return
    need = (a.decision, a.item, a.site, a.job, a.max_workers, a.wall, a.total_hours)
    if any(x is None for x in need):
        m.die("site queue add <project> <queue> --decision D<n> --item <item> --site <site> --job <script> "
              "--max-workers N --wall H --total-hours T [--until YYYY-MM-DDTHH:MM]")
    why = m._launch_blocked(c, {"project": a.project, "decision": a.decision, "item": a.item})
    if why:
        m.die("queue refused: %s (a queue's workers are resubmitted only under a decision of the user's in force "
              "and an active item)" % why)
    until = None
    if a.until:
        import datetime
        until = int(datetime.datetime.fromisoformat(a.until).timestamp())
    c.execute("INSERT OR REPLACE INTO queues (project,id,decision,item,site,job,max_workers,wall_h,total_hours,"
              "submitted_hours,until,failed_seen,last,status,created) VALUES (?,?,?,?,?,?,?,?,?,0,?,NULL,NULL,'active',?)",
              (a.project, a.id, a.decision, a.item, a.site, a.job, a.max_workers, a.wall, a.total_hours, until,
               int(_t.time())))
    c.commit()
    print("  queue %s/%s recorded under %s: at most %d workers of %g h, %g worker-hours in all%s" % (
        a.project, a.id, a.decision, a.max_workers, a.wall, a.total_hours,
        ", until %s" % a.until if a.until else ""))


# ---- the entry's owner: RUN_DONE events and the waiter
MAIN_KINDS = ("ALERT", "STALLED", "MAINTAIN", "REFUSED")   # always the main session's, whoever owns the entry


def read_owners(m, pr, rules=None):
    """{entry: {status, owner, owner_state, runs}} from the project's `plan owners` (rules.owners_cmd), or None."""
    rules = rules or rules_of(pr)
    try:
        rc, out = m._run_local(rules.get("owners_cmd") or "python3 tools/plan.py owners",
                               m.os.path.expanduser(pr["local"]), 120)
        return {x["entry"]: x for x in json.loads(out.strip().splitlines()[-1])} if not rc else None
    except (ValueError, IndexError, KeyError, OSError):
        return None


WAIT_ENDED = ("COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL",
              "DEADLINE", "REMOVED")


def _sorted_clean(c, rid):
    """A run of an earlier pass counts as clean when the supervisor sorted it so (its RUN event's note says
    "finished cleanly"), or when no RUN event records a sorting of its COMPLETED end at all."""
    rows = c.execute("SELECT ack_note FROM events WHERE kind='RUN' AND key=?", ("%s:COMPLETED" % rid,)).fetchall()
    return not rows or any("finished cleanly" in (n or "") for (n,) in rows)


def run_done_events(m, c, name, clean, owners, log=print, ended_now=None):
    """RUN_DONE only for what an owner needs (a wake per clean run end costs the main session several
    turns): one event per entry per pass, raised when a batch of a step's wait (`plan wait`, owners' "waits") closed
    this pass, listing the batch's clean runs. A batch is the wait's runs of one job script (the registry's `job`), so
    a step waiting on two batches (six 1 h probes and six 4 h arms) hears of the
    first when it ends, not hours later with the second. A batch closes this pass when all its runs have ended and one
    of them ended this pass (`clean`, or `ended_now`: every run sorted this pass, broken ones included, so a batch
    whose last run broke still names its clean ones). A clean end whose batch is still running, a run in no wait, and
    an intermediate chain job (`plan run --intermediate`, owners' "intermediate") are acked by the supervisor
    (run_end) with no event; a broken run opens its own RUN event at once (run_end). None while the entry's owner is
    working (it is on it). Returns new events."""
    import hashlib
    clean = set(clean or ())
    ended_now = set(ended_now or ()) | clean
    if not ended_now or owners is None:
        return 0
    ended = set(WAIT_ENDED) | set(getattr(m, "TERMINAL", ()))
    new = 0
    for entry, o in sorted(owners.items()):
        inter = set(o.get("intermediate") or [])
        mine = ended_now & set(o.get("runs") or [])
        if not mine:
            continue
        done, steps = set(), set()
        for w in o.get("waits") or []:
            ids = sorted(set(w.get("runs") or []))
            if not ids or not (set(ids) & mine):
                continue
            rows = c.execute("SELECT id, state, job FROM runs WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
            batches = {}
            for rid, state, job in rows.fetchall():
                batches.setdefault(job, {})[rid] = state
            for job, got in batches.items():
                if set(got) & mine and all(s_ in ended for s_ in got.values()):
                    ok = {r for r, s_ in got.items() if r not in inter and (r in clean or s_ == "COMPLETED" and
                                                                             _sorted_clean(c, r))}
                    if ok:
                        done |= ok
                        steps.add(str(w.get("step")))
        quiet = (clean & mine) - done
        if quiet:
            log("  [%s] %s: %d clean run end(s) not closing a batch of a wait (%d intermediate): acked, no RUN_DONE"
                % (name, entry, len(quiet), len(quiet & inter)))
        if not done:
            continue
        done = sorted(done)
        if o.get("owner") and o.get("owner_state") == "working":
            log("  [%s] %s: a wait on %d run(s) closed; owner %s working: no RUN_DONE" % (name, entry, len(done), o["owner"]))
            continue
        who = ("RESUME %s" % o["owner"]) if o.get("owner") and o.get("owner_state") != "closed" else "LAUNCH an owner"
        key = "runs:" + hashlib.sha1(" ".join(done).encode()).hexdigest()[:12]
        msg = "%d run(s) recorded on %s finished cleanly (step %s): %s%s; %s with them" % (
            len(done), entry, ", ".join(sorted(steps)), ", ".join(done[:8]),
            " and %d more" % (len(done) - 8) if len(done) > 8 else "", who)
        new += m.add_event(c, name, entry, "RUN_DONE", key, msg)
    return new


def sweep_done_events(m, c, name, pr, owners=None, log=print, rows=None, summary=None):
    """One RUN_DONE per step whose wait's sweeps (`step_sweeps`) have all ended (key step:N; older output without it: one per sweep)
    recorded on an entry and named by a step's wait (`plan wait`; `plan sweepends` reads
    them), naming the sweep, its evaluations and best val_loss (summary.txt, read only). Sweep trials are not in the
    run registry, so run_done_events never saw them. Once per sweep:
    an earlier RUN_DONE with the key stays, acked or not. None while the entry's owner is working."""
    cwd = m.os.path.expanduser(pr["local"])
    if rows is None:
        try:
            rc, out = m._run_local("python3 tools/plan.py sweepends", cwd, 300)
            rows = json.loads(out.strip().splitlines()[-1]) if not rc else []
        except (ValueError, IndexError, OSError):
            return 0
    new = 0
    seen = set()
    for r in rows:
        if not r.get("steps"):
            continue
        if r.get("step_sweeps") is None:       # older `plan sweepends` output: one event per sweep
            units = [("sweep:%s:%s" % (r["site"], r["name"]), None, ", ".join(r["steps"]))]
        else:
            units = [("step:%s" % s_, s_, s_) for s_ in r["steps"]]
        for key, step, steps in units:
            if (r["entry"], key) in seen or c.execute(
                    "SELECT 1 FROM events WHERE project IS ? AND item IS ? AND kind='RUN_DONE' AND key=?",
                    (name, r["entry"], key)).fetchone():
                continue
            seen.add((r["entry"], key))
            if r.get("owner") and r.get("owner_state") == "working":
                log("  [%s] %s: %s ended; owner %s working: no RUN_DONE" % (name, r["entry"], key, r["owner"]))
                continue
            pairs = [tuple(x) for x in r["step_sweeps"][step]] if step is not None else [(r["site"], r["name"])]
            parts = []
            for site_, nm in pairs:
                if summary is not None:
                    got = summary(dict(r, site=site_, name=nm))
                else:
                    try:
                        rc, out = m._run_local("python3 tools/plan.py sweepends --read %s:%s" % (site_, nm), cwd, 300)
                        got = json.loads(out.strip().splitlines()[-1]) if not rc else {}
                    except (ValueError, IndexError, OSError):
                        got = {}
                parts.append("sweep %s (%s): %s evaluations, best val_loss %s" % (
                    nm, site_, got.get("evaluations") if got.get("evaluations") is not None else "? (summary unread)",
                    got.get("best_val_loss") or "? (summary unread)"))
            who = ("RESUME %s" % r["owner"]) if r.get("owner") and r.get("owner_state") != "closed" else "LAUNCH an owner"
            msg = "recorded on %s, step %s: every sweep in its wait ended; %s; %s with it" % (
                r["entry"], steps, "; ".join(parts), who)
            new += m.add_event(c, name, r["entry"], "RUN_DONE", key, msg)
    return new


def waiter_wants(kind, item, owners):
    """Does an open event need the main session? ALERTs, stalls, upkeep and refusals always; an event of an entry
    with no owner, a stopped or closed owner, or no entry at all, yes; one whose entry's owner is working, no."""
    if kind in MAIN_KINDS or owners is None:
        return True
    o = owners.get(item)
    return not (o and o.get("owner") and o.get("owner_state") == "working")
