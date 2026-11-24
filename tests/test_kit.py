#!/usr/bin/env python3
"""End-to-end check of the kit, offline: installs it into a throwaway HOME, adds the project layer to a copy of
examples/toy, and feeds the guards the hook inputs Claude Code would send. No cluster is contacted.

    python3 tests/test_kit.py          exit 0 when every case gives the expected verdict
"""
import json, os, shutil, subprocess, sys, tempfile

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(cmd, env, cwd=None, inp=None):
    return subprocess.run(cmd, env=env, cwd=cwd, input=inp, capture_output=True, text=True, timeout=300)


def main():
    home = tempfile.mkdtemp(prefix="kit_home_")
    toy = os.path.join(home, "work", "toy")
    env = dict(os.environ, HOME=home, PATH=os.path.join(home, ".local", "bin") + ":/usr/bin:/bin",
               CLAUDE_PROJECT_DIR=toy)
    for k in [k for k in env if k.startswith(("PLAN_", "CCORCH_"))]:
        del env[k]
    fails = []

    def expect(name, ok, detail=""):
        print("%-4s %s" % ("ok" if ok else "FAIL", name))
        if not ok:
            fails.append("%s: %s" % (name, detail[:400]))

    r = sh([os.path.join(KIT, "install.sh")], env)
    expect("user install", r.returncode == 0 and os.path.islink(os.path.join(home, ".local", "bin", "site")),
           r.stdout + r.stderr)
    r = sh([os.path.join(KIT, "install.sh")], env)
    expect("second install keeps every file", r.stdout.count("kept") == 10, r.stdout)

    shutil.copytree(os.path.join(KIT, "examples", "toy"), toy)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    sh(["git", "init", "-q", "-b", "trunk"], env, toy)
    sh(git + ["add", "-A"], env, toy)
    sh(git + ["commit", "-qm", "toy"], env, toy)
    r = sh([os.path.join(KIT, "install.sh"), "--project", toy], env)
    expect("project install", r.returncode == 0 and os.path.exists(os.path.join(toy, "plan", "plan.yaml")),
           r.stdout + r.stderr)

    r = sh([sys.executable, "-c", "import sys; sys.path.insert(0, 'tools'); import plan; "
            "print(plan.PROJECT, plan.NOTES_FILE, plan.CONTEXT_CLOSE)"], env, toy)
    expect("plan.py finds its project and policy", r.stdout.split() == ["toy", "notes.txt", "300000"], r.stdout + r.stderr)
    r = sh([sys.executable, "tools/plan.py", "next"], env, toy)
    expect("plan next on an empty plan", r.returncode == 0, r.stdout + r.stderr)
    r = sh(["site", "items"], env, toy)
    expect("site items", r.returncode == 0, r.stdout + r.stderr)

    def hook(name, inp):
        return sh([sys.executable, ".claude/hooks/dispatch.py"] + name.split(), env, toy, json.dumps(inp))

    def bash(cmd):
        return {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}

    for cmd, refused in [("sbatch job.sh", True),
                         ("site submit myslurm toy job.sh --item demo --est-gpu-hours 0.2", True),   # no site pick
                         ("site submit auto toy job.sh --item demo", True),                          # no GPU estimate
                         ("site submit auto toy job.sh --item demo --est-gpu-hours 20", False),
                         ("site submit auto toy job.sh --item demo --est-gpu-hours 40", True),       # over gpu_cap_h
                         ("site run myslurm toy -- rm -rf datasets/old", True),
                         ("site run myslurm toy -- rm -rf zz_probe_1", False),
                         ("ls -la", False)]:
        r = hook("submit_guard.py", bash(cmd))
        expect("submit_guard %s: %s" % ("refuses" if refused else "allows", cmd), (r.returncode == 2) == refused,
               r.stderr)

    plan_file = os.path.join("plan", "plan" + ".yaml")
    r = hook("plan_files_guard.py", {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Edit",
                                     "tool_input": {"file_path": plan_file, "old_string": "a", "new_string": "b"}})
    expect("plan_files_guard refuses a hand edit of the plan", r.returncode == 2, r.stderr)
    r = hook("no_blocking_question.sh", {"session_id": "s", "hook_event_name": "PreToolUse",
                                         "tool_name": "AskUserQuestion", "tool_input": {}})
    expect("no_blocking_question refuses AskUserQuestion", r.returncode == 2, r.stderr)
    r = hook("wait_guard.py", {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Monitor",
                               "tool_input": {"command": "site poll"}})
    expect("wait_guard refuses a Monitor on jobs", r.returncode == 2, r.stdout + r.stderr)
    tr = os.path.join(home, "transcript.jsonl")
    open(tr, "w").close()
    r = hook("plan_check.py", {"session_id": "s", "hook_event_name": "Stop", "transcript_path": tr,
                               "stop_hook_active": False})
    expect("plan_check runs at Stop (exit 0 or a refusal, never a crash)", r.returncode in (0, 2), r.stdout + r.stderr)
    r = sh(["bash", ".claude/hooks/review_backlog.sh", "status"], env, toy)
    expect("review_backlog status", r.returncode == 0, r.stdout + r.stderr)

    shutil.rmtree(home, ignore_errors=True)
    if fails:
        print("\n".join(["", "FAILED:"] + fails))
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
