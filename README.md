# orchestrator-kit

Run a research project from one Claude Code session that acts as an **orchestrator**: it talks to you, records your
decisions, and hands each piece of work to a subagent that owns it. The owners design jobs, get the design reviewed,
and submit them to whichever of your GPU clusters can finish them first. A background supervisor watches the jobs on
every cluster and wakes the session only when something needs attention.

This kit was pulled out of a working setup that has run a physics ML project for months over three clusters (two
Slurm, one HTCondor). It is the general machinery; your project, clusters and rules go in config files.

## Architecture

```
 you ──► MAIN SESSION (Claude Code in ~/work/<project>)
           │  talks to you, records your words as decisions, directs agents; never does an entry's work itself
           │
           ├──► OWNER agents   one per plan entry: design → review → submit → read results → write up → hand off
           ├──► WORKERS        cheaper model, mechanical steps only (resubmit, read a log and ack, small fixes)
           └──► REVIEWERS      design-reviewer (before any job), repo-reviewer (code), claudemd-keeper (rules)
           │
   plan/plan.yaml  ◄── tools/plan.py   entries, owners, designs, waits, questions to you, hand-offs
   LEDGER (~/.local/share/ccorch/runs.db) ◄── `site`   your decisions (verbatim), work items, runs, events
           │
   `site` ──ssh──► clusters    sync (git), pick (placement), submit, poll, logs, fetch, copy (site to site)
           │                    every sbatch/condor_submit there passes site_gate.py (work item + GPU budget)
           │
   `site supervise` (background)  polls runs, sorts each run end (clean / planned limit / broke), runs upkeep,
   `site events --wait` (waiter)  raises events; the waiter wakes the main session, which resumes the owner
```

The rules the agents follow live in two CLAUDE.md files (workspace and project) and in short reference sections
pushed into an agent's context only when it touches their topic. **Hooks** turn the rules that matter into checks:
a submission without placement, design review, work item or GPU budget is refused; a hand edit of the plan is
refused; nobody waits on jobs in the foreground; a turn cannot end while work it could start is idle.

Life of a piece of work:

1. You say what you want. The main session records it with your exact words (`site decide`), which approves a work
   item, and opens a plan entry (`tools/plan.py add`).
2. It launches an owner with a brief built from `plan/step_prompt.txt` (`tools/plan.py brief <entry>`).
3. The owner records a design (question, prior work, measure, control, seeds, outcome) and launches the
   design-reviewer; only on PASS can its jobs go out.
4. `site pick <project> --jobs N` measures every cluster (free GPUs, queue ahead, your caps, how long your jobs
   really waited there) and plans the batch to finish earliest; `site submit` sends exactly what it allots.
5. The owner records its runs, says what it waits on (`plan wait`) and ends its turn. Nothing polls.
6. The supervisor sorts each run end from its log. Clean ends are acked silently; a broken run becomes an event that
   resumes its owner, who reads the log before resubmitting.
7. The owner writes the result up, the reviewers pass the backlog, and the entry is closed (`plan done`).

## What is in this repo

| Path | What | Core / adapt |
|---|---|---|
| `bin/site` | the multi-cluster tool and the ledger (one Python file) | core |
| `bin/site_gate.py` | the submission gate the tool installs on each site | core |
| `bin/site_runends.py` | sorts run ends without a model | core |
| `bin/labllm` | client for an OpenAI-compatible LLM (a lab server) for cheap routed work | core, optional |
| `config/*.example.toml` | sites (clusters) and projects, with the **policies** | fill in |
| `context/` | reference sections: site tool, ledger, an example of per-site facts | core; site facts are yours |
| `user/` | user-level hooks: decisions printed into every session, no foreground waiting | core |
| `workspace/CLAUDE.md` | rules for every project under `~/work` | core, edit to taste |
| `project/` | per-project layer: `.claude/` hooks, agents, settings; `plan/` brief templates; `tools/plan.py`; `sites/` + `siteconf.py`; a `CLAUDE.md` template | core; fill in `sites/` and `CLAUDE.md` |
| `examples/toy/` | a toy project with one job | example |
| `tests/test_kit.py` | installs everything into a temporary HOME and checks the guards | test |

Policies are settings, in `~/.config/ccorch/projects.toml` (`[defaults.policy]`, overridden per project): GPU-hour
caps per work item (`gpu_cap_h`, `gpu_cap_scarce_h`), the context size at which an owner hands off
(`context_close`), the notes file results are written to, an opener word for replies, which paths may not be deleted
by hand on a site and which pruning tools may (`data_paths`, `prune_tools`), and Stop checks switched off
(`checks_off`). A site marked `budget = "scarce"` in `sites.toml` gets no work unless asked (`--allow-scarce`).

## Install

Needs Linux or WSL, Python 3.11+ (or `tomli`) with `pyyaml` locally, git, ssh, and Claude Code.

```
git clone <this repo> ~/orchestrator-kit
~/orchestrator-kit/install.sh          # --work DIR if your projects are not under ~/work
```

It links `site`, `site_gate.py`, `site_runends.py` and `labllm` into `~/.local/bin`, and copies the config templates
to `~/.config/ccorch/`, the reference sections to `~/.claude/context/`, the user hooks to `~/.claude/hooks/` and the
workspace rules to `~/work/CLAUDE.md`. It never overwrites a file: an existing different `site` is refused unless you
pass `--force` (which backs it up), and a `~/.claude/settings.json` that already registers hooks is left for you to
merge with `user/settings.hooks.json`. Check it with `python3 tests/test_kit.py`.

## Configure a cluster

1. **ssh.** Give the cluster an alias in `~/.ssh/config` with key authentication and connection sharing, e.g.
   `Host mycluster` / `HostName <login host>` / `User <you>` / `ControlMaster auto` / `ControlPath ~/.ssh/cm-%r@%h:%p` /
   `ControlPersist 4h`. For a two-factor site, open the master yourself (`ssh -fN mycluster`); the tool never prompts.
2. **`~/.config/ccorch/sites.toml`.** One `[sites.<name>]` block per cluster: `ssh` alias, `root` (your storage
   there), `scheduler` (`slurm` or `condor`), `account`, `partition`, `gpu_flag`, and the quirks the comments
   describe (`mem` where it is mandatory, `budget = "scarce"` for limited hours, `login_cap` where login nodes take
   no scripts). Set `[defaults] relay` to an always-on host that can reach every site, for `site copy`.
3. **Per project**, `sites/sites.yaml` (paths and scheduler settings of the project on each site) and
   `sites/activate.sh` (how a job finds its environment there). These are the only files that name a cluster; job
   scripts carry only name, time, CPUs, GPUs and output.
4. Check: `site list` (is it up), `site env <site> <project>` (checkout, Python env, `sites/setup.sh`),
   `site pick <project> --jobs 1` (does it get work), then submit `scripts/job_probe.sh` from the toy.

Notes for specific clusters you share with the author are in `context/site-facts.example.txt` (installed as
`~/.claude/context/site-facts.txt` when you have none): keep the lines that apply, add your own.

## Run the example

```
cp -r ~/orchestrator-kit/examples/toy ~/work/toy && cd ~/work/toy
git init -b trunk && git add -A && git commit -m "toy"
git remote add origin <a git remote the clusters can pull from>     # site sync pushes trunk there
~/orchestrator-kit/install.sh --project ~/work/toy
```

Then fill in `[projects.toy]` in `~/.config/ccorch/projects.toml` (`repo`, `sites`), the `<...>` in
`sites/sites.yaml` and `sites/activate.sh`, and the project description in `CLAUDE.md`; commit. Prepare a site with
`site env <site> toy`.

Start Claude Code in `~/work/toy` and say, for example: *"Run the toy fit once on the cluster, seed 0, 500 steps."*
The main session should record your sentence as a decision, add a plan entry, start the supervisor
(`site supervise --project toy`) and the waiter (`site events --wait`) in the background, and launch an owner. The
owner designs the run, has it reviewed, plans it with `site pick toy --jobs 1`, submits `job.sh`, records the run and
stops. When the job prints `RUN_DONE`, the supervisor sorts it clean; the owner reads `metrics.json`
(`site fetch <run>`), writes the result under `\label{sec:toy-fit}` in `notes.txt` and closes the entry.

By hand, the same steps are:

```
site decide toy --kind approve --quote "<your exact words, as typed in the session>"   # checked against transcripts
python3 tools/plan.py add toy_fit --title "toy fit" --question "does the fit recover y=3x+1?" \
    --next "run job.sh once" --match 'toy*' --decision D1
site pick toy --jobs 1
site submit auto toy job.sh --item toy_fit --est-gpu-hours 0.2 --hdr=--time=00:10:00
site runs --project toy;  site logs <run>;  site fetch <run>
```

## Status and limits

- Extracted from one person's setup and tested here only offline (`tests/test_kit.py`: install, plan CLI, guard
  verdicts). The cluster paths (`sync`, `pick`, `submit`, `supervise`) are the code that runs in production, but this
  copy has not yet been run against a cluster on someone else's account.
- `bin/site` is one large file; splitting it into modules is welcome.
- The workflow assumes one development branch per project, `trunk`, which every site checks out.
- The kit keeps its state on each site under `~/.ccorch`; set `CCORCH_REMOTE_DIR` (e.g. `.ccorch_kit`) to keep a
  second installation on the same cluster account apart from the first. On a site with `login_cap`, `site env` runs
  its setup as a CPU job: run the same command again to read its result.
