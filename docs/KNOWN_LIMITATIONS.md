# MondayOS — known limitations

Stated rather than hidden. Each entry below was found by the v1.0 acceptance
programme, adjudicated against the v1.0 product guarantees, and deliberately
carried into the release rather than fixed under release pressure.

The two guarantees these are judged against:

- **Score integrity** — MondayOS computes confidence, evidence strength and
  execution risk. The model may report, round or describe them. It may not
  create them.
- **Evidence integrity** — no unverified identifier reaches the user.

Every limitation here is either *fail-closed* (it withholds a correct answer,
and admits nothing false) or *unobserved in practice*. None permitted an
incorrect answer to reach a user in the 52-turn hosted acceptance run.

---

## D-8 — discussing handle syntax can trigger a correction

**Behaviour.** An answer that writes `[E1]` while *explaining* the citation
mechanism — rather than citing with it — is read as a citation to a handle the
turn does not have, and MondayOS regenerates the answer once.

**Direction.** Fail-closed. Cost is one extra provider call on a turn that talks
about MondayOS's own plumbing.

**Guarantee affected.** None. Evidence integrity is over-satisfied.

**Why it was not fixed.** Narrowing the handle pattern to recognise prose *about*
handles would loosen the check that closed the multi-handle citation bypass
(D-6). That is a worse trade.

**Deferred to:** v1.0.1.

---

## D-10 — ranges and bare "evidence" escape score extraction

**Behaviour.** Two shapes are not recognised as score claims:

1. A range sharing one percent sign — in `94–95%` the first number has neither
   its own `%` nor a decimal point, so the score-shaped test rejects it.
2. A bare concept word — `Strong evidence (94%)` uses "evidence" rather than
   "evidence strength", and no line-local shape matches it.

**Direction.** **Fail-open, in principle.** An invented number in either shape
would be delivered without being checked. This is the only limitation here that
is not fail-closed, and the range form can occur in the primary score-reporting
position, not only in a restatement.

**Guarantee affected.** Score integrity, at the backstop layer only. The
generation instruction and the authoritative-score supply are unaffected.

**Observed impact.** None. Across 52 hosted turns and 251 validated score
claims, this shape occurred once, and the value was one MondayOS had computed —
the model was reciting, not inventing. Zero invented scores were delivered in
the run.

**Why it was not fixed.** Score validation is a heuristic backstop behind a
computed-score architecture, not a proof. No heuristic extractor over natural
language is provably complete, and five successive refinements (D-4, D-7, D-9,
D-10, D-11) each found something narrower than the last. "The extractor has no
remaining gaps" is not an achievable release criterion and was not a v1.0
acceptance criterion.

**What contains it.** The independent audit cross-check runs on every turn and
reports any claim the product validator did not see. A future occurrence
surfaces as an integrity finding rather than passing silently.

**Deferred to:** v1.0.1.

---

## D-11 — a ratio inside a scored block reads as a score

**Behaviour.** Under a heading that establishes a score concept, a number that is
not a score inherits it. Observed: *"16 source files and 2 test files yield a
0.12 ratio"* was read as an evidence strength of 0.12.

**Direction.** Fail-closed. It can cause a correct answer to be withheld.

**Guarantee affected.** None.

**Observed impact.** Once in 52 turns, with no standalone consequence — that turn
failed closed on a genuine invented score regardless.

**Deferred to:** v1.0.1.

---

## D-12 — real `Class.method` citations are refused

**Behaviour.** Dotted symbol references are not resolved by the evidence
authority, so a citation that is correct is treated as unverifiable and the
answer is refused.

**Direction.** Fail-closed.

**Guarantee affected.** None. Evidence integrity is *"no unverified identifier
reaches the user"*; this over-satisfies it.

**Observed impact.** **The largest of the four: 3 of 52 turns (5.8%)** lost a
correct answer. Verified examples, all of which exist in the source:

| Refused | Actually at |
|---|---|
| `TestCaseInventory.test_every_known_failure_says_why` | `tests/test_benchmark.py:65, :89` |
| `TestPathValidation.test_a_rejection_says_which_part_to_change` | `tests/test_identity.py:98, :122` |
| `TestPlanner.test_a_plan_without_recommendations_says_so` | `tests/test_growth_generation.py:261, :301` |
| `WorkspaceIncrement2RouteTests.test_briefing_with_nothing_recorded_says_so` | `tests/test_dashboard_api.py:446, :559` |
| `RecordedData.forecast_asof` | `recorded_data.py:37, :130` (WeatherBot) |

**Why it was not fixed.** Resolving dotted symbols means loosening a citation
check, and it was found on the eve of the release run. Loosening a citation check
under time pressure is how the multi-handle bypass got in.

**Deferred to:** v1.0.1. This is the first item in the backlog.

---

## Provider and environment limitations

- **Anthropic: `connect_timeout` is not independently enforced.** The Anthropic
  SDK owns its transport and vendors it as `httpx2`. MondayOS passes a plain
  numeric timeout — the derived generation deadline — rather than importing a
  private vendored module to construct a two-phase timeout object. Connecting and
  generating therefore share one deadline on this provider. Ollama and OpenAI
  honour both phases.
- **Ollama does not stream and reports no stop reason.** A local Ollama
  configuration cannot exercise the streaming delivery path or the
  truncation-reporting guarantee at all.
- **TypeScript symbols are regex-extracted, not parsed**; test→code links use
  naming convention, not import analysis; the credential filter is conservative
  and skips legitimately-named files. See `docs/AI_WORKSPACE.md` §7.
