# MondayOS — Documentation Standards

**Version:** 0.1.0  
**Status:** Active  
**Last Updated:** 2026-06-27

---

## Purpose

Documentation in MondayOS is a first-class engineering artifact. It is not written after the fact — it is written as part of completing any task. A feature without documentation is not done. An architectural decision without a record is not made.

This document defines what documentation exists, where it lives, who is responsible for it, and how it is written.

---

## Documentation Philosophy

### Documentation Is a Product

Users of MondayOS — humans and AI agents — rely on documentation to understand the system. A knowledge base that cannot be searched, documentation that is out of date, and reasoning that was never written down are all forms of technical debt.

### Freshness Is Accuracy

Documentation that was accurate six months ago and has not been updated is misinformation. Every change to the system must update the relevant documentation in the same commit or PR.

### Documentation for AI Agents

AI agents in MondayOS read documentation to make decisions. This adds a requirement that human-oriented documentation does not have: documentation must be structured enough for an AI to parse, extract facts from, and reason about. Prefer:
- Clear section headings
- Explicit statements over implied context
- Lists and tables over long prose when presenting structured information
- Defined vocabulary (terms used consistently throughout the system)

### Write Once, Reference Many

Documentation should not be duplicated. If the same information appears in two places, one of them will go stale. Write the canonical version in one place; link to it from everywhere else.

---

## Documentation Types and Locations

| Type | Location | Purpose | Audience |
|---|---|---|---|
| Project foundation docs | `docs/` | Architecture, vision, standards | Humans + AI agents |
| ADRs | `docs/DECISIONS.md` | Architectural Decision Records | Humans + AI agents |
| Knowledge entries | `knowledge/` | Learned patterns, bugs, runbooks | AI agents primarily |
| Module README | `{module}/README.md` | Module purpose and interface | Engineers |
| API reference | Auto-generated from docstrings | Function/class signatures | Engineers |
| Changelog | `docs/CHANGELOG.md` | Version history | All stakeholders |
| Task records | `tasks/` | What was done and why | AI agents + engineers |

---

## Foundation Document Standards (`docs/`)

Foundation documents describe the system at the project level. They are long-lived and change only when the system changes meaningfully.

### Required Frontmatter

Every foundation document begins with a metadata block:

```markdown
# Document Title

**Version:** {semver}  
**Status:** {Draft | Active | Deprecated | Superseded by {link}}  
**Last Updated:** {YYYY-MM-DD}  
**Owner:** {team or individual}
```

### Writing Style

- Use active voice. "The orchestrator routes tasks" not "Tasks are routed by the orchestrator."
- Use present tense for how the system works; past tense for historical decisions.
- Define terms on first use. Do not assume the reader knows internal terminology.
- Every major design decision includes the reasoning: what alternatives were considered, why this approach was chosen, what would need to change for the decision to be revisited.
- Avoid hedging language ("might", "could", "perhaps") unless genuine uncertainty is intended.

### Headings

Use `##` for major sections, `###` for subsections. Do not use `####` — if you need a fourth level, the section should be broken into its own document.

### Length

Foundation documents should be as long as they need to be and no longer. Prefer complete sentences over bullet fragments for explanatory content. Prefer tables and lists for reference content.

---

## Architectural Decision Records (ADRs)

Every significant architectural decision is recorded as an ADR in `docs/DECISIONS.md`. "Significant" means: a decision that future engineers would need to know about to understand why the system is built the way it is.

### What Qualifies as an ADR

- Choice of programming language or framework
- Choice of data storage mechanism
- Selection of a third-party dependency
- Introduction of a new architectural pattern
- A security tradeoff
- A deliberate decision to defer a feature or capability

### What Does Not Qualify

- How to name a variable
- Which developer wrote a function
- Decisions that are obvious from the code itself

### ADR Format

```markdown
## ADR-{NNN}: {Title}

**Date:** {YYYY-MM-DD}  
**Status:** {Proposed | Accepted | Deprecated | Superseded by ADR-NNN}  
**Deciders:** {who made this decision}

### Context

{What situation prompted this decision? What problem were we solving?}

### Decision

{What did we decide to do?}

### Alternatives Considered

{What other options were evaluated and why were they not chosen?}

### Consequences

{What becomes easier? What becomes harder? What do we now need to watch out for?}
```

### ADR Numbering

ADRs are numbered sequentially starting at `ADR-001`. Numbers are never reused or reassigned.

---

## Module README Standards

Every top-level directory in the project (`core/`, `memory/`, `knowledge/`, etc.) contains a `README.md`.

### Required Sections

```markdown
# {Module Name}

## Purpose

{One paragraph: what this module does and why it exists.}

## Responsibilities

{Bulleted list of what this module owns.}

## What This Module Does NOT Own

{Explicit boundaries — what belongs elsewhere. This prevents scope creep.}

## Public Interface

{Key classes and functions exposed by this module, with brief descriptions.}

## Dependencies

{Other internal modules this module depends on, and why.}

## Configuration

{What configuration values does this module read? What are their defaults?}
```

---

## Changelog Standards

`docs/CHANGELOG.md` follows the [Keep a Changelog](https://keepachangelog.com/) format.

```markdown
## [0.2.0] — 2026-09-01

### Added
- Task priority queue in orchestrator

### Changed
- Memory layer now stores entries as structured JSON instead of plain text

### Fixed
- Knowledge retrieval returning stale entries after index regeneration

### Deprecated
- Legacy `run_task()` API; use `execute_task()` instead

### Removed
- Experimental streaming response handler (superseded by integration layer)

### Security
- Rotate all API keys stored in config files (keys should never be in config)
```

Every version entry must appear before code for that version is merged to `main`. The changelog is updated in the same PR as the feature or fix.

---

## Inline Code Documentation

### Docstrings

Public interfaces receive a docstring if the function name alone is insufficient to understand the contract:

```python
def execute_task(task: Task, context: ExecutionContext) -> TaskResult:
    """Execute a task using the assigned agent and return the structured result."""
```

Multi-line docstrings are used only when the contract includes non-obvious pre/post conditions or important error behavior:

```python
def write_knowledge_entry(entry: KnowledgeEntry) -> EntryId:
    """Persist a knowledge entry and rebuild the search index.

    Raises KnowledgeConflictError if an entry with the same
    signature already exists. Use update_knowledge_entry() to
    supersede an existing entry.
    """
```

### Inline Comments

See [ENGINEERING_STANDARDS.md](ENGINEERING_STANDARDS.md) for inline comment policy. Comments explain WHY, not WHAT. Absent a good reason, do not comment.

---

## Knowledge Entry Standards

Knowledge entries in `knowledge/` are written by AI agents and humans. They follow a strict schema to enable structured retrieval.

See [KNOWLEDGE_SYSTEM.md](KNOWLEDGE_SYSTEM.md) for the full entry schema. The key requirement: every entry is self-contained. A reader who has never seen the original problem should be able to understand the entry completely.

---

## Documentation Review Checklist

When reviewing a PR that includes documentation changes, verify:

- [ ] Frontmatter is present and current (version, status, date)
- [ ] All linked documents exist (no broken links)
- [ ] New terms are defined on first use
- [ ] Decision reasoning is included, not just the decision itself
- [ ] No information is duplicated across documents (link instead)
- [ ] Changelog is updated if the change is user-facing
- [ ] ADR is added if an architectural decision was made
- [ ] Module README is updated if the module's interface or purpose changed

---

## Documentation Debt Policy

Documentation that is known to be incorrect or outdated is marked with a `> **Warning:** This section is outdated.` blockquote at the top, with a link to the relevant task tracking the update.

Documentation debt is tracked as tasks in the task system with priority `P2`. It is not allowed to accumulate without a tracking entry.

Incorrect documentation is worse than missing documentation. If you cannot update it now, mark it as outdated.
