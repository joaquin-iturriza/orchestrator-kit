# Resolve the project root and this site's environment at RUNTIME. Source it from every job script instead of
# hardcoding a cluster path:
#
#     _CCORCH_ROOT="${CCORCH_PROJECT_DIR:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)}}"
#     source "$_CCORCH_ROOT/sites/activate.sh"
#
# and the same script runs unchanged at every site. CCORCH_SITE is exported by `site submit` and sniffed from the
# checkout's path otherwise. Exports PROJECT_DIR, WORK, SCRATCH, DATA_DIR, SUBMIT_DIR. Replace every <...>.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export PROJECT_DIR

if [ -z "${CCORCH_SITE:-}" ]; then
  case "$PROJECT_DIR" in
    "<your storage root on myslurm>"*) CCORCH_SITE=myslurm ;;
    "<code root on mycondor>"*)        CCORCH_SITE=mycondor ;;
    *)                                 CCORCH_SITE=local ;;
  esac
fi
export CCORCH_SITE

case "$CCORCH_SITE" in
  myslurm|mycondor)
    # module load <python module>   # if the site needs one before the venv
    [ -d "$PROJECT_DIR/.venv" ] && source "$PROJECT_DIR/.venv/bin/activate"
    export WORK="${WORK:-$(dirname "$PROJECT_DIR")}"
    export SCRATCH="${SCRATCH:-$WORK/tmp}"
    export DATA_DIR="${DATA_DIR:-$WORK/datasets}"
    export SUBMIT_DIR="${SUBMIT_DIR:-$PROJECT_DIR}"
    ;;
  *)
    [ -d "$PROJECT_DIR/.venv" ] && source "$PROJECT_DIR/.venv/bin/activate"
    export WORK="${WORK:-$PROJECT_DIR}" SCRATCH="${SCRATCH:-$PROJECT_DIR/tmp}" DATA_DIR="${DATA_DIR:-$PROJECT_DIR/data}"
    export SUBMIT_DIR="${SUBMIT_DIR:-$PROJECT_DIR}"
    ;;
esac
