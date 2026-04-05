---
tags: [decision, architecture, data]
source: docs/design-principles.md, MEMORY.md
---

# Disk as Single Source of Truth

All business data lives only on disk. Memory holds no cached copies.

## Rules

- `core/store.py` is the unified read/write layer
- All reads go through `store.load_*()`, all writes through `store.save_*()`
- Frontend is a pure render layer — fetches from REST API, no local state
- Backend-frontend sync: 3-second dirty tick → `state_changed` broadcast → frontend re-fetches

## DirtyCategory System

```python
class DirtyCategory(Enum):
    EMPLOYEES = "employees"
    PROJECTS = "projects"
    CANDIDATES = "candidates"
    ...
```

`mark_dirty(category)` → next sync frame broadcasts changes → frontend re-fetches relevant data.

## Why

- Server restart loses nothing
- No cache invalidation bugs
- Single write function = single place to debug
- Git-friendly data format (YAML/JSON/Markdown)

## Data Locations

| Data | Path | Format |
|------|------|--------|
| Employee profiles | `employees/{id}/profile.yaml` | YAML |
| Task trees | `iterations/{iter}/task_tree.yaml` | YAML |
| Company state | `company_culture.yaml`, `company_direction.yaml` | YAML |
| Project archives | `project_archive/{id}/` | JSON |
| Config | `config.yaml`, `.env` | YAML, dotenv |

## Related
- [[Design Principles]] — Principle #1
- [[Agent Loop]] — Snapshot & recovery mechanism
