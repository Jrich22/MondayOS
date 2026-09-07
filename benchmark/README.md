# benchmark

A repeatable, provider-free measurement of MondayOS against four real projects.

It exists so that a change to retrieval or discovery can be **argued about**.
Before it, "initiative discovery got better" was a claim. Now it is a diff
against a committed baseline, measured the same way on every corpus.

## What it does not measure

**A green benchmark does not mean MondayOS gives good advice.**

This is the most important line in this document. The harness measures dimensions
with a right answer that needs no judgement — which register a question routed
to, whether an answer reached grounded evidence, where its citations point, which
initiatives were discovered, whether one project's evidence contains another's
work. All of that can be perfect while the reasoning on top of it is useless.

There is deliberately **no automated "recommendation usefulness" score**. It is a
real dimension and it is not mechanical; turning it into a number would make it
look objective while measuring nothing. That stays a human-review step, and the
live conversational battery in S5 is where it belongs.

## Running it

```bash
python -m benchmark            # measure, compare against the baseline, exit 1 on failure
python -m benchmark --record   # re-record the baseline after an intended change
```

Roughly 1.5 s cold, 0.4 s warm. No network, no API key, no model.

## Assertions vs observations

The split is the design.

**Assertions are invariants.** A violation fails the run outright — no threshold,
no tolerance, because these are properties MondayOS either has or does not.

| Assertion | Meaning |
|---|---|
| `leaked_commits` | Evidence from another project reached this one |
| `grounded_false_positives` | A code lookup was answered in a strategic register |
| `citations_outside_root` | A citation points outside the project |

**Observations are measurements.** They move for legitimate reasons, so they are
compared against the committed baseline rather than an absolute: routing
accuracy, retrieval grounded rate, citation navigability, the ordered initiative
set, and the individual known-failing cases.

**Volatile values are recorded and never compared** — file counts, symbol counts,
timings. A benchmark that fails whenever someone adds a file is one people learn
to route around.

## Re-recording the baseline

An **improvement fails the run**, on purpose:

```
Benchmark improved; committed baseline is stale.
Review the change and re-record intentionally.
```

A baseline that silently absorbs good news stops describing the system and stops
protecting it. So when a stabilization increment lands, the sequence is: read the
diff, confirm the change is the one intended, then `--record` and commit the new
baseline alongside the change that caused it.

## Known failures are declared, not hidden

A benchmark whose cases all pass is evidence the questions were chosen to pass.
Today's weaknesses are declared as cases with a stated reason, measured like any
other, and recorded **individually** rather than folded into a rate — S3 and S4
need to show exactly which failures disappeared.

Currently recorded:

- `retrieval.why-decision` — decision retrieval matches ADR titles, not bodies.
  Passes on MondayOS and sourcingBOT, fails on Cue App and WeatherBot, so the
  weakness is corpus-dependent rather than absolute.
- `routing.strategic.{blocking-us, how-healthy, refactor-or-ship,
  highest-leverage, say-more}` — five natural strategic phrasings that route
  grounded. All five are **false negatives**; false positives remain zero, which
  is the failure direction the router was designed to prefer.
- Cue App discovers exactly one initiative, named `src`. That is the S3 problem
  recorded rather than hidden.

## Limitations of this corpus set

Worth stating plainly, because the number will be read as more general than it is.

- **Four projects is a small sample**, and two of them — Cue App and sourcingBOT —
  live *inside* MondayOS's own git repository. WeatherBot is the only true
  standalone. Passing here does not prove MondayOS works on an arbitrary repo.
- **Two layouts, not many.** Flat top-level packages (MondayOS, WeatherBot) and
  `src/`-style (Cue App, sourcingBOT). Monorepos, polyglot repos and
  non-Python-first projects are unrepresented.
- **The corpora are our own projects**, so their conventions are the conventions
  MondayOS was built against. That is exactly the overfitting the benchmark was
  built to expose, and it cannot fully escape it.
- **Corpus questions are hand-written.** They name a symbol and a topic each
  project genuinely contains, but they were chosen by the same person who wrote
  the retrieval.

## Adding a corpus

Add an entry to `CORPUS_QUESTIONS` in `corpus.py` with a `symbol` the project
genuinely defines and a `topic` its documentation genuinely covers, register the
project in `config/projects.json`, then `--record`. Nothing else needs to change:
scoring never branches on which project it is looking at, and a test greps for
that to keep it true.

A project that is not checked out is skipped with a stated reason rather than
failing the run.

## Provider-free by construction

`guard.py` walks the benchmark's imports **transitively** and refuses to run if
any of them can reach `brain`, `monday`, `workspace`, or a vendor SDK. A single
accidental import would put a network round-trip in the call path and turn a
repeatable number into a sampled one — while still looking like it worked.

The reachable set is `core`, `intelligence`, `initiatives`, `reasoning`.
