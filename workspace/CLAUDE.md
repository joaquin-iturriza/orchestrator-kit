# ~/work  (install.sh copies this to ~/work/CLAUDE.md when none exists; edit it to your taste)

Every directory here is a git checkout of one research project, worked on locally. The clusters are interchangeable
GPU sites reached only through the `site` tool (`~/.config/ccorch/sites.toml`). The user's decisions and the work they
authorise live in its ledger (`site decisions`, `site items`), and `site supervise` watches every project's jobs.

## Talking to the user

- Talk only when there is something to say: a finding or a call for the user comes in four parts, in order: new
  findings with numbers, deviations from what was asked, what is stuck and why, what needs the user; jobs finishing
  are not news.
- When the user comes back after a long absence, start from `site digest`.
- A hold or release names the items it covers: say that exact list to the user in the same message.

## What you may do

- A decision is recorded only in the user's exact words, the whole sentence, never a fragment of it (`site decide`).
- No work without an active item: `site submit`, `site copy` and any `site run` that starts work name it (`--item`;
  `'*'` for upkeep).
- Use every site that is up; a batch is never pinned to one site while another can take it. A site marked
  `budget = "scarce"` in sites.toml takes work only with `--allow-scarce`, when the user asked for it.
- Never delete on a cluster what is costly to get back (runs, checkpoints, hard-to-regenerate data, results) or what
  our work did not make; our own cheap-to-rebuild debris (a duplicate cache, `zz_probe_*`) is deleted when in the way,
  and reported; results are pruned only by a project's pruning tool (projects.toml `policy.prune_tools`) under the
  user's recorded decision.
- Commits are the user's (`git config user.name/user.email` of each repo); add no attribution lines the user did not ask for.

## Clusters and jobs

- Never touch a cluster by hand (no ssh, sshfs, edits there, scp or rsync between sites, ssh pipes): code moves by
  `site sync`, jobs by `site submit`, state by `site poll|logs|fetch`, data by `site copy`, site to site.
- Placement is `site pick`'s: plan a batch with `site pick <project> --jobs N [--groups G ...]` and submit only what
  it allots (`site submit auto` for a single job); data on one site only is a setup gap to fix.
- Login nodes have no GPU: `site run` is for sweep generators, submitters and CPU dry runs, never training, CUDA code
  or a shell loop.
- Never hardcode a path: Python uses `siteconf`, job scripts source `sites/activate.sh`; a job script holds only the
  job's business (name, time, CPUs, GPUs, output), and the site's directives come at submit time.
- Nobody waits on jobs, in the foreground or the background: the watch is `site supervise` and one
  `site events --wait`, run by the main session; an agent records its jobs and ends its turn.
- An event whose fix means reading a log goes to the agent that owns its work, or a worker when there is none, which
  reads the log before anything is resubmitted, fixes it and acks it with what was done (`site ack`); any other event
  is explained and acked by whoever it reaches.
- Verify before claiming: a run's state is read in that turn from `site runs` and its log (`site logs`); RUNNING with
  an empty log is not working. A site that does not answer is retried once (`site list`, or
  `site run <site> <project> -- hostname`) before it is reported down.

## Code and git

- One branch per repo: `trunk`.

## Reference

- site-tool: the `site` verbs, `site pick`'s plan, where results are; pushed on any `site` command.
- ledger: decisions, items, events, the supervisor and how it sorts run ends; pushed on `site decide|undecide|
  decisions|item|items|events|ack|supervise|digest`.
- site-facts: each cluster's flags and quirks; pushed on a command naming a site or a scheduler.
