#!/usr/bin/env bash
# PreToolUse(Bash) hook — HARD BLOCK on foreground waiting: `sleep`, `watch`,
# `tail -f`/`-F`, in a command that is not run in the background.
#
# Why: ~/work/CLAUDE.md — nobody waits on jobs: the supervisor (`site supervise`) and one
# `site events --wait` waiter wake the session when something ends. A
# foreground `sleep N; tail log` only blocks the user. The model did it anyway
# (sleep 100/110/150 while the user waited for an answer), so it is enforced here
# instead of trusted.
#
# Allowed: the same commands with run_in_background (e.g. a delayed
# check), and `sleep` inside Python/heredoc code
# (time.sleep) since only shell command positions are matched.
set -uo pipefail

input=$(cat)
{ IFS= read -r bg; IFS= read -r cmd; } < <(printf '%s' "$input" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); t=d.get("tool_input",{})
    print("1" if t.get("run_in_background") else "0")
    print(t.get("command","").replace("\n","\x1f"))
except Exception:
    print("0"); print()' 2>/dev/null)
[ "$bg" = "1" ] && exit 0
cmd=${cmd//$'\x1f'/$'\n'}
[ -z "$cmd" ] && exit 0

sep='(^|[;&|(`]|\$\(|[[:space:]](do|then|else)[[:space:]])[[:space:]]*'
# a wrapper in front is still the command: `timeout 30 tail -f /dev/null` is a disguised sleep,
# so `timeout [opts] N`, `nice`, `env`, `command`, `exec`, `stdbuf ...` are skipped before matching
wrap='((timeout([[:space:]]+-[^[:space:]]+([[:space:]]+[0-9.]+[smhd]?)?)*[[:space:]]+[0-9.]+[smhd]?|nice([[:space:]]+-n[[:space:]]*-?[0-9]+)?|env|command|exec|stdbuf([[:space:]]+-[^[:space:]]+)*)[[:space:]]+)*'
if printf '%s\n' "$cmd" | grep -qE "${sep}${wrap}(sleep|watch)[[:space:]]" \
   || printf '%s\n' "$cmd" | grep -qE "${sep}${wrap}tail[[:space:]]([^;&|]*[[:space:]])?-[a-zA-Z]*[fF]" \
   || printf '%s\n' "$cmd" | grep -qE "${sep}${wrap}read[[:space:]]([^;&|]*[[:space:]])?-[a-zA-Z]*t[[:space:]]*[0-9]"; then
  cat >&2 <<'EOF'
BLOCKED by ~/.claude/hooks/sleep_guard.sh: foreground waiting (sleep / watch / tail -f / read -t, wrapped or not).
Do not wait: answer the user / do the next thing now. If something must be waited
on, record the jobs and end the turn: the supervisor's event resumes the work.
EOF
  exit 2
fi
exit 0
