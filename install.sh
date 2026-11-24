#!/usr/bin/env bash
# install.sh -- set up the orchestrator kit for this user, or add its per-project layer to a project checkout.
#
#   ./install.sh [--work DIR] [--force]     user level: tools on PATH, config and reference templates (never overwrites)
#   ./install.sh --project DIR              copy the project layer (.claude/, plan/, tools/, sites/, siteconf.py,
#                                           CLAUDE.md) into the git checkout DIR, keeping every file DIR already has
#
# --force replaces an existing ~/.local/bin/site (or sibling) that is not this kit's, after backing it up.
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HOME/work"; FORCE=0; PROJECT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --work) WORK="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --project) PROJECT="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

copy_new() {   # copy_new SRC DST: only when DST does not exist
  if [ -e "$2" ]; then echo "  kept     $2 (exists)"; else mkdir -p "$(dirname "$2")"; cp "$1" "$2"; echo "  created  $2"; fi
}

if [ -n "$PROJECT" ]; then
  [ -d "$PROJECT/.git" ] || { echo "$PROJECT is not a git checkout (git init it first)" >&2; exit 2; }
  echo "project layer -> $PROJECT"
  (cd "$KIT/project" && find . -type f ! -name '*.pyc' ! -path '*/__pycache__/*') | while read -r f; do
    copy_new "$KIT/project/$f" "$PROJECT/${f#./}"
  done
  chmod +x "$PROJECT"/.claude/hooks/*.sh "$PROJECT"/.claude/hooks/*.py "$PROJECT"/tools/*.py
  [ -e "$PROJECT/plan/plan.yaml" ] || { printf 'entries: []\n' > "$PROJECT/plan/plan.yaml"; echo "  created  $PROJECT/plan/plan.yaml"; }
  git -C "$PROJECT" rev-parse --verify -q trunk >/dev/null || echo "  note: name its branch trunk (git branch -M trunk)"
  name=$(basename "$PROJECT")
  echo "Next: add [projects.$name] to ~/.config/ccorch/projects.toml (see $KIT/config/projects.example.toml),"
  echo "      fill in $PROJECT/sites/sites.yaml, sites/activate.sh and CLAUDE.md, and commit."
  exit 0
fi

python3 -c 'import sys; import yaml; sys.version_info >= (3, 11) or __import__("tomli")' 2>/dev/null || {
  echo "needs python >= 3.11 (or the tomli package) and pyyaml: pip install --user pyyaml" >&2; exit 2; }

echo "tools -> ~/.local/bin"
mkdir -p "$HOME/.local/bin"
for f in site site_gate.py site_runends.py labllm; do
  dst="$HOME/.local/bin/$f"
  if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$KIT/bin/$f" ]; then echo "  ok       $dst"; continue; fi
  if [ -e "$dst" ]; then
    if [ "$FORCE" = 1 ]; then mv "$dst" "$dst.bak-$(date +%Y%m%d-%H%M%S)"; echo "  backed up $dst"
    else echo "  REFUSED  $dst exists and is not this kit's (rerun with --force to back it up and replace it)"; continue; fi
  fi
  ln -s "$KIT/bin/$f" "$dst"; echo "  linked   $dst"
done
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "  note: add ~/.local/bin to PATH" ;; esac

echo "config -> ~/.config/ccorch"
copy_new "$KIT/config/sites.example.toml" "$HOME/.config/ccorch/sites.toml"
copy_new "$KIT/config/projects.example.toml" "$HOME/.config/ccorch/projects.toml"

echo "reference sections -> ~/.claude/context"
copy_new "$KIT/context/site-tool.txt" "$HOME/.claude/context/site-tool.txt"
copy_new "$KIT/context/ledger.txt" "$HOME/.claude/context/ledger.txt"
copy_new "$KIT/context/site-facts.example.txt" "$HOME/.claude/context/site-facts.txt"

echo "user-level hooks -> ~/.claude/hooks"
for f in dispatch.py ledger_guard.sh sleep_guard.sh; do copy_new "$KIT/user/hooks/$f" "$HOME/.claude/hooks/$f"; done
chmod +x "$HOME"/.claude/hooks/*.sh
python3 - "$HOME/.claude/settings.json" "$KIT/user/settings.hooks.json" <<'PY'
import json, os, sys
dst, src = sys.argv[1:3]
s = json.load(open(dst)) if os.path.exists(dst) else {}
if s.get("hooks"):
    print("  kept     %s (it already registers hooks: merge %s by hand)" % (dst, src))
else:
    s["hooks"] = json.load(open(src))["hooks"]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(s, open(dst, "w"), indent=2)
    print("  updated  %s (hooks registered)" % dst)
PY

echo "workspace rules -> $WORK"
copy_new "$KIT/workspace/CLAUDE.md" "$WORK/CLAUDE.md"
[ "$WORK" = "$HOME/work" ] || echo "  note: export CCORCH_WORK_ROOT=$WORK (ledger_guard.sh acts only on sessions under it)"
echo "Done. Next: edit ~/.config/ccorch/sites.toml (README: Configure a cluster), then ./install.sh --project <checkout>."
