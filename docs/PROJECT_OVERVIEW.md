# MondayOS — Project Overview

**Version:** 0.1.0  
**Status:** Foundation Phase  
**Last Updated:** 2026-06-27  
**Owner:** Lead Software Engineering

---

## What Is MondayOS?

MondayOS is an AI Operating System — a platform that coordinates multiple AI models (Claude, GPT-4, local Ollama models, and others) to build software, learn from experience, document every engineering decision, and manage long-term technical projects autonomously and collaboratively.

MondayOS is **not** a chatbot. It is not a code editor plugin. It is not a single-model wrapper.

It is a coordination layer that treats AI agents as first-class citizens of a software engineering organization: they receive tasks, execute work, produce artifacts, write documentation, surface accumulated knowledge, and escalate to humans when human judgment is required.

---

## The Problem MondayOS Solves

Modern software teams using AI tools face a predictable set of failure modes:

| Problem | Manifestation |
|---|---|
| Context amnesia | Every AI session starts from scratch; past decisions are lost |
| Siloed models | Claude, GPT, and local models cannot collaborate on the same task |
| Non-explainable actions | AI takes actions without leaving an audit trail |
| No learning loop | The same bugs are solved repeatedly across sessions |
| Human bottlenecks | AI blocks on trivial decisions that should have been pre-authorized |
| Documentation debt | Code accumulates; understanding of *why* it was written degrades |

MondayOS addresses each failure mode directly. It is designed so that every engineering decision — whether made by a human or an AI — is captured, reasoned about, and retrievable.

---

## Who MondayOS Is For

### Primary Users (Phase 1)
- Solo engineers and small teams using AI-assisted development
- Founders building AI-native software products

### Future Users (Phases 2–4)
- Engineering organizations that want AI agents as participating team members
- Platform teams requiring cross-model orchestration
- Enterprises requiring auditable, explainable AI-assisted development

---

## Core Capabilities

### 1. Multi-Model Orchestration
Routes tasks to the most appropriate AI model based on capability, cost, and latency requirements. Claude handles complex reasoning and code review. GPT-4 handles structured output generation. Local Ollama models handle low-latency, privacy-sensitive, or offline workloads. The routing logic is declarative and auditable.

### 2. Persistent Memory
All context — code decisions, bug resolutions, architecture choices, project state — is stored and retrievable across sessions. AI agents do not forget between runs.

### 3. Knowledge Accumulation
Every bug resolved, every decision made, every pattern discovered becomes a structured knowledge entry. The system learns continuously. Problems are never solved twice.

### 4. Task Coordination
Tasks are created, assigned, tracked, and completed by AI agents. Humans can inject tasks, approve results, and redirect in-progress work. The task graph is the authoritative record of what is happening and why.

### 5. Audit Trail
Every AI action — code change, task assignment, knowledge entry, external API call — is logged with a timestamp, model attribution, reasoning trace, and human-readable explanation.

### 6. Human-in-the-Loop Gates
Any action affecting production systems, external services, or irreversible state requires explicit human approval before execution. Autonomy is bounded and configurable.

---

## Core Principles

These are non-negotiable. They govern every engineering decision in this codebase.

1. **Never solve the same problem twice.** Every bug resolution and decision is captured as reusable knowledge.
2. **Every change is documented.** Code changes, architectural shifts, and task completions are self-documenting.
3. **Every AI action is explainable.** No action is taken without a reasoning trace attached to it.
4. **Git is the source of truth.** All persistent state that matters lives in version control.
5. **Human approval gates production.** Autonomous action is bounded. Humans own the blast radius.
6. **Simplicity over cleverness.** The system must be understandable by a new engineer in one working day.
7. **Tests protect every important feature.** Features without tests do not exist in production.
8. **Observability from day one.** Every subsystem emits structured logs and metrics.

---

## What MondayOS Is Not

- Not a replacement for version control — it complements Git.
- Not a CI/CD pipeline — it integrates with existing pipelines.
- Not an IDE — it operates above the editor layer.
- Not tied to a single AI provider — provider independence is a first-class architectural concern.
- Not a replacement for human engineers — it amplifies their output and institutional memory.

---

## Directory Structure

```
MondayOS/
├── docs/               # All project documentation (you are here)
├── core/               # Core orchestration engine and shared runtime
├── orchestrator/       # Multi-model task routing and agent coordination
├── memory/             # Persistent memory and cross-session state
├── knowledge/          # Knowledge base, retrieval, and learning system
├── tasks/              # Task graph management and execution tracking
├── workflows/          # Predefined multi-step agent workflow definitions
├── integrations/       # External model and tool integrations (Claude, GPT, Ollama)
├── prompts/            # Versioned prompt templates
├── dashboard/          # Monitoring, observability, and approval UI
├── config/             # Environment and system configuration
└── logs/               # Structured operational logs
```

Each directory contains its own `README.md` documenting its purpose, public interface, and ownership.

---

## Document Map

| Document | Purpose |
|---|---|
| [VISION.md](VISION.md) | Long-term goals and product philosophy |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical design and component relationships |
| [ENGINEERING_STANDARDS.md](ENGINEERING_STANDARDS.md) | Code quality, review, and testing standards |
| [DOCUMENTATION_STANDARDS.md](DOCUMENTATION_STANDARDS.md) | How documentation is written and maintained |
| [KNOWLEDGE_SYSTEM.md](KNOWLEDGE_SYSTEM.md) | Design of the knowledge accumulation layer |
| [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) | Design of the session and long-term memory layer |
| [TASK_SYSTEM.md](TASK_SYSTEM.md) | Design of the task coordination and execution system |
| [ROADMAP.md](ROADMAP.md) | Phased development plan and milestone tracking |
| [DECISIONS.md](DECISIONS.md) | Architectural Decision Record (ADR) log |
| [CHANGELOG.md](CHANGELOG.md) | Version history and release notes |
