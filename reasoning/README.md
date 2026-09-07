# reasoning

The layer between retrieval and response.

MondayOS retrieved well before this package existed and hallucinated little. What
it could not do was **conclude** anything. Retrieval hands back documents, and a
model handed documents does the only thing possible with them: summarises. Asked
"what should we build next?", a system built entirely of retrieval correctly
reports which documents it lacks — which is why Monday read as a search engine
rather than an engineer.

```
question -> retrieve -> [ understand -> reason -> recommend -> score ] -> narrate
                          ^-------------- this package --------------^
```

## The central constraint

The property that kept hallucinations low was a standing instruction never to
infer. Deleting it to gain a strategic voice would have cost the trustworthiness
that made the strategic voice worth having.

So inference is not permitted — it is **typed**. A conclusion is safe to state
when it is labelled a conclusion, carries the facts it came from, and names the
rule that produced it. A reader can then reject the rule instead of having to
trust the output. That is why `Claim` has a `kind` and a `derivation`: on a screen,
an unmarked inference and a fact look identical, and the distinction this system
rests on would vanish exactly where it matters.

## Confidence is computed, never asked for

Every score comes from arithmetic over evidence (`confidence.py`). None is
requested from a model.

A model asked to rate its own confidence produces a fluent number that tracks how
confident the *prose* sounds rather than how strong the *evidence* is. It carries
the authority of a measurement while measuring nothing, and it fails hardest in
the case that matters most: a well-written answer resting on almost nothing.

The scoring rests on one idea:

> **Corroboration across independent source kinds beats volume.**

Ten files mentioning a term are nearly one piece of evidence — the same fact seen
ten times, and often ten copies of one mistake. A source file *plus* a test that
exercises it *plus* an ADR that decided it are three independent confirmations,
because each would have had to go wrong separately.

Every score explains itself. A percentage nobody can argue with is worse than no
percentage at all.

## Modules

| Module | Responsibility |
|---|---|
| `models.py` | `Claim`, `ClaimKind`, `Confidence`, `Gap`, `Recommendation`, `Assessment` |
| `confidence.py` | Deterministic scoring from evidence |
| `facts.py` | Reads index, graph, tasks and git into structured quantities |
| `inference.py` | Named rules that turn facts into conclusions |
| `gaps.py` | Absences, expressed as proposed work |
| `recommend.py` | Ranks candidates against each other |
| `executive.py` | Routes strategic questions to the strategic register |
| `engine.py` | Orchestrates the above into an `Assessment` |

## Two registers

`GROUNDED` is the existing behaviour: answer what was asked from what was
retrieved. `EXECUTIVE` is the strategic register, entered when a question asks
what to *do* rather than what is *true*.

Routing is pattern-based, not model-based, for the same reason indexing is: it
has to be predictable. Detection is deliberately narrow — a false positive
(answering "where is X" with a memo) is worse than a false negative (returning
the grounded answer MondayOS already gave).

A grounded turn still reasons when retrieval comes back thin. That is what
replaces *"the context does not contain that"* with an answer built from what is
actually there.

## What this package does not do

- **Never calls a model.** An `Assessment` is built deterministically and handed
  to a responder to narrate. The system reasons; the model explains.
- **Never writes.** Gap analysis proposes task payloads; it creates nothing. A
  reasoning layer that silently files work into a real backlog is one people stop
  asking questions of.
- **Never widens scope.** One engine holds one project's index and graph, both
  already project-scoped, so no argument could reach a second project (ADR-017).

## Reading it

Start at `engine.py` for the flow, `models.py` for the vocabulary, and
`confidence.py` for the part that decides whether any of it can be trusted.
