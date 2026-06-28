# MondayOS Reasoning Engine

**Specification v1.0 — Sprint 1.4**

---

## Overview

The MondayOS Reasoning Engine is the implementation of `Monday.ask()`. It answers engineering questions using only knowledge already stored inside MondayOS — no external model calls, no network I/O, no API keys.

The engine lives in `brain/reasoner.py` and is composed into `Monday` via the `ReasoningEngine` class.

```python
from monday import Monday

monday = Monday()

monday.ask("Have we seen Homebrew PATH issues before?")
monday.ask("Summarize everything we know about Weather observations.")
monday.ask("Show all ADRs related to search.")
monday.ask("What is currently blocked?")
```

---

## Architecture

```
monday.ask(prompt)
     │
     ▼
ReasoningEngine.answer(prompt)
     │
     ├─ 1. _classify_intent(prompt)     → QuestionIntent enum
     │
     ├─ 2. _extract_terms(prompt)       → list[str] (stop-word stripped)
     │
     ├─ 3. _search_knowledge(...)       → list[KnowledgeEntry]
     │         └─ KnowledgeStore.search()
     │         └─ KnowledgeStore.list_all()  (RECENT_CHANGES only)
     │
     ├─ 4. _search_tasks(...)           → list[dict]
     │         └─ TaskManager.list_active()
     │
     ├─ 5. _traverse_relationships(...) → list[KnowledgeEntry]  (depth=1)
     │         └─ KnowledgeStore.get()  per relationship target
     │
     ├─ 6. Partition entries:
     │         supporting_entries = non-DECISION entries
     │         related_decisions  = DECISION entries only
     │
     ├─ 7. _synthesize(...)             → answer: str
     │
     ├─ 8. _suggest_actions(...)        → list[str]
     │
     └─ 9. _calculate_confidence(...)  → float (0.0 – 0.95)
```

---

## Question Classification

Intent is determined by pattern-matching the lowercase prompt against keyword sets. Longer, more specific patterns are checked first to prevent false positives.

| Intent            | Trigger keywords                                              |
|-------------------|---------------------------------------------------------------|
| `BLOCKED_TASKS`   | "block", "blocked"                                            |
| `RECENT_CHANGES`  | "recent", "changed recently", "what changed", "what's new"   |
| `SUMMARY`         | "summarize", "summary", "everything we know", "what do we know" |
| `ONBOARDING`      | "read first", "where to start", "understand", "onboard"       |
| `HISTORICAL`      | "have we seen", "seen before", "before?", "seen this"         |
| `TYPE_DECISION`   | "adr", "decision", "decisions", "architectural"               |
| `TYPE_BUG`        | "bug", "bugs", "issue", "error", "failure"                   |
| `TYPE_TASK`       | "task", "tasks", "ticket"                                    |
| `GENERAL`         | (default — all other prompts)                                |

Each intent drives a different search strategy, answer template, and action set.

---

## Term Extraction

Meaningful words are extracted after:
1. Lowercasing the prompt.
2. Replacing all non-alphanumeric characters with spaces.
3. Removing words in the stop-word set (common English words plus MondayOS question words: "summarize", "show", "related", "tell", etc.).
4. Discarding tokens shorter than 3 characters.

Example:
```
Input:  "Have we seen Homebrew PATH issues before?"
Output: ["seen", "homebrew", "path", "issues"]
```

These terms become the search queries passed to `KnowledgeStore.search()`.

---

## Search Strategy

### Knowledge search

All intents except `BLOCKED_TASKS` search the knowledge store.

1. **Multi-word search first**: the extracted terms are joined and submitted as one query to capture multi-word matches (e.g., "homebrew path" scores higher than independent term searches).
2. **Term-by-term supplement**: if fewer than 3 results return from the multi-word search, each term is searched individually and unique results are appended.
3. **Type filter**: `TYPE_BUG` filters to `entry_type=BUG`; `TYPE_DECISION` filters to `entry_type=DECISION`. Other intents return results across all types.
4. **Limit**: 20 entries maximum from the initial search; trimmed to 10 after type filtering.

`RECENT_CHANGES` bypasses term search entirely — it calls `list_all()` and sorts descending by `updated_at` (falling back to `created_at`).

### Task search

| Intent           | What is searched                                    |
|------------------|-----------------------------------------------------|
| `BLOCKED_TASKS`  | `TaskManager.list_active(status=BLOCKED)`           |
| `TYPE_TASK`      | All active tasks matching extracted terms           |
| `RECENT_CHANGES` | All active tasks (terms filter if present)          |
| `GENERAL`        | Active tasks whose title or objective contains a term |
| Others           | No task search                                      |

---

## Relationship Traversal

After the initial search, the top 3 knowledge entries have their `relationships` list traversed:

```
for entry in top_3:
    for rel in entry.relationships:
        target = KnowledgeStore.get(rel.target_id)  # may raise if not found
        if target not in already_seen:
            add to knowledge_hits
```

This is a **depth-1 BFS** (one hop). Missing targets are silently skipped (the store may not yet hold all referenced entries).

The traversal enriches results with contextually related entries that would not appear from keyword search alone — for example, the ADR that *caused* a bug, or the pattern that *resolved* it.

**Future work**: depth-2 traversal for graph-connected topics (requires Neo4j or SQLite backend to avoid O(n²) file reads).

---

## Ranking Strategy

Within each search result set, ranking is delegated to `KnowledgeStore.search()`, which uses a per-entry keyword score:

| Match location | Score per term |
|----------------|---------------|
| Title          | +3.0           |
| Tag            | +2.0           |
| Summary        | +1.0           |
| Body           | +0.5           |

Results are returned sorted descending by total score. The reasoner does not re-rank; it trusts the store's ordering and applies type filters on top.

Traversed entries (from relationships) are appended at the end — they are supplemental context, not primary matches.

---

## Confidence Calculation

Confidence is a scalar in `[0.0, 0.95]` computed from four additive signals:

```
confidence = min(base + summary_bonus + alignment_bonus + rel_bonus, 0.95)
```

| Signal           | Formula                                                    | Max   |
|------------------|------------------------------------------------------------|-------|
| `base`           | `min(total_results × 0.10, 0.70)` — more results = higher | 0.70  |
| `summary_bonus`  | `0.04 × entries_with_nonempty_summary` (top 3 only)        | 0.12  |
| `alignment_bonus`| `0.05 × entries_matching_intent_type` (TYPE_BUG/DECISION)  | 0.15  |
| `rel_bonus`      | `0.02 × total_relationships` on top 3 entries              | 0.10  |
| **Hard cap**     | `min(raw, 0.95)` — never claim full certainty              | 0.95  |

The hard cap at 0.95 reflects a deliberate design choice: without an LLM to validate the synthesized answer against the source material, claiming certainty would be misleading. The 0.05 gap signals that LLM grounding is still available to add.

---

## Answer Synthesis

Answers are built from intent-specific templates that incorporate topic, entry count, and top entry content:

| Intent           | Template summary                                               |
|------------------|----------------------------------------------------------------|
| `HISTORICAL`     | "Yes — N entries found. Most relevant: [ID] Title\nSummary"   |
| `SUMMARY`        | "Found N entries about 'topic':\n• list\n\nTop entry: ..."    |
| `TYPE_DECISION`  | "Found N decision(s) related to 'topic':\n• list"             |
| `TYPE_BUG`       | "Found N bug entries related to 'topic':\n• list"             |
| `TYPE_TASK`      | "Found N task(s) related to 'topic':\n• list [status]"        |
| `BLOCKED_TASKS`  | "N blocked task(s):\n• TASK-ID: Title"                        |
| `RECENT_CHANGES` | "Recent knowledge base activity (N entries):\n• list"         |
| `ONBOARDING`     | "Suggested reading order for 'topic':\n1. ... 2. ..."         |
| `GENERAL`        | "Found N entries ... Top result: [ID] Title\nSummary"         |
| (no results)     | "No results found for 'topic' in MondayOS."                   |

For `ONBOARDING`, entries are sorted by relationship count (descending) before listing — the most-connected entries are presumed to be the highest-leverage starting points.

---

## Suggested Next Actions

Each response includes up to 5 actionable follow-ups:

- **No results found**: `monday.learn(...)` to capture missing knowledge
- **Blocked tasks**: `monday.task('update_status', ...)` to unblock each
- **Results found (general)**: `monday.search('{id}')` for the top 2 entries
- **Bug or historical**: `monday.search('resolved {topic}')` to check resolution
- **Decision queries**: `monday.search('decision')` for the full ADR log
- **All types with results**: `monday.learn(...)` to add more knowledge

---

## Storage Independence

`ReasoningEngine` depends only on the public interfaces of `KnowledgeStore` and `TaskManager`:

```python
KnowledgeStore.search(query, limit)    → list[KnowledgeEntry]
KnowledgeStore.get(entry_id)           → KnowledgeEntry
KnowledgeStore.list_all()              → list[KnowledgeEntry]

TaskManager.list_active(status, ...)   → list[Task]
```

It does not import `KnowledgeParser`, `KnowledgeLoader`, `KnowledgeIndex`, or `TaskParser` — those are private implementation details. This means the Markdown-on-disk backend can be replaced with SQLite, PostgreSQL, or Neo4j without changing a line in `reasoner.py`.

---

## Future Integration Points

### Phase 2: LLM grounding

The engine's pipeline is designed so an LLM drops in at step 7 (synthesis) without changing steps 1–6:

```python
# Phase 2 replacement for _synthesize():
def _synthesize_with_llm(prompt, intent, entries, tasks, llm_client):
    structured_context = _build_context_block(entries, tasks)
    return llm_client.complete(
        system="You are a knowledge-grounding assistant for MondayOS.",
        user=f"Question: {prompt}\n\nContext:\n{structured_context}",
    )
```

The classified `intent` and ranked `entries`/`tasks` become the structured prompt — the LLM does not search; it only synthesizes. Confidence would then be set from the model's logprobs or a calibration heuristic.

### Phase 2: Model routing

When multiple models are available (Claude, GPT-4, Ollama), `model_used` in the response allows callers to verify which model answered. The `Router` (already in `brain/router.py`) is the planned integration point.

### Phase 3: Graph traversal

With Neo4j or SQLite backend, `_traverse_relationships` can be upgraded from depth-1 file reads to arbitrary-depth graph queries:

```python
# Phase 3:
related = self._knowledge.traverse(entry.id, relation=None, depth=2)
```

The `traverse()` method is already specified in the `StorageBackend` protocol in `docs/MKS.md`.

### Phase 3: Semantic search

Replacing `KnowledgeStore.search()` with a vector DB backend would change keyword scoring to cosine similarity — the reasoning engine's API stays identical.

---

## Supported Question Patterns

```python
# Historical lookup
monday.ask("Have we seen Homebrew PATH issues before?")
monday.ask("Has this auth error come up before?")

# Knowledge summary
monday.ask("What do we know about rate limiting?")
monday.ask("Summarize everything we know about Weather observations.")

# Type-filtered queries
monday.ask("Show related bugs for Homebrew.")
monday.ask("Show all ADRs related to search.")
monday.ask("Show all tasks related to authentication.")

# Operational queries
monday.ask("What is currently blocked?")
monday.ask("What changed recently?")

# Onboarding
monday.ask("What should I read first to understand the knowledge system?")
monday.ask("Where do I start to understand the task lifecycle?")
```

---

*Document owner: Lead Software Engineer  
Last updated: Sprint 1.4 (2026-06-27)*
