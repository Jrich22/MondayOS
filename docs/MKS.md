# MondayOS Knowledge Specification (MKS)

**Specification Number:** MKS-1.0  
**Status:** Active  
**Supersedes:** `docs/KNOWLEDGE_SYSTEM.md` (v0.1.0)  
**Effective Date:** 2026-06-27  
**Owner:** Lead Software Engineering

---

## Preface

This document is the canonical product specification for every piece of knowledge stored, retrieved, and managed by MondayOS. It is a contract, not a guide.

Every implementation — whether a Markdown file on disk, a SQLite row, a PostgreSQL record, a Neo4j node, or a vector embedding — must conform to this specification. Any implementation that cannot be validated against these rules is non-conforming and must be rejected or migrated.

This specification does not describe how MondayOS implements storage. It describes what MondayOS stores and what contracts those objects must satisfy regardless of how or where they are stored.

**Implementations change. This specification endures.**

---

## Table of Contents

1. Scope and Applicability
2. Terminology
3. The Canonical Knowledge Object (CKO)
4. Field Specification
5. ID Specification
6. Relationship Specification
7. Lifecycle Specification
8. Versioning Specification
9. Knowledge Type Catalogue
10. Validation Specification
11. Storage Backend Specification
12. Conformance Requirements
13. Changelog

---

## 1. Scope and Applicability

This specification applies to:

- All knowledge entries created, updated, or retrieved by MondayOS
- All storage backends used by MondayOS (current and future)
- All agents (human and AI) that write knowledge entries
- All consumers of the `KnowledgeStore` interface in the `knowledge` module
- All callers of `Monday.learn()` and `Monday.search()`

This specification does not govern:

- Task operational state (managed by the `tasks` module — see TASK_SYSTEM.md)
- Session memory (managed by `memory.SessionMemory`)
- System configuration (managed by `config/`)
- Log output (managed by `core/logger`)

However, when a task is completed, its knowledge record is governed by this specification (type: `TASK`). The operational system and the knowledge system coexist; the knowledge system is the persistent historical record.

---

## 2. Terminology

| Term | Definition |
|---|---|
| **CKO** | Canonical Knowledge Object — the universal base structure all knowledge types share |
| **Knowledge Entry** | A single instance of a CKO; one record in the knowledge base |
| **Type** | The category of a knowledge entry; determines required fields and body structure |
| **Relationship** | A directional, typed link between two knowledge entries |
| **Supersession** | The act of replacing an entry with a newer version; old entry becomes SUPERSEDED |
| **ID** | A globally unique, immutable identifier assigned at creation and never changed |
| **Version** | An integer counter that increments with every mutation of an entry |
| **Lifecycle** | The set of valid status states and the transitions between them |
| **Backend** | A storage implementation (Markdown files, SQLite, PostgreSQL, Neo4j, vector DB) |
| **Conforming** | A CKO instance that satisfies all validation rules in this specification |
| **Authored by** | The entity that wrote the entry — a human user or a named AI agent |

---

## 3. The Canonical Knowledge Object (CKO)

Every knowledge entry in MondayOS — regardless of type, author, or storage backend — is an instance of the Canonical Knowledge Object. The CKO is the universal schema.

### 3.1 Conceptual Model

```
┌─────────────────────────────────────────────────────────┐
│                 Canonical Knowledge Object              │
├──────────────────────────────────────────────────────── │
│  Identity       id · type · version                     │
│  Content        title · summary · body                  │
│  Provenance     created_at · created_by                 │
│                 updated_at · updated_by                 │
│                 authored_by · confidence                │
│  Classification tags · components · status              │
│  Relationships  relationships[]                         │
│  Type Data      type_fields{}   (type-specific fields)  │
│  Extension      metadata{}      (forward compatibility) │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Canonical JSON Representation

The CKO is defined in JSON because JSON is the language-neutral canonical form. All storage backends serialize to and from this representation.

```json
{
  "id":           "BUG-0001",
  "type":         "BUG",
  "version":      3,
  "status":       "ACTIVE",
  "title":        "Claude API rate limit not retried correctly",
  "summary":      "429 responses were dropped instead of retried with back-off.",
  "body":         "## Symptom\n...",
  "created_at":   "2026-06-27T14:00:00Z",
  "created_by":   "human:jrich",
  "updated_at":   "2026-06-27T15:30:00Z",
  "updated_by":   "agent:claude-sonnet-4-6",
  "authored_by":  "agent:claude-sonnet-4-6",
  "confidence":   0.95,
  "tags":         ["api", "rate-limit", "retry", "claude"],
  "components":   ["integrations", "brain"],
  "relationships": [
    {
      "relation":   "RESOLVED_BY",
      "target_id":  "TASK-0042",
      "note":       "Fix implemented and committed"
    },
    {
      "relation":   "LED_TO",
      "target_id":  "PAT-0007",
      "note":       "Extracted retry pattern from this resolution"
    }
  ],
  "type_fields": {
    "symptom":      "Claude API returns 429; caller crashes instead of waiting",
    "root_cause":   "retry-after header was not read; fixed wait time used instead",
    "resolution":   "Read retry-after header; fall back to exponential back-off",
    "prevention":   "Add integration test with mocked 429 response",
    "severity":     "HIGH",
    "commit_refs":  ["a3f9b2c"]
  },
  "metadata": {}
}
```

---

## 4. Field Specification

### 4.1 Required Fields (All Types)

Every conforming CKO must have all of the following fields. An entry missing any required field must be rejected by the validation layer.

---

#### `id` — EntityId

**Type:** String  
**Format:** `{TYPE_PREFIX}-{NNNN}` where NNNN is a zero-padded integer ≥ 1 (minimum 4 digits)  
**Constraints:** Globally unique; immutable after creation; must match the type prefix for the entry's type  
**Why it exists:** Stable cross-reference identifier. Every relationship, commit reference, and external citation uses this ID. IDs must never be reused, even for deleted or archived entries.

---

#### `type` — KnowledgeType

**Type:** Enum (string)  
**Valid values:** `BUG` | `DECISION` | `TASK` | `SPRINT` | `FEATURE` | `LESSON` | `PATTERN` | `RUNBOOK` | `DOCUMENTATION` | `RESEARCH` | `WEATHER` | `EXPERIMENT`  
**Constraints:** Immutable after creation. If a reclassification is needed, create a new entry with the correct type and supersede the old one.  
**Why it exists:** The type determines which `type_fields` are required, which validation rules apply, and how the body is structured. It is the primary dispatch key in the knowledge system.

---

#### `version` — Integer

**Type:** Integer ≥ 1  
**Constraints:** Starts at 1 on creation; incremented by exactly 1 on every mutation; no gaps permitted  
**Why it exists:** Optimistic concurrency control. Two agents attempting to update the same entry simultaneously are detected when they both present the same version number. The second write fails and must re-read before retrying.

---

#### `status` — LifecycleStatus

**Type:** Enum (string)  
**Valid values:** `DRAFT` | `ACTIVE` | `DEPRECATED` | `SUPERSEDED` | `ARCHIVED`  
**Constraints:** Must follow the valid lifecycle transitions in Section 7  
**Why it exists:** Controls visibility and search inclusion. Only ACTIVE entries are included in default searches. This enables clean deprecation without information loss.

---

#### `title` — String

**Type:** String  
**Length:** 1–200 characters  
**Constraints:** Non-empty; no leading or trailing whitespace; no newlines  
**Why it exists:** The human-readable name of the entry. Used in search result snippets, index tables, and relationship labels. Must be descriptive enough to identify the entry without reading the body.

---

#### `summary` — String

**Type:** String  
**Length:** 1–500 characters  
**Constraints:** Non-empty; no leading or trailing whitespace; no newlines; plain text (no Markdown)  
**Why it exists:** A single-paragraph executive description used in search result previews, relationship hover text, and agent context injection. The summary is what an AI agent reads when deciding whether to retrieve the full entry. It must stand alone.

---

#### `body` — String

**Type:** String (Markdown)  
**Length:** 1–100,000 characters  
**Constraints:** Non-empty; must contain at least one Markdown heading (`##`)  
**Why it exists:** The full structured content of the entry. Body structure is type-specific (defined in Section 9). The body is what humans and agents read to understand the full context.

---

#### `created_at` — Timestamp

**Type:** ISO 8601 UTC datetime string  
**Format:** `YYYY-MM-DDTHH:MM:SSZ` (UTC only; no timezone offsets)  
**Constraints:** Immutable after creation; must be ≤ `updated_at`  
**Why it exists:** Establishes when the entry was first recorded. Immutable because it is used in audit trails and temporal queries.

---

#### `created_by` — String

**Type:** String  
**Format:** `human:{identifier}` or `agent:{model-id}`  
**Examples:** `human:jrich`, `agent:claude-sonnet-4-6`  
**Constraints:** Immutable after creation; non-empty  
**Why it exists:** Attribution. Who first created this entry, which is different from who last updated it or who authored the content.

---

#### `updated_at` — Timestamp

**Type:** ISO 8601 UTC datetime string  
**Constraints:** Must be ≥ `created_at`; updated on every mutation including status changes  
**Why it exists:** Allows sorting by recency and detecting staleness. Any mutation — even a status change — must update this field.

---

#### `updated_by` — String

**Type:** String  
**Format:** `human:{identifier}` or `agent:{model-id}`  
**Constraints:** Non-empty; reflects the most recent mutating actor  
**Why it exists:** Separates the original author from the most recent modifier. Essential for audit trails when entries are updated by agents different from the original creator.

---

#### `authored_by` — String

**Type:** String  
**Format:** `human:{identifier}` or `agent:{model-id}`  
**Constraints:** Non-empty; set at creation; may be updated if content is substantially rewritten  
**Why it exists:** Identifies who wrote the intellectual content (title, summary, body, type_fields). Used for trust calibration: agent-authored entries may undergo additional review.

---

#### `confidence` — Float

**Type:** Float  
**Range:** 0.0–1.0 (inclusive)  
**Constraints:** Must be within [0.0, 1.0]  
**Why it exists:** Signals certainty about the entry's accuracy. Human-authored entries default to 1.0. Agent-authored entries self-report confidence. Consumers use this to calibrate how much weight to give the entry. A bug entry with `confidence: 0.4` means "we think this is the root cause but are not certain."

---

#### `tags` — List[String]

**Type:** Array of strings  
**Length:** 0–50 items  
**Item constraints:** Each tag is 1–64 characters; lowercase; no spaces (use hyphens); alphanumeric plus hyphens only  
**Constraints:** No duplicate tags within a single entry  
**Why it exists:** The primary free-form search mechanism. Tags are cross-cutting labels that cut across type, component, and lifecycle. They enable queries like "show me everything tagged `rate-limit` regardless of type."

---

#### `components` — List[String]

**Type:** Array of strings  
**Length:** 0–20 items  
**Item constraints:** Each component matches a top-level MondayOS module name or a user-defined domain  
**Why it exists:** Structured attribution to system areas. Separate from tags because components are controlled vocabulary (they match real module names), while tags are free-form.

---

#### `relationships` — List[Relationship]

**Type:** Array of Relationship objects (see Section 6)  
**Length:** 0–200 items  
**Constraints:** No duplicate (target_id, relation) pairs  
**Why it exists:** The relationship graph is the primary structural value of the knowledge base. Raw entries are useful; connected entries are powerful. Every entry should be connected to at least the entries it was motivated by or that it resolves.

---

#### `type_fields` — Object

**Type:** Key-value map  
**Constraints:** Keys and values are type-specific (see Section 9); required keys for the entry's type must be present  
**Why it exists:** Type-specific structured data that does not fit in the universal base fields. Keeping type-specific fields in a dedicated sub-object allows the base schema to remain stable while type schemas evolve independently.

---

### 4.2 Optional Fields (All Types)

Optional fields may be absent from a conforming CKO. Implementations must not fail when optional fields are absent.

---

#### `superseded_by` — EntityId | null

**Type:** EntityId string or null  
**Constraints:** Must reference an existing entry ID; only present when status is SUPERSEDED  
**Why it exists:** When an entry is superseded, this field points to its successor. This makes the succession chain traversable in both directions — the old entry points forward; the new entry's relationships include a SUPERSEDES relationship pointing backward.

---

#### `archived_at` — Timestamp | null

**Type:** ISO 8601 UTC datetime string or null  
**Constraints:** Only present when status is ARCHIVED  
**Why it exists:** Distinguishes entries that have been explicitly archived from those that simply haven't been touched. Allows age-based archive management.

---

#### `review_due` — Date | null

**Type:** ISO 8601 date string (YYYY-MM-DD) or null  
**Why it exists:** Some entry types (especially RUNBOOK, DECISION) should be periodically reviewed for accuracy. This field triggers automated review reminders.

---

#### `metadata` — Object

**Type:** Key-value map  
**Constraints:** Keys are strings; values may be any JSON-serializable type  
**Why it exists:** Forward compatibility. Fields that are candidates for promotion to first-class CKO fields are incubated in `metadata` first. Once a field appears in metadata frequently enough, it is promoted to a first-class optional field in the next specification revision.

---

## 5. ID Specification

### 5.1 ID Format

```
{TYPE_PREFIX}-{SEQUENCE}
```

Where:
- `TYPE_PREFIX` is a registered two-to-four character uppercase code (see Section 5.2)
- `SEQUENCE` is a zero-padded decimal integer, minimum 4 digits

**Valid examples:** `BUG-0001`, `DEC-0042`, `TASK-0100`, `WEATHER-0001`, `EXP-0099`

### 5.2 Type Prefix Registry

| Type | Prefix | Example |
|---|---|---|
| `BUG` | `BUG` | `BUG-0001` |
| `DECISION` | `DEC` | `DEC-0001` |
| `TASK` | `TASK` | `TASK-0001` |
| `SPRINT` | `SPR` | `SPR-0001` |
| `FEATURE` | `FEA` | `FEA-0001` |
| `LESSON` | `LES` | `LES-0001` |
| `PATTERN` | `PAT` | `PAT-0001` |
| `RUNBOOK` | `RUN` | `RUN-0001` |
| `DOCUMENTATION` | `DOC` | `DOC-0001` |
| `RESEARCH` | `RES` | `RES-0001` |
| `WEATHER` | `WEA` | `WEA-0001` |
| `EXPERIMENT` | `EXP` | `EXP-0001` |

### 5.3 ID Generation Rules

1. IDs are assigned by the `KnowledgeStore` at creation time, not by the caller.
2. The sequence number is the next integer in the per-type monotonic counter.
3. Counters are persistent across restarts. The current maximum sequence number is derived from the existing entry set on startup.
4. IDs are **never reused**, even if an entry is archived.
5. IDs are **immutable** after assignment. If an entry's type needs to change, the entry is superseded; the old ID remains on the old entry.
6. The prefix must exactly match the entry's `type` field according to the prefix registry.

### 5.4 ID Validation Rules

An ID is valid if and only if:
- It matches the regex: `^[A-Z]{2,7}-[0-9]{4,}$`
- The prefix is in the Type Prefix Registry
- The prefix matches the entry's `type` field
- The sequence number is ≥ 1

---

## 6. Relationship Specification

### 6.1 Relationship Model

A relationship is a directional, typed link from a source entry to a target entry. Relationships are stored on the source entry (in its `relationships` array). The inverse direction is queryable but is not redundantly stored on the target.

```json
{
  "relation":  "RESOLVED_BY",
  "target_id": "TASK-0042",
  "note":      "Fix implemented in this task",
  "created_at": "2026-06-27T15:00:00Z",
  "created_by": "human:jrich"
}
```

### 6.2 Relationship Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `relation` | RelationType (enum) | Yes | The type of relationship |
| `target_id` | EntityId | Yes | The ID of the target entry |
| `note` | String (≤500 chars) | No | Human-readable explanation of why this relationship exists |
| `created_at` | Timestamp | Yes | When this relationship was established |
| `created_by` | String | Yes | Who established this relationship |

### 6.3 Relationship Type Registry

Each relation type has a defined **forward** direction (stored on the source) and an **inverse** direction (queryable on the target). The inverse is not stored but must be derivable by any backend.

| Forward Relation | Inverse Relation | Meaning |
|---|---|---|
| `CAUSED_BY` | `CAUSED` | This entry was caused by the target |
| `RESOLVED_BY` | `RESOLVES` | This entry was resolved by the target |
| `SPAWNED` | `SPAWNED_BY` | This entry created the target |
| `PART_OF` | `CONTAINS` | This entry belongs to the target collection |
| `SUPERSEDES` | `SUPERSEDED_BY` | This entry replaces the target |
| `DEPENDS_ON` | `DEPENDED_ON_BY` | This entry requires the target to be complete |
| `IMPLEMENTS` | `IMPLEMENTED_BY` | This entry is the implementation of the target |
| `DOCUMENTS` | `DOCUMENTED_BY` | This entry documents the target |
| `REFERENCES` | `REFERENCED_BY` | This entry cites the target for context |
| `RELATED_TO` | `RELATED_TO` | Bidirectional relevance (symmetric) |
| `LED_TO` | `RESULTED_FROM` | This entry was the outcome of the target |
| `VALIDATES` | `VALIDATED_BY` | This entry confirms the target's claims |
| `CONTRADICTS` | `CONTRADICTED_BY` | This entry disagrees with the target |

### 6.4 The Knowledge Graph

The full set of entries and relationships forms a directed graph. This graph enables queries that are impossible with a flat search:

- "Show me everything that caused BUG-0001" → traverse CAUSED_BY
- "Show me everything this sprint produced" → traverse CONTAINS inverse
- "Show me the full decision chain behind FEATURE-0003" → traverse IMPLEMENTS → DEC → CAUSED_BY

This graph structure is why the MKS is designed to be Neo4j-compatible from day one. Even when stored as Markdown files, the relationships encode a graph that a future migration can import directly.

#### Example: Bug → Decision → Task → Sprint → Feature Chain

```
BUG-0001  ──[RESOLVED_BY]──► TASK-0042
BUG-0001  ──[LED_TO]────────► DEC-0008   (decision to change retry architecture)
DEC-0008  ──[IMPLEMENTS]────► FEA-0002   (retry reliability as a feature)
TASK-0042 ──[PART_OF]───────► SPR-0003  (task was in sprint 3)
SPR-0003  ──[PART_OF]───────► FEA-0002  (sprint contributed to feature)
BUG-0001  ──[LED_TO]────────► PAT-0007  (extracted retry pattern from fix)
PAT-0007  ──[DOCUMENTS]─────► FEA-0002  (pattern documents the retry approach)
```

Traversing this graph from `BUG-0001` reveals the complete chain of consequences: what decision the bug prompted, what task fixed it, what pattern it produced, what feature it ultimately contributed to, and which sprint it was resolved in.

### 6.5 Relationship Validation Rules

1. `target_id` must reference an entry that exists in the knowledge base.
2. `target_id` must not equal the source entry's `id` (no self-referential relationships).
3. The (target_id, relation) pair must be unique within a single entry's `relationships` array.
4. When an entry's status is SUPERSEDED, its relationships are preserved and remain traversable.
5. `SUPERSEDES` relationships are automatically created by the versioning system; they must not be manually created.
6. `RELATED_TO` relationships are bidirectional by convention; backends may optionally store them on both sides for query performance.

---

## 7. Lifecycle Specification

### 7.1 Status States

| Status | Included in Default Search | Mutable | Description |
|---|---|---|---|
| `DRAFT` | No | Yes | Being written; not yet reviewed or complete |
| `ACTIVE` | Yes | Yes | Live, authoritative, searchable |
| `DEPRECATED` | No (available via filter) | Yes | Valid but no longer recommended |
| `SUPERSEDED` | No (available via filter) | No | Replaced by a newer entry; read-only |
| `ARCHIVED` | No (available via filter) | No | Historical record; read-only |

### 7.2 Valid Transitions

```
                    ┌─────────────────┐
                    │      DRAFT      │
                    └────────┬────────┘
                             │ reviewed / published
                             ▼
                    ┌─────────────────┐
              ┌────►│     ACTIVE      │◄────┐
              │     └──┬─────────┬───┘     │
              │        │         │         │ (un-deprecate — rare)
       reopen │  retire│    supersede      │
              │        ▼         ▼         │
              │  ┌──────────┐ ┌──────────┐ │
              └──│DEPRECATED│ │SUPERSEDED│ │
                 └────┬─────┘ └──────────┘ │
                      │ archive             │
                      ▼                     │
                 ┌──────────┐               │
                 │ ARCHIVED │               │
                 └──────────┘               │
                      │ restore (exceptional)
                      └───────────────────►─┘
```

### 7.3 Transition Rules

| From | To | Permitted? | Rules |
|---|---|---|---|
| `DRAFT` | `ACTIVE` | Yes | Required fields must all be present and valid |
| `DRAFT` | `ARCHIVED` | Yes | Entry discarded before publication |
| `ACTIVE` | `DEPRECATED` | Yes | No additional constraints |
| `ACTIVE` | `SUPERSEDED` | Yes | `superseded_by` must be set; new entry must already exist |
| `ACTIVE` | `ARCHIVED` | Yes | Entry explicitly retired |
| `DEPRECATED` | `ACTIVE` | Yes | Un-deprecation requires human approval |
| `DEPRECATED` | `ARCHIVED` | Yes | Final retirement |
| `SUPERSEDED` | any | **No** | SUPERSEDED is permanent; no transitions |
| `ARCHIVED` | `ACTIVE` | Exceptional | Requires explicit human approval and a new version increment |
| `ARCHIVED` | any other | **No** | |

### 7.4 Terminal States

`SUPERSEDED` is a permanent terminal state. An entry that has been superseded may not be transitioned to any other status. It is read-only and preserved for historical traversal.

`ARCHIVED` is a soft terminal state. Restoration to `ACTIVE` is permitted under exceptional circumstances with human approval, but is discouraged. The preferred path is to create a new entry with the correct content.

---

## 8. Versioning Specification

### 8.1 Versioning Model

Every mutation to a knowledge entry increments the `version` field. The definition of mutation:

- Any change to `title`, `summary`, `body`, or `type_fields`
- Any change to `status`
- Any change to `tags`, `components`, or `confidence`
- Addition or removal of any relationship
- Any change to `metadata`

Non-mutations (do not increment version):
- Reading the entry
- Regenerating the search index
- Backend migration (data is preserved; version is not incremented)

### 8.2 Version Increment Protocol

```
1. Read current entry → verify version matches expected
2. Apply mutations
3. Set updated_at = now()
4. Set updated_by = current actor
5. Increment version by 1
6. Write new version
7. If write fails due to version conflict → abort; caller must re-read and retry
```

### 8.3 Audit Trail

Backends are not required to store all historical versions in Phase 1. However, they must not actively destroy previous version data. The recommended approach:

- **Markdown backend:** Git history preserves all versions automatically.
- **SQLite/PostgreSQL:** Maintain a `knowledge_history` table with (id, version, snapshot_json, changed_at, changed_by).
- **Neo4j:** Store version history as node properties or as a versioned node chain.

Version history is read-only. Historical versions may be retrieved but never mutated.

### 8.4 Supersession vs. Versioning

Versioning (incrementing the version field) is for corrections and updates to an existing entry: fixing a typo, adding a tag, updating a status.

Supersession is for replacing an entry with a fundamentally different one: a better answer to the same question, a different root cause identified later, an architectural decision reversed.

**Rule:** If the meaning or conclusion of the entry changes significantly, supersede. If the form or metadata of the entry changes, version.

---

## 9. Knowledge Type Catalogue

### 9.1 BUG

**Purpose:** Record a defect that was discovered and resolved, so it can be recognized and fixed immediately if it recurs.

**Type Prefix:** `BUG`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `symptom` | String | ≤2000 chars | Observable failure: error message, unexpected behavior, or user-visible effect |
| `root_cause` | String | ≤2000 chars | The precise technical cause. Must be specific enough that a new engineer could reproduce and verify the cause |
| `resolution` | String | ≤2000 chars | What was changed to eliminate the defect |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `prevention` | String | What test, lint rule, or architectural change prevents recurrence |
| `severity` | Enum: `CRITICAL\|HIGH\|MEDIUM\|LOW` | Business impact |
| `reproduction_steps` | List[String] | Step-by-step reproduction |
| `affected_versions` | List[String] | Version strings affected |
| `commit_refs` | List[String] | Git SHAs of the fix |
| `first_seen` | Timestamp | When was this first observed |
| `resolved_at` | Timestamp | When was it confirmed fixed |

**Canonical Relationships:**
- `RESOLVED_BY → TASK` (the task that implemented the fix)
- `LED_TO → DECISION` (if the bug prompted an architectural change)
- `LED_TO → PATTERN` (if a reusable pattern was extracted from the fix)
- `PART_OF → SPRINT` (which sprint it was resolved in)

**Required Body Structure:**
```markdown
## Symptom
{Observable failure with full error message or behavior description}

## Root Cause
{Specific technical explanation. "Unknown" is acceptable if genuinely unknown, but must say why}

## Resolution
{What was changed, with commit references}

## Prevention
{How to ensure this class of bug cannot recur}
```

**Example:**
```json
{
  "id": "BUG-0001",
  "type": "BUG",
  "title": "Claude API 429 response causes crash instead of retry",
  "summary": "Rate limit responses from the Anthropic API were not retried; the caller crashed.",
  "type_fields": {
    "symptom": "RuntimeError raised on HTTP 429 from anthropic.completions.create()",
    "root_cause": "The integration layer caught only HTTP 5xx errors in its retry loop; 429 was treated as a non-retriable client error.",
    "resolution": "Added 429 to the retriable status set; reads retry-after header; falls back to exponential back-off with jitter.",
    "severity": "HIGH",
    "prevention": "Add test with mocked 429 response verifying retry behavior and back-off timing.",
    "commit_refs": ["a3f9b2c"]
  }
}
```

**Validation:**
- `symptom`, `root_cause`, and `resolution` must each be non-empty
- `severity` must be one of the four valid values if present
- `commit_refs` items must match git SHA format: `[0-9a-f]{7,40}`

---

### 9.2 DECISION

**Purpose:** Record an architectural, technical, or product decision with its full context, so that future engineers and agents can understand why the system is built the way it is — and know when the conditions for that decision have changed.

**Type Prefix:** `DEC`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `context` | String | ≤3000 chars | The situation and constraints that required a decision |
| `decision` | String | ≤2000 chars | Exactly what was decided — one clear statement |
| `rationale` | String | ≤3000 chars | Why this option was chosen over alternatives |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `alternatives` | List[{option: String, reason_rejected: String}] | Options considered but not chosen |
| `consequences` | String | What becomes easier, harder, or riskier as a result |
| `deciders` | List[String] | Who made this decision |
| `review_trigger` | String | Condition that would prompt revisiting this decision |
| `review_due` | Date | When this decision should be reviewed for continued validity |

**Canonical Relationships:**
- `IMPLEMENTS → FEATURE` (the feature this decision enables)
- `SUPERSEDES → DECISION` (the prior decision this replaces)
- `CAUSED_BY → BUG` (if a bug prompted this decision)
- `REFERENCES → RESEARCH` (research that informed this decision)

**Required Body Structure:**
```markdown
## Context
{What situation required a decision?}

## Decision
{What was decided?}

## Rationale
{Why this option?}

## Alternatives Considered
| Alternative | Reason Not Chosen |
|---|---|

## Consequences
{What becomes easier? What becomes harder?}
```

**Example:**
```json
{
  "id": "DEC-0009",
  "type": "DECISION",
  "title": "Use exponential back-off with jitter for all API retries",
  "summary": "Retry logic uses exponential back-off plus random jitter to prevent thundering herd on API rate limits.",
  "type_fields": {
    "context": "After BUG-0001, we needed a robust retry strategy for all external API calls. Multiple agents calling the same endpoint simultaneously caused synchronized retry storms.",
    "decision": "All integration layer retries use exponential back-off (base 2, max 60s) with random jitter (±25% of computed delay).",
    "rationale": "Exponential back-off prevents thundering herd; jitter desynchronizes retries from multiple callers. Well-established pattern (AWS, Google cloud guidelines).",
    "deciders": ["human:jrich"],
    "review_trigger": "If a provider changes their rate limit structure, revisit the back-off parameters."
  }
}
```

**Validation:**
- `context`, `decision`, and `rationale` must each be non-empty
- `alternatives` items must have both `option` and `reason_rejected` fields
- `review_due` must be a valid ISO 8601 date string if present

---

### 9.3 TASK

**Purpose:** Record the permanent knowledge artifact of a completed unit of work. This is distinct from the operational task record in `tasks/` — the TASK knowledge entry is the historical record created when a task is completed.

**Type Prefix:** `TASK`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `objective` | String | ≤1000 chars | What was the task trying to accomplish |
| `outcome` | String | ≤2000 chars | What was actually accomplished |
| `status_at_completion` | Enum | `COMPLETED\|CANCELLED` | How the task ended |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `assigned_to` | String | Who executed the task |
| `time_spent_minutes` | Integer | Actual time invested |
| `acceptance_criteria_met` | List[{criterion: String, met: Boolean}] | Outcome per criterion |
| `commit_refs` | List[String] | Code changes produced |
| `blockers_encountered` | List[String] | What got in the way |
| `operational_task_id` | String | Reference to the source operational task (e.g. `tasks/active/TASK-0042.md`) |

**Canonical Relationships:**
- `PART_OF → SPRINT`
- `RESOLVES → BUG`
- `IMPLEMENTS → FEATURE`
- `LED_TO → PATTERN`
- `SPAWNED → TASK` (subtasks)

**Required Body Structure:**
```markdown
## Objective
{What this task was meant to accomplish}

## Outcome
{What was actually done and delivered}

## Key Decisions Made
{Any decisions made during execution that should be recorded}

## Lessons
{What was learned that should inform future tasks}
```

---

### 9.4 SPRINT

**Purpose:** Record a completed development sprint as a unit of accumulated progress — what was planned, what was delivered, and what was learned.

**Type Prefix:** `SPR`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `start_date` | Date | ISO 8601 | When the sprint began |
| `end_date` | Date | ISO 8601 | When the sprint ended |
| `goals` | List[String] | ≥1 item | What the sprint set out to accomplish |
| `outcomes` | List[String] | ≥1 item | What was actually delivered |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `velocity` | Integer | Story points or task count completed |
| `planned_velocity` | Integer | Originally planned |
| `retrospective` | String | What went well, what didn't, what to change |
| `carry_over` | List[EntityId] | Task IDs not completed; carried to next sprint |

**Canonical Relationships:**
- `CONTAINS → TASK` (all tasks completed in this sprint)
- `PART_OF → FEATURE` (features advanced in this sprint)
- `LED_TO → LESSON` (lessons from the retrospective)

**Required Body Structure:**
```markdown
## Goals
{What was this sprint meant to accomplish?}

## Delivered
{What was actually completed?}

## Not Delivered
{What was planned but not completed, and why?}

## Retrospective
{What went well? What was hard? What changes for next sprint?}
```

---

### 9.5 FEATURE

**Purpose:** Document a product capability — what it does, why it exists, how it is defined, and what state it is in.

**Type Prefix:** `FEA`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `description` | String | ≤2000 chars | What this feature does for users |
| `acceptance_criteria` | List[String] | ≥1 item | Testable conditions that define completion |
| `feature_status` | Enum | `PLANNED\|IN_PROGRESS\|COMPLETE\|CANCELLED` | Current delivery state |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `user_stories` | List[String] | "As a {role}, I want {capability}" statements |
| `design_refs` | List[String] | Links to design documents or mockups |
| `target_version` | String | Version in which this feature is planned |
| `delivered_version` | String | Version in which this feature shipped |

**Canonical Relationships:**
- `IMPLEMENTED_BY → SPRINT` (sprints that delivered parts of this feature)
- `DOCUMENTS → DECISION` (architectural decisions that define this feature)
- `REFERENCED_BY → RESEARCH` (research that motivated this feature)

---

### 9.6 LESSON

**Purpose:** Capture a generalized insight from experience — not tied to a specific bug or decision, but an observation about how to work better.

**Type Prefix:** `LES`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `situation` | String | ≤1000 chars | The context in which this lesson was learned |
| `lesson` | String | ≤1000 chars | The insight itself — stated as a principle or rule |
| `recommendation` | String | ≤1000 chars | What to do differently as a result |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `severity` | Enum: `HIGH\|MEDIUM\|LOW` | How important this lesson is to apply |
| `applicable_contexts` | List[String] | Where this lesson applies |
| `counter_examples` | String | Situations where this lesson does NOT apply |

**Canonical Relationships:**
- `RESULTED_FROM → BUG` (bug that prompted this lesson)
- `RESULTED_FROM → SPRINT` (retrospective lesson)
- `VALIDATES → PATTERN` (lesson confirms a pattern is sound)

---

### 9.7 PATTERN

**Purpose:** Document a reusable solution to a recurring problem. Patterns are positive knowledge — proven approaches worth repeating.

**Type Prefix:** `PAT`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `problem` | String | ≤1000 chars | The recurring situation this pattern addresses |
| `solution` | String | ≤3000 chars | How to apply the pattern — concrete, with examples |
| `context` | String | ≤1000 chars | The conditions under which this pattern applies |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `trade_offs` | String | What you gain and what you give up |
| `known_uses` | List[String] | Where in the codebase this pattern is applied |
| `related_patterns` | List[EntityId] | Other patterns to be aware of |
| `anti_pattern` | String | The common mistake this pattern prevents |

**Required Body Structure:**
```markdown
## Problem
{The recurring situation}

## Solution
{How to apply the pattern, with code examples}

## When to Use
{Conditions that indicate this pattern is appropriate}

## When NOT to Use
{Conditions where applying this pattern would be a mistake}

## Known Applications
{Where this is already used in the codebase}
```

---

### 9.8 RUNBOOK

**Purpose:** Document a step-by-step operational procedure for a task that is performed infrequently enough that it cannot be done reliably from memory.

**Type Prefix:** `RUN`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `trigger` | String | ≤500 chars | What condition or event should cause this runbook to be used |
| `steps` | List[String] | ≥1 item | Ordered steps; each describes the action and its expected outcome |
| `verification` | String | ≤1000 chars | How to confirm the procedure succeeded |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `prerequisites` | List[String] | What must be true before starting |
| `rollback` | String | How to undo the procedure if something goes wrong |
| `estimated_minutes` | Integer | How long this procedure typically takes |
| `last_verified_at` | Timestamp | When this runbook was last executed and verified as accurate |
| `known_failure_modes` | List[{failure: String, resolution: String}] | What has gone wrong before |

**Validation:**
- `steps` must have at least one item
- `last_verified_at` should be updated every time the runbook is executed
- Runbooks older than 180 days since `last_verified_at` are flagged for review

---

### 9.9 DOCUMENTATION

**Purpose:** Record a structured reference document — the kind of content that belongs in `docs/` but also needs to be searchable and relationally connected within the knowledge base.

**Type Prefix:** `DOC`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `content_type` | Enum | `SPECIFICATION\|GUIDE\|REFERENCE\|OVERVIEW\|STANDARD` | The kind of document |
| `scope` | String | ≤500 chars | What system area or audience this document covers |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `audience` | List[String] | Intended readers: "engineers", "agents", "external-consumers" |
| `review_cycle_days` | Integer | How often this document should be reviewed |
| `source_path` | String | Relative path to the source file (e.g. `docs/ARCHITECTURE.md`) |

---

### 9.10 RESEARCH

**Purpose:** Document the results of an investigation — a structured record of a question asked, methodology used, and findings reached. Enables future agents to build on prior research rather than re-investigating known territory.

**Type Prefix:** `RES`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `question` | String | ≤500 chars | The specific question this research was trying to answer |
| `methodology` | String | ≤1000 chars | How the investigation was conducted |
| `findings` | String | ≤3000 chars | What was discovered |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `data_sources` | List[String] | URLs, files, APIs, or systems consulted |
| `conclusion` | String | The answer to the question, stated directly |
| `confidence_notes` | String | Why the stated confidence level was assigned |
| `follow_on_questions` | List[String] | Related questions this research did not answer |
| `contradicts_prior` | List[EntityId] | Research entries this contradicts |

**Canonical Relationships:**
- `INFORMS → DECISION` (research that informed a decision)
- `VALIDATES → EXPERIMENT` (research that validates experimental results)
- `LED_TO → FEATURE` (research that motivated a feature)

---

### 9.11 WEATHER

**Purpose:** Record a domain-specific structured observation of weather or environmental conditions. Included in the MKS to demonstrate that the knowledge system supports domain-specific non-engineering knowledge, and as a foundation for weather-aware agent workflows.

**Type Prefix:** `WEA`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `location` | String | ≤200 chars | Location name or coordinates |
| `observed_at` | Timestamp | ISO 8601 UTC | When the observation was made |
| `conditions` | String | ≤500 chars | General weather description |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `temperature_c` | Float | Temperature in Celsius |
| `humidity_pct` | Float | Relative humidity 0–100 |
| `wind_speed_kph` | Float | Wind speed in km/h |
| `wind_direction` | String | Cardinal direction (N, NE, E, ...) |
| `pressure_hpa` | Float | Atmospheric pressure in hPa |
| `precipitation_mm` | Float | Precipitation in mm |
| `visibility_km` | Float | Visibility in km |
| `uv_index` | Integer | UV index 0–11+ |
| `forecast_accuracy` | Float | If this was a forecast, how accurate was it (0.0–1.0) |
| `data_source` | String | API or sensor that provided the data |
| `raw_payload` | Object | Original data from the source, if available |

**Canonical Relationships:**
- `REFERENCES → EXPERIMENT` (weather observation that informed an experiment)
- `VALIDATES → RESEARCH` (observation that confirms research findings)

---

### 9.12 EXPERIMENT

**Purpose:** Document a hypothesis, the test designed to evaluate it, and the results. Enables systematic learning through controlled investigation.

**Type Prefix:** `EXP`

**Mandatory type_fields:**

| Field | Type | Constraints | Description |
|---|---|---|---|
| `hypothesis` | String | ≤500 chars | The specific, falsifiable claim being tested |
| `method` | String | ≤2000 chars | How the experiment was conducted |
| `result` | String | ≤2000 chars | What was observed — raw observations, not interpretation |
| `conclusion` | String | ≤1000 chars | What the results mean — did the hypothesis hold? |

**Optional type_fields:**

| Field | Type | Description |
|---|---|---|
| `control` | String | What the control condition was |
| `variables` | List[{name: String, values: List}] | Variables manipulated or observed |
| `metrics` | List[{metric: String, baseline: Any, result: Any}] | Measured values before and after |
| `statistical_significance` | Float | p-value or confidence interval if applicable |
| `sample_size` | Integer | Number of trials or data points |
| `duration_minutes` | Integer | How long the experiment ran |
| `rejected_hypothesis` | Boolean | True if the hypothesis was falsified |

**Canonical Relationships:**
- `VALIDATES → PATTERN` (experiment that confirms a pattern works)
- `CONTRADICTS → RESEARCH` (experiment that contradicts prior research)
- `LED_TO → DECISION` (experiment result that prompted a decision)
- `REFERENCES → WEATHER` (experiment that used weather data)

**Required Body Structure:**
```markdown
## Hypothesis
{The specific claim being tested}

## Method
{How the experiment was conducted}

## Results
{Raw observations — numbers, logs, outputs}

## Conclusion
{What the results mean; was the hypothesis supported?}

## Next Steps
{What this experiment suggests should be investigated next}
```

---

## 10. Validation Specification

### 10.1 Validation Levels

**Level 1 — Schema Validation:** All required CKO fields are present and have the correct types.

**Level 2 — Constraint Validation:** Field values satisfy length, format, and range constraints.

**Level 3 — Semantic Validation:** The entry is internally consistent (e.g., `status` is `SUPERSEDED` and `superseded_by` is set; `created_at ≤ updated_at`; type prefix matches type field).

**Level 4 — Referential Validation:** All relationship `target_id` values reference existing entries.

### 10.2 Validation Rules Table

| Rule ID | Level | Description |
|---|---|---|
| VAL-001 | 1 | All required CKO fields must be present |
| VAL-002 | 1 | `type` must be a valid KnowledgeType enum value |
| VAL-003 | 1 | `status` must be a valid LifecycleStatus enum value |
| VAL-004 | 2 | `id` must match regex `^[A-Z]{2,7}-[0-9]{4,}$` |
| VAL-005 | 2 | `id` prefix must match the entry's `type` per the prefix registry |
| VAL-006 | 2 | `title` must be 1–200 characters, no newlines |
| VAL-007 | 2 | `summary` must be 1–500 characters, no newlines, plain text |
| VAL-008 | 2 | `body` must be ≥1 character and contain at least one `##` heading |
| VAL-009 | 2 | `confidence` must be in [0.0, 1.0] |
| VAL-010 | 2 | Tags must match `^[a-z0-9][a-z0-9-]*[a-z0-9]$` or be single characters |
| VAL-011 | 2 | `version` must be integer ≥ 1 |
| VAL-012 | 2 | `created_by` and `updated_by` must match `^(human|agent):.+$` |
| VAL-013 | 3 | `created_at ≤ updated_at` |
| VAL-014 | 3 | If `status` is `SUPERSEDED`, `superseded_by` must be set |
| VAL-015 | 3 | If `status` is `ARCHIVED`, `archived_at` must be set |
| VAL-016 | 3 | All mandatory `type_fields` for the entry's type must be present |
| VAL-017 | 3 | No relationship `target_id` may equal the entry's own `id` |
| VAL-018 | 3 | No duplicate (target_id, relation) pairs in `relationships` |
| VAL-019 | 4 | All relationship `target_id` values must exist in the knowledge base |
| VAL-020 | 4 | `superseded_by`, when set, must reference an existing entry |

### 10.3 Validation on Write

All Level 1, 2, and 3 validations must be enforced synchronously on every write. A write that fails any of these rules must be rejected with a typed error.

Level 4 (referential) validation is enforced synchronously in Phase 1 (small knowledge base). In Phase 2+, Level 4 may be enforced asynchronously with compensation if targets are not yet created (optimistic relationship creation).

### 10.4 Draft Exception

An entry in `DRAFT` status is exempt from Level 3 and Level 4 validations. This allows partial entries to be saved and completed over multiple sessions. Promotion from `DRAFT` to `ACTIVE` triggers full validation.

---

## 11. Storage Backend Specification

### 11.1 The Backend Abstraction

The `KnowledgeStore` class in `knowledge/store.py` is the **only** interface through which knowledge entries are persisted or retrieved. It does not know which backend is in use. The backend is a constructor parameter, not an implementation detail of the store.

```
Monday.learn() / Monday.search()
        │
        ▼
  KnowledgeStore  ◄────── stable public interface
        │
        ▼
  StorageBackend  ◄────── one of: Markdown, SQLite, PostgreSQL, Neo4j, Vector
        │
        ▼
  Physical Storage
```

Every backend implements the same `StorageBackend` protocol:

```python
class StorageBackend(Protocol):
    def create(self, entry: KnowledgeEntry) -> EntityId: ...
    def read(self, entry_id: EntityId) -> KnowledgeEntry: ...
    def update(self, entry: KnowledgeEntry) -> KnowledgeEntry: ...
    def search(self, query: SearchQuery) -> list[KnowledgeEntry]: ...
    def list_by_type(self, type: KnowledgeType) -> list[KnowledgeEntry]: ...
    def list_by_status(self, status: LifecycleStatus) -> list[KnowledgeEntry]: ...
    def traverse(self, entry_id: EntityId, relation: RelationType, depth: int) -> list[KnowledgeEntry]: ...
```

Changing backends requires: implementing `StorageBackend`, running the migration protocol (Section 11.7), and passing the new backend to `KnowledgeStore`. The `Monday` public API does not change.

### 11.2 Markdown Backend (Phase 1)

**When to use:** Single user, local operation, Git as the source of truth.

**CKO → Markdown Mapping:**

The CKO is serialized as a Markdown file with a YAML frontmatter block. All CKO fields except `body` go in frontmatter. `body` is the Markdown content below the frontmatter separator.

```markdown
---
id: BUG-0001
type: BUG
version: 1
status: ACTIVE
title: "Claude API 429 not retried"
summary: "Rate limit responses were not retried; caller crashed."
created_at: "2026-06-27T14:00:00Z"
created_by: "human:jrich"
updated_at: "2026-06-27T14:00:00Z"
updated_by: "human:jrich"
authored_by: "human:jrich"
confidence: 1.0
tags: [api, rate-limit, retry]
components: [integrations]
relationships:
  - relation: RESOLVED_BY
    target_id: TASK-0042
    note: "Fix implemented"
    created_at: "2026-06-27T14:00:00Z"
    created_by: "human:jrich"
type_fields:
  symptom: "RuntimeError on HTTP 429"
  root_cause: "429 not in retriable status set"
  resolution: "Added 429 to retry set; reads retry-after header"
  severity: HIGH
metadata: {}
---

## Symptom
...

## Root Cause
...
```

**Directory layout:**
```
knowledge/
├── bugs/        BUG-NNNN.md
├── decisions/   DEC-NNNN.md
├── tasks/       TASK-NNNN.md
├── sprints/     SPR-NNNN.md
├── features/    FEA-NNNN.md
├── lessons/     LES-NNNN.md
├── patterns/    PAT-NNNN.md
├── runbooks/    RUN-NNNN.md
├── docs/        DOC-NNNN.md
├── research/    RES-NNNN.md
├── weather/     WEA-NNNN.md
├── experiments/ EXP-NNNN.md
└── index.md     (auto-generated; never edited)
```

**Sequence tracking:** A `.sequences` JSON file at `knowledge/.sequences` tracks the current maximum sequence per type. Updated atomically on every write.

```json
{
  "BUG": 3, "DEC": 9, "TASK": 42, "SPR": 2,
  "FEA": 5, "LES": 1, "PAT": 7, "RUN": 3,
  "DOC": 12, "RES": 2, "WEA": 8, "EXP": 1
}
```

**Limitations:** No full-text search, no relational queries, no graph traversal. Suitable up to ~1,000 entries.

### 11.3 SQLite Backend (Phase 2)

**When to use:** Multi-session, moderate volume (up to ~100,000 entries), full-text search required.

**Schema:**

```sql
CREATE TABLE knowledge_entries (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    authored_by TEXT NOT NULL,
    confidence  REAL NOT NULL,
    tags        TEXT NOT NULL,   -- JSON array
    components  TEXT NOT NULL,   -- JSON array
    type_fields TEXT NOT NULL,   -- JSON object
    metadata    TEXT NOT NULL,   -- JSON object
    superseded_by TEXT,
    archived_at TEXT
);

CREATE TABLE knowledge_relationships (
    source_id   TEXT NOT NULL REFERENCES knowledge_entries(id),
    target_id   TEXT NOT NULL,
    relation    TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, relation)
);

CREATE TABLE knowledge_history (
    id          TEXT NOT NULL,
    version     INTEGER NOT NULL,
    snapshot    TEXT NOT NULL,   -- full JSON of entry at this version
    changed_at  TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    PRIMARY KEY (id, version)
);

CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    id UNINDEXED,
    title,
    summary,
    body,
    tags,
    content=knowledge_entries,
    content_rowid=rowid
);

CREATE TABLE knowledge_sequences (
    type        TEXT PRIMARY KEY,
    current_seq INTEGER NOT NULL DEFAULT 0
);
```

**Graph traversal in SQLite:** Use recursive CTEs to traverse the relationship graph to arbitrary depth without changing the data model.

### 11.4 PostgreSQL Backend (Phase 3)

**When to use:** Multi-user team deployment, high query volume, advanced full-text search, production SLA required.

**Key differences from SQLite schema:**
- `tags` and `components` stored as `TEXT[]` for native array operations
- `type_fields` and `metadata` stored as `JSONB` for indexed JSON queries
- `body` and `summary` indexed with `tsvector` for native full-text search
- `knowledge_relationships` may use native graph extensions (pg_graph or Apache AGE) if available
- Partitioning by `type` for large knowledge bases

### 11.5 Neo4j Backend (Graph)

**When to use:** Relationship traversal is the primary access pattern; knowledge graph queries are central to the product.

**CKO → Neo4j Mapping:**

Every knowledge entry becomes a **Node** with label equal to its type (`BUG`, `DECISION`, etc.). Every relationship becomes a **directed edge** with type equal to the relation name.

```cypher
// Entry as node
CREATE (b:BUG:KnowledgeEntry {
  id: 'BUG-0001',
  title: 'Claude API 429 not retried',
  status: 'ACTIVE',
  version: 1,
  confidence: 0.95,
  -- ... all CKO base fields
  type_fields: '{...}',   -- JSON string
  metadata: '{}'
})

// Relationship as edge
MATCH (b:BUG {id: 'BUG-0001'}), (t:TASK {id: 'TASK-0042'})
CREATE (b)-[:RESOLVED_BY {note: 'Fix implemented', created_at: '...', created_by: '...'}]->(t)
```

**Graph queries enabled by this model:**

```cypher
// Full causal chain from a bug
MATCH path = (b:BUG {id: 'BUG-0001'})-[*1..5]->()
RETURN path

// All decisions that affect a component
MATCH (d:DECISION)-[:IMPLEMENTS|DOCUMENTS]->(f:FEATURE)
WHERE 'integrations' IN d.components
RETURN d, f

// Shortest path between a bug and a feature
MATCH path = shortestPath((b:BUG {id: 'BUG-0001'})-[*]->(f:FEATURE))
RETURN path
```

The MKS relationship model maps directly to Neo4j without any data model changes because relationships were first-class from the beginning of this specification.

### 11.6 Vector Database Backend

**When to use:** Semantic search ("find entries similar to this description") is required; embedding-based retrieval.

**CKO → Vector Mapping:**

Each entry produces one or more vectors:
- **Primary vector:** Embedding of `title + " " + summary`
- **Full vector:** Embedding of `title + " " + summary + " " + body` (truncated to model context window)
- **Metadata filters:** All structured fields stored as filterable metadata alongside the vector

```json
{
  "id": "BUG-0001",
  "vector": [0.12, -0.34, ...],
  "metadata": {
    "type": "BUG",
    "status": "ACTIVE",
    "tags": ["api", "rate-limit"],
    "components": ["integrations"],
    "confidence": 0.95,
    "created_at": "2026-06-27T14:00:00Z"
  }
}
```

Vector backends do not replace structural backends — they are a retrieval layer on top of an authoritative store (SQLite or PostgreSQL). The vector index is derived from the canonical CKO; it is never the source of truth.

### 11.7 Migration Protocol

Migration between backends is zero-downtime for read access and requires a write freeze for atomic cut-over.

**Protocol:**

```
Phase 1: Prepare
  1. Validate all existing entries against MKS rules
  2. Export all entries as canonical JSON (CKO format)
  3. Verify export count matches source count
  4. Stand up new backend in parallel (do not route traffic)

Phase 2: Import
  1. Import all entries into new backend
  2. Verify import count matches export count
  3. Run spot-check validation on 10% of entries
  4. Build all indexes (FTS, graph, vector)

Phase 3: Verify
  1. Run full test suite against new backend
  2. Run query equivalence tests (same query, both backends, compare results)
  3. Human review of migration report

Phase 4: Cut Over
  1. Freeze writes to old backend
  2. Export delta (entries written during import phase)
  3. Import delta to new backend
  4. Swap StorageBackend constructor argument
  5. Run health check
  6. Unfreeze writes

Phase 5: Decommission
  1. Keep old backend in read-only mode for 30 days
  2. After 30 days, archive old backend data and decommission
```

**What the public API does not know about migration:**

`Monday.learn()` and `Monday.search()` call `KnowledgeStore` methods. `KnowledgeStore` calls `StorageBackend` methods. The backend is swapped at the `StorageBackend` level. The public API, `KnowledgeStore`, and all callers above it are completely unaware that a migration occurred.

---

## 12. Conformance Requirements

An implementation is **MKS-conforming** if and only if:

1. Every entry it creates satisfies all Level 1, 2, and 3 validation rules in Section 10.
2. Every entry created with status `ACTIVE` also satisfies Level 4 validation.
3. Entry IDs follow the format and prefix rules in Section 5.
4. Lifecycle transitions follow the rules in Section 7.3.
5. Version numbers are monotonically increasing per entry with no gaps.
6. Relationships are stored on the source entry with all required fields.
7. The `SUPERSEDED` status is permanent and cannot be transitioned.
8. The `StorageBackend` protocol is implemented with all seven methods.
9. Migration in and out of the backend via canonical JSON export/import is supported.

An implementation that fails any of these requirements is **non-conforming**. Non-conforming data must be rejected at write time or flagged for remediation during migration.

---

## 13. Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-06-27 | Initial specification. 12 knowledge types, relationship graph, lifecycle, versioning, 5 storage backends, migration protocol. Supersedes KNOWLEDGE_SYSTEM.md v0.1.0. |
