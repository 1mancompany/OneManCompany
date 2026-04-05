---
tags: [development, principles, core]
source: docs/design-principles.md, vibe-coding-guide.md
---

# Design Principles

> The load-bearing walls of this codebase. Violate them and things break in subtle ways.

## 1. Disk Is the Only Truth

Every piece of data has exactly **one file** that owns it and exactly **one write function** (`store.save_*()`). Memory holds only intermediate computation.

- Reads always go to disk (`store.load_*()`)
- Frontend is a pure render layer — fetches from REST API, no local state cache
- Backend-frontend sync: 3-second dirty tick → `state_changed` broadcast → frontend re-fetches

**Test**: Can you restart the server and lose nothing?

→ See [[Disk as Single Truth]]

## 2. Systematic Design, Not Patching

Every change must be a **systematic design**. If a bug reveals a structural flaw, fix the structure.

**Bad**: `if employee_id == "00003": ...`
**Good**: Extract a protocol/registry that handles all cases uniformly.

**Test**: Would a second similar request require touching the same code?

## 3. Modular, General-Purpose, Common Design

Registry/dispatch over if-elif chains. New capabilities addable without modifying existing code.

**Test**: Can a new use case be added by only writing new code?

## 4. Complete Data Packages

Any new state must be: **Serializable** (YAML/JSON), **Recoverable** (survives restart), **Registered** (in company state + owning employee), **Terminable** (clear lifecycle, won't be stuck forever).

## 5. No Silent Exceptions

Never `except Exception: pass`. Always log errors. Always re-raise `asyncio.CancelledError`.

## 6. Debug Logging at Key Points

Function entry params, branch decisions, external call results, state changes. Users deploy with INFO, `--debug` / `OMC_DEBUG=1` enables DEBUG. Diagnostic logs stay permanently.

## 7. No Duplicate Systems

→ See [[No Duplicate Systems]]

## 8. Manifest-Driven UI

UI sections declared by data files, not hardcoded.

## Related
- [[Coding Standards]] — Implementation rules
- [[No Duplicate Systems]] — Iron law
- [[Disk as Single Truth]] — Principle #1 deep dive
