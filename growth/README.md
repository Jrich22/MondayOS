# growth

The Growth Bot service — project-isolated marketing workspaces, content, and approvals.

## Purpose

The Growth Bot plans, drafts, reviews, and approves marketing content on behalf of a MondayOS
project. It is one service used by many projects: sourcingBOT, Cue, and every project added later
share this engine and share none of their data. The full specification, including the increments
not yet built, is [docs/GROWTH_BOT.md](../docs/GROWTH_BOT.md).

**Nothing in this module can publish.** There is no connector, no scheduler, and no lifecycle state
beyond `approved`. That is the current increment boundary, and `tests/test_growth.py` asserts it
rather than leaving it as a claim in a docstring.

## Responsibilities

- Resolving a project name to exactly one Growth Workspace, and refusing anything else (ADR-011).
- Storing a project's business, brand, audience, and marketing state.
- Holding platform credentials **by reference** — a secret's name, never its value.
- Modelling the Content Item, its lifecycle, and the legal transitions between states.
- Computing and comparing approval fingerprints, so an edited item cannot stay approved (ADR-013).

## What This Module Does NOT Own

- **The human-approval policy.** That is `agents/gates.py::ApprovalGate`. This module registers
  `publish_content` as a gated action and defers the decision (ADR-012).
- **Publishing.** No outbound integration exists yet. When it does, it belongs in `integrations/`
  alongside the Confluence connector, not here.
- **The project registry.** Projects are registered through `monday/project.py`; growth reads it.
- **Agent execution.** Roles and providers belong to `agents/`.
- **Learned findings.** Growth Brain output will be knowledge entries in `knowledge/`.

## Public Interface

| Symbol | Description |
|---|---|
| `GrowthService` | The façade behind `Monday.growth()` |
| `GrowthStore` / `WorkspaceHandle` | Persistence; a handle is scoped to one project |
| `Workspace` + `Business` / `Brand` / `Audience` / `Marketing` | Workspace state |
| `PlatformBinding` / `SUPPORTED_PLATFORMS` | Credential references |
| `ContentItem` / `ContentStatus` / `ContentTransition` | The unit of approval |
| `compute_fingerprint` / `FINGERPRINTED_FIELDS` | The approval contract |
| `normalize_project_slug` / `resolve_project` | The isolation gate |
| `PUBLISH_ACTION` / `publish_action_is_gated` | The gated action name |

## Dependencies

- `monday.project` — the existing project registry; growth does not define a second one.
- `agents.gates` — the approval-gate policy and `GATED_ACTIONS`.
- `core.types` — shared `EntityId` and `Timestamp` aliases.

## Storage

Per-workspace, under the project root, following `tasks/` and `agents/`:

```
growth/workspaces/<slug>/
├── workspace.md              business, brand, audience, marketing, bindings
├── content/CONTENT-NNNN.md   one Content Item
└── .sequences.json           CONTENT- id allocation, scoped to THIS workspace
```

The sequence file is per workspace on purpose. A shared counter would let one project infer
another's publishing volume from the gaps in its own ids.

## Configuration

No configuration of its own. It reads `MondayConfig.project_root` through `Monday`, and resolves
platform credentials from environment variables named by each binding's `secret_name` — the
credential itself is never read into a workspace file, a log, or an agent prompt.
