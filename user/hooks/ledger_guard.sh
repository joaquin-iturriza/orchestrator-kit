#!/usr/bin/env bash
# Stop and SessionStart hook for every project (registered in ~/.claude/settings.json): the decisions and work-item
# registers of the `site` tool (~/work/CLAUDE.md, "Decisions and work items").
#   stop           a turn cannot end while the session's project has an open event, active work and no supervisor,
#                  or no armed waiter (`site guard --stop`)
#   session-start  prints the decisions in force and the items into the new context, so they survive compaction
# It fails closed: if `site guard` itself breaks, the turn is stopped and says so (a guard that exits on its own
# error is a guard that is off).
mode=${1:-stop}
cat >/dev/null
dir="${CLAUDE_PROJECT_DIR:-$PWD}"
# only the research checkouts have projects in ~/.config/ccorch/projects.toml: a session anywhere else
# is never touched, so a broken `site` cannot block it
case "$(realpath -m "$dir")/" in "${CCORCH_WORK_ROOT:-$HOME/work}"/*) ;; *) exit 0 ;; esac
# a project whose own Stop check runs `site guard --stop` itself (the kit's plan_check.py: it carries
# the marker below) is not asked twice; its session-start part stays here
if [ "$mode" = "stop" ] && grep -qF 'LEDGER_MARK: ledger: site guard --stop' "$dir/.claude/hooks/plan_check.py" 2>/dev/null; then
  exit 0
fi
out=$(site guard --"$mode" --project-dir "$dir" 2>&1)
rc=$?
if [ "$mode" = "session-start" ]; then
  printf '%s\n' "$out"
  exit 0
fi
case $rc in
  0) exit 0 ;;
  2) printf '%s\n' "$out" >&2; exit 2 ;;
  *) printf 'site ledger guard failed (exit %s); fix it before ending the turn:\n%s\n' "$rc" "$out" >&2; exit 2 ;;
esac
