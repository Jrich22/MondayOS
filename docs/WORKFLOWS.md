# MondayOS — Workflow System

**Version:** 1.0  
**Status:** Active  
**Last Updated:** 2026-06-28

---

## Overview

A MondayOS workflow is a named, ordered sequence of steps that coordinate the system's subsystems — knowledge capture, task management, search, and human approval — without requiring direct model calls. Workflows are defined in YAML files and executed by the `WorkflowEngine`.

Every workflow run produces a structured execution log. Every step output is captured in the execution context, making later steps aware of what earlier steps found or created.

---

## Workflow Definition Format

Workflow definitions live in `workflows/definitions/*.yaml`. Each file defines one workflow.

```yaml
name: my-workflow          # required; used to invoke the workflow
version: "1.0"             # required; recorded in every execution log
description: >             # optional; shown by 'monday workflow show'
  What this workflow does.

triggers:                  # advisory; not enforced in Phase 1
  - human-request

inputs:                    # declared input variables
  function_name:
    description: Name of the function to implement
    required: true
  component:
    description: Component this function belongs to
    required: false
    default: core

steps:
  - id: research           # required; unique within the workflow
    type: ask              # required; see Step Types below
    description: Human-readable summary of this step
    input:
      prompt: "What do we know about {inputs.function_name}?"
```

### Required Fields

| Field     | Description |
|-----------|-------------|
| `name`    | Workflow identifier. Used by CLI and API to invoke the workflow. |
| `version` | Semantic version string. Recorded in every execution log. |
| `steps`   | Ordered list of step objects. Must contain at least one step. |

Each step requires `id` (unique string) and `type` (one of the step types below).

---

## Template Substitution

Step `input` values support `{placeholder}` substitution. Placeholders are resolved at runtime from the execution context:

| Placeholder pattern       | Resolves to |
|---------------------------|-------------|
| `{inputs.variable_name}`  | A declared workflow input variable |
| `{step_id.output_key}`    | An output from a previously completed step |

Unknown placeholders are left as literal text (no error).

**Example:**
```yaml
steps:
  - id: create-task
    type: task_create
    input:
      title: "Implement {inputs.function_name}"
      objective: "Task created by workflow. Research: {research.answer}"
```

After `create-task` runs, `{create-task.task_id}` is available to all later steps.

---

## Step Types

### `ask`

Calls `Monday.ask()` with a prompt. Searches internal knowledge and returns a synthesized answer.

```yaml
- id: research
  type: ask
  input:
    prompt: "What do we know about {inputs.topic}?"
```

**Outputs:** `answer`, `confidence`, `sources`, `model_used`

---

### `search`

Calls `Monday.search()` with a query string.

```yaml
- id: find-related
  type: search
  input:
    query: "{inputs.function_name}"
    limit: 5         # optional; default 10
```

**Outputs:** `results` (list), `total_found` (int)

---

### `learn`

Calls `Monday.learn()` to add a knowledge entry.

```yaml
- id: capture-pattern
  type: learn
  input:
    title: "Pattern: {inputs.function_name}"
    entry_type: pattern
    content: |
      ## {inputs.function_name}
      Task: {create-task.task_id}
      Findings: {research.answer}
    tags:
      - implementation
      - "{inputs.component}"
```

**Required input:** `content`  
**Outputs:** `entry_id`, `accepted`

---

### `task_create`

Calls `Monday.task("create")` to create a new task.

```yaml
- id: create-task
  type: task_create
  input:
    title: "Implement {inputs.function_name}"
    objective: "Implement the function in {inputs.component}"
    task_type: feature    # optional; default feature
    priority: P2          # optional; default P2
    created_by: "workflow:implement-function/1.0"  # optional
```

**Required input:** `title`, `objective`  
**Outputs:** `task_id`, `success`

---

### `task_start`

Transitions a task from BACKLOG → ASSIGNED → IN_PROGRESS in two hops.

```yaml
- id: start-task
  type: task_start
  input:
    task_id: "{create-task.task_id}"
```

**Required input:** `task_id`  
**Outputs:** `task_id`, `success`, `status`

---

### `task_complete`

Calls `Monday.task("complete")` to mark a task as COMPLETED.

```yaml
- id: done
  type: task_complete
  input:
    task_id: "{create-task.task_id}"
    reason: "Completed. Pattern: {capture-pattern.entry_id}"
```

**Required input:** `task_id`  
**Outputs:** `task_id`, `success`

---

### `human_approval`

Pauses the workflow and calls the configured approval handler. If the handler returns `False`, the workflow is cancelled with `WorkflowStatus.CANCELLED`.

```yaml
- id: approval-gate
  type: human_approval
  message: |
    Ready to proceed.
    Task: {create-task.task_id}
    Findings: {research.answer}
    Approve?
```

The `message` field supports full template substitution. In the CLI, this triggers a terminal prompt. In tests or automation, inject an `approval_handler` callable.

**Outputs:** `approved` (bool, always `True` if the step completes — rejection raises `ApprovalDenied` and cancels the workflow)

---

## Execution Lifecycle

When `WorkflowEngine.run()` is called:

1. The named workflow is loaded from `workflows/definitions/{name}.yaml`.
2. User-supplied inputs are merged with declared defaults.
3. An execution context is initialised: `{inputs.variable_name: value}` for all inputs.
4. Each step executes in order:
   - The step's `input` dict is resolved (template substitution from context).
   - The step's action is dispatched to the `Monday` public API.
   - Outputs are stored in context as `{step_id.output_key}`.
5. If a step raises `StepExecutionError`, the workflow status is set to `FAILED` and no further steps run.
6. If a `human_approval` step is rejected, the workflow status is set to `CANCELLED` and no further steps run.
7. On completion (any status), the execution log is written to `logs/workflows/`.

---

## Execution Log

Every workflow run writes a JSON file to `logs/workflows/{workflow_name}-{execution_id[:8]}.json`.

The log records:

```json
{
  "execution_id": "a3f2c1d8-...",
  "workflow_name": "implement-function",
  "workflow_version": "1.0",
  "status": "completed",
  "started_at": "2026-06-28T12:00:00+00:00",
  "completed_at": "2026-06-28T12:00:05+00:00",
  "inputs": {"function_name": "parse_config", "component": "core"},
  "context": {"inputs.function_name": "parse_config", "research.answer": "...", ...},
  "steps": [
    {
      "step_id": "research",
      "step_type": "ask",
      "status": "completed",
      "started_at": "...",
      "completed_at": "...",
      "input": {"prompt": "What do we know about parse_config?"},
      "output": {"answer": "...", "confidence": 0.3, "sources": []},
      "error": ""
    }
  ],
  "error": ""
}
```

---

## CLI Usage

```bash
# List all available workflow definitions
monday workflow list

# Inspect a workflow's steps and inputs
monday workflow show implement-function

# Run a workflow with required inputs (terminal approval gate)
monday workflow run implement-function --var function_name=parse_config

# Run with multiple inputs
monday workflow run implement-function \
  --var function_name=validate_input \
  --var component=tasks

# Run non-interactively (auto-approve all gates)
monday workflow run implement-function \
  --var function_name=parse_config \
  --yes
```

---

## Python API Usage

```python
from monday import Monday, MondayConfig

monday = Monday(MondayConfig(project_root=Path(".")))

# List available workflows
r = monday.workflow("list")
print(r.data["workflows"])

# Show workflow steps
r = monday.workflow("show", name="implement-function")
for step in r.data["steps"]:
    print(step["id"], step["type"])

# Run a workflow (terminal approval prompt by default)
r = monday.workflow(
    "run",
    name="implement-function",
    inputs={"function_name": "parse_config", "component": "core"},
)

# Run with a custom approval handler (e.g. auto-approve)
r = monday.workflow(
    "run",
    name="implement-function",
    inputs={"function_name": "parse_config"},
    approval_handler=lambda msg, ctx: True,
)
print(r.status)      # "completed"
print(r.execution_id)
```

---

## Adding a New Workflow

1. Create `workflows/definitions/{your-workflow-name}.yaml`.
2. Declare inputs with descriptions and defaults.
3. Define steps using the step types above.
4. Test with `monday workflow show your-workflow-name`.
5. Run with `monday workflow run your-workflow-name --var key=value --yes`.

No code changes are needed to add a new workflow. The engine discovers all `.yaml` files in `workflows/definitions/` automatically.

---

## Adding a New Step Type

1. Add the new type to `StepType` in `workflows/definition.py`.
2. Add a dispatch branch in `WorkflowEngine._execute_step()` in `workflows/engine.py`.
3. Add a corresponding action to `Monday.task()` or a new `Monday` method if needed.
4. Update this document.
5. Write tests in `tests/test_workflows.py`.

---

## Phase 2 Integration Points

The workflow system is designed to absorb LLM integration with no schema changes:

- A future `model_call` step type routes to the integration layer (`integrations/claude/`, `integrations/openai/`).
- The `approval_handler` protocol can be satisfied by a web UI in the Phase 2 dashboard.
- Workflow definitions can gain `parallel:` step groups for concurrent execution.
- The execution log format is stable and extends naturally with new fields.
