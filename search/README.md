# search

Unified full-text retrieval across all MondayOS data sources.

## Responsibility

The search module provides a single interface for querying knowledge entries, tasks, and memory. Agents always search before acting — retrieval is what enables the system to avoid repeating solved problems.

## What This Module Does NOT Own

- The data being searched. Knowledge entries live in `knowledge/`, tasks in `tasks/`.
- Indexing of knowledge entries. The `KnowledgeIndex` in `knowledge/` maintains its own index; the search engine queries it.
- Ranking models or ML infrastructure. Phase 1 is pure keyword search.

## Public Interface

| Symbol | Description |
|---|---|
| `SearchEngine` | The single search interface — call `.search(query)` |
| `SearchQuery` | What to search: text, sources, tags, components, limit |
| `SearchResult` | One result: source, entry_id, title, snippet, score |

## Usage Pattern

```python
from search import SearchEngine, SearchQuery

engine = SearchEngine()
results = engine.search(SearchQuery(
    text="rate limit retry",
    sources=["knowledge"],
    limit=5,
))
for r in results:
    print(f"{r.score:.2f}  {r.entry_id}  {r.title}")
```

## Phase Roadmap

| Phase | Search Method |
|---|---|
| 1 | Keyword (TF-IDF or simple substring over Markdown files) |
| 2 | Semantic (embedding-based, local model) |
| 3 | Hybrid (keyword + semantic + re-ranking) |

The `SearchEngine` interface does not change between phases.

## Dependencies

- `core.types` — `EntityId`
- `knowledge` — `KnowledgeIndex` for indexed knowledge lookups
- `tasks` — `TaskManager` for task content queries
