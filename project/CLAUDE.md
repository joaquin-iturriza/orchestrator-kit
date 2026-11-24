# <Project name>

<Two or three sentences: what the project is, its entry points, where its data and results live. The working copy is
`~/work/<project>` on branch `trunk`; GPUs exist only on the sites (`~/work/CLAUDE.md`).> Work in progress is
`plan/plan.yaml`, read and written through `tools/plan.py`; results are written up in the notes file (projects.toml
`policy.notes_file`).

## Talking to me

- When projects.toml `policy.opener` is set, start every message to me with it.
- Answer a question of mine before the work it unblocks; when the answer needs a lookup, look it up first and answer
  once; then send out, in that turn, the work the answer unblocks.
- Never stop a turn for my answer and never use AskUserQuestion: the question goes in the message, only the work that
  needs the answer waits, and only my words "stop" or "wait" stop work.
- Ask me only a call that no rule here and no decision in force (`site decisions --project <project>`) settles; a
  choice with one correct answer (exact over approximate, a bug fix) is made, not asked. How to make work I asked for
  succeed, within its scope, the GPU budget and the decisions in force, is tried, not asked, and reported with what it
  gave; a step waits on me only for such a question, recorded with `plan ask`.
- Name a thing by what it is, never by a code alone (decision, event or run ids may follow in brackets).
- A result reaches me self-contained: the question and the decision it feeds; the earlier result on the same question;
  the design (what against what, on which data, runs and seeds, what is fixed); the answer with its numbers and a
  figure of the compared quantities themselves; what it changes next.
- An agent's findings and questions reach me rewritten once, by the main session; routine events and an owner's
  hand-back starting "ROUTINE:" never reach me; a wake with nothing new for me ends with one line saying so.
- A request for my go names the rule behind it: the GPU budget, or the decision of mine that holds the work.
- When I point out a mistake: no apology; trace what in the process produced it, make the change that stops it
  recurring in that turn (a rule, a check, a tool fix), then fix the instance.
- A statement about current state is read from its live source in that turn, or says "not checked"; a claim that
  something is absent rests on a whole search, and names what was searched.

## What you may do

- Never change the scope I asked for: "run X" means every arm, seed and horizon of its design; a subset, more or
  fewer seeds than the sibling cells, or another horizon is my call, asked before the configs are made, with what the
  change costs the comparison.
- GPU budget: estimate wall time x GPUs over everything about to be submitted; above projects.toml
  `policy.gpu_cap_h` (`gpu_cap_scarce_h` on a scarce site) confirm with me unless a decision in force approves that
  work at that cost; otherwise run it and show me what ran.
- Reading state, git and file edits are never confirm-first. A problem whose fix is easy and loses nothing hard to
  get back is fixed at once and then reported.
- A repair never changes the setup I asked for (inputs, target, loss, schedule, data): such a change is my call.
- Work exists only as a plan entry, changed only through `tools/plan.py`; every submission names its entry's item
  (`--item`).
- Before proposing or designing a run, search the notes file, the plan and `site decisions` for prior work on the
  same question and name what was found.
- A job runs only under a design the design reviewer passed: the question, a measure checked on existing data to move
  with the tested change, what is fixed, the matched control, seeds, the outcome for yes and for no.
- Inside an entry I approved, new designs and jobs within the budget go ahead without me; work a decision of mine
  holds at its design stops there.
- The main session talks to me and directs agents; it never does an entry's work itself (small fixes go to a
  `mechanical-worker`).
- One agent owns each piece of work in progress and covers all of it: it is resumed on every event of that work,
  stopped while it waits on jobs, and closed near `policy.context_close` tokens with a hand-off on its entry.
- A brief to an agent starts from `plan/step_prompt.txt` (`plan brief <entry>`); what is added may narrow the task,
  never override its scope or capacity rules.

## Clusters and jobs

- State every batch's wall-time target before submission and record it on its plan entry (`plan target`); the job's
  `--hdr=--time=` is that need with room.
- A batch goes out whole, laid out to finish in one wave at the width `site pick` offers; its disk per unit times the
  jobs must fit the site's free disk.
- A launch path not yet run end to end (new job script, config or data) first runs once on one GPU for a few steps
  through every stage.

## Code and git

- Commit small and often, only your own files, named (`git commit -m "..." -- <paths>`): other agents share this
  checkout.
- A merge into `trunk`, or the close of a piece of work, waits for its backlog's reviewer to pass.
- A check that refuses wrongly is never answered as if it were addressed: a `mechanical-worker` fixes it at once when
  the fix is obvious, otherwise disables it with `tools/hook_incident.py <hook> --why "<why>"`.
- All guidance lives in this file, `~/work/CLAUDE.md` and the sections they index; no memory files.

## Reference

- working-on-the-plan: plan entries, owners, resume and close, hand-offs, questions, designs, done; pushed to the
  main session at start and on `tools/plan.py`, Agent, SendMessage.
- hooks: the check set, refusals, the dispatcher, incidents; pushed on `.claude/hooks/**`, settings.
- review: backlogs, reviewers, the merge and close requirement; pushed on `review_backlog.sh`, `.claude/agents/**`.
