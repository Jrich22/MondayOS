# initiatives

The product layer above the repository.

MondayOS modelled artefacts: files, tasks, commits, decisions, tests. Those are
what engineers touch. None of them is what a product is made of, and no amount of
reasoning over them answers **"how is Billing going?"** — because Billing is not
a file, and the question is not about any of its parts.

An initiative is the missing noun: a business capability, assembled from the work
that builds it.

## Declared and derived

Discovery reads what the project already states about itself — never similarity,
never clustering, for the same reason the index rejects embeddings. A grouping
nobody can explain is a grouping nobody can correct, and the first time Monday
files something under the wrong capability the user needs to know *why* in order
to fix it. Every member therefore carries its reason.

| Source | Signal | Authority |
|---|---|---|
| Declaration | a human named it in `config/initiatives.json` | highest |
| Task prefixes | `Cue App: Roll Call` names its capability | high |
| Documents | `docs/AI_WORKSPACE.md` is somebody deciding this deserved a document | medium |
| Layout | a substantial directory, or a name recurring across several architectural layers | low |

Seeds from different sources merge — `workspace/` and `AI_WORKSPACE.md` are one
initiative seen twice.

**Evidence decides existence; documents only decide wording.** A capability needs
concrete work behind it: a directory, a co-occurring name, or a task prefix. A
document may corroborate one and may improve its name, but it cannot create one.
Letting the highest authority win outright is how `research/` came to be reported
as "Research Roadmap" and `safety/` as "Safety Implementation Plan" — real code,
named after plans written about it. So a document may contribute at most one
qualifier, and never a word describing a document or a process. Whichever
document supplies the name is the document the evidence cites.

What counts as "substantial", and where a capability can be found at all, is read
from the repository's shape rather than assumed — see `layout.py`. The previous
rule wanted ten source files in a top-level directory, which described this
repository and almost nothing else: projects keeping their source under `src/`
reported one capability called "src".

**A declared initiative may have nothing in it, and that is the point.**
Discovery can only see what exists, which is a hard ceiling on roadmap reasoning:
a capability agreed in planning and never started is invisible to every signal in
the repository. "We committed to Billing and have written nothing" is a sentence a
repository-derived system cannot say, and it is often the most important one in
the room.

## Precision over recall

A missing member is a gap a user can point out. A wrong member is Monday
confidently misdescribing their product, and it corrupts every number computed
from it. So the filters are deliberately aggressive:

- a filed record (`TASK-0051.md`, `DEC-0001.md`) is one unit of work inside
  something, not a something — recognised by the shape of its name and by living
  in a store, never by which directories this repository files things in
- a directory with no source in it is not a capability, whatever it is called
- an initiative whose only member is its own markdown file is a document
- a container (`src/`, `app/`) holds capabilities rather than being one
- a transport (`dashboard_api`) folds into what it serves, by the ordinary
  token-subset merge rather than by a rule of its own
- the project's own namespace (`monday/`) is the product, not a part of it

Without these, MondayOS reported 57 initiatives including "Runbook" and
"Task 0051". With them the roster means something.

## Progress refuses to invent a denominator

With tasks, completed-over-total is a real ratio. Without them there is nothing
honest to divide — counting files would produce a number that moves when someone
splits a module in two — so an initiative with no tasks reports **maturity
signals** (code, tests, docs, decision) instead of a percentage.

A missing number with a stated basis beats a confident wrong one.

## Health leads on blockers, not progress

An initiative at 80% with a blocked task needs attention more than one at 20%
moving steadily. A status ordered by percentage buries exactly the thing worth
seeing, so the bands answer *"should I worry"* rather than *"how much is done"*.

`BLOCKED` · `AT_RISK` · `STALLED` · `HEALTHY` · `NOT_STARTED`

## Dependencies are stated, never inferred

Two initiatives depend on each other when they share an artefact — a fact. Nothing
is derived from similarity: an invented dependency is a constraint the project
never agreed to, and it would be acted on as though it had.

## Membership is implementation, not adjacency

Members are gathered by path prefix, which is right for code and wrong for a
store. MondayOS keeps its task records in `tasks/` beside the six modules that
manage them, so `Task System` once reported 89 members — 79 of them tasks it
tracks rather than code implementing it. Because the roster sorts by member
count and health and progress are computed from the same list, the capability
that owned the largest store became the largest thing in the project.

So code and tests are always members, and everything else is a member only if it
is not a filed record. Tasks and decisions still reach an initiative as
structured evidence; they just do not count as implementation surface.
