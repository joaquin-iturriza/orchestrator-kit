#!/bin/bash
#SBATCH --job-name=toy
#SBATCH --cpus-per-task=2
#SBATCH --time=00:10:00
#SBATCH --output=%x_%j.out
#SBATCH --gres=gpu:1
# The job's own business only (name, time, CPUs, GPUs, output); the site's partition, account and flags are added by
# `site submit` from ~/.config/ccorch/sites.toml and sites/sites.yaml.
_CCORCH_ROOT="${CCORCH_PROJECT_DIR:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}}"
source "$_CCORCH_ROOT/sites/activate.sh"
cd "$PROJECT_DIR"
python train.py --steps "${STEPS:-500}" --seed "${SEED:-0}" --out "runs/toy_${SLURM_JOB_ID:-local}"   # under the checkout: site fetch looks there
