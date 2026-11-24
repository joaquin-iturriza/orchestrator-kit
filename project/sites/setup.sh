#!/bin/bash
# Make this site able to run the project. Contract (run by `site env`, checked by `site pick`), with
# sites/activate.sh sourced:
#   bash sites/setup.sh            prepare this site (idempotent): directories, data, tools the jobs need
#   bash sites/setup.sh --verify   fast, read-only: exit 0 iff runnable, one status line
# `site pick` gives a site work only when --verify passes there.
set -u

verify() {
  local miss=()
  python -c "import numpy" 2>/dev/null || miss+=("numpy in the env")
  [ -d "$DATA_DIR" ] || miss+=("$DATA_DIR")
  local probe="${SUBMIT_DIR:-$PROJECT_DIR}/.zz_probe_verify_$$"
  if touch "$probe" 2>/dev/null; then rm -f "$probe"; else miss+=("writable ${SUBMIT_DIR:-$PROJECT_DIR}"); fi
  if [ ${#miss[@]} -gt 0 ]; then echo "missing: ${miss[*]}"; return 1; fi
  echo "ok: $(python --version 2>&1), data dir $DATA_DIR"
}
[ "${1:-}" = "--verify" ] && { verify; exit $?; }

mkdir -p "$DATA_DIR" "$SCRATCH"
verify
