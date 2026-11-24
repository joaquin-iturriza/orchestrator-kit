#!/bin/bash
#SBATCH --job-name=probe
#SBATCH --cpus-per-task=1
#SBATCH --time=00:05:00
#SBATCH --output=%x_%j.out
#SBATCH --gres=gpu:1
# Infrastructure check (env activates, GPU visible), never a training run: test a new site with this.
_CCORCH_ROOT="${CCORCH_PROJECT_DIR:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)}}"
source "$_CCORCH_ROOT/sites/activate.sh"
echo "site=$CCORCH_SITE host=$(hostname) project=$PROJECT_DIR data=$DATA_DIR"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1 | head -2
python -c "import sys, numpy; print('python', sys.version.split()[0], 'numpy', numpy.__version__)"
echo RUN_DONE
