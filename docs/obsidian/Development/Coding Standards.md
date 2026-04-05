---
tags: [development, standards]
source: vibe-coding-guide.md, MEMORY.md
---

# Coding Standards

## Style

- `loguru.logger` not print/stdlib logging
- Lazy imports for heavy/circular deps
- Type hints on public APIs
- Constants/enums over string literals

## Error Handling

- No `except Exception: pass` — must log
- `asyncio.CancelledError` must re-raise
- Use `_tool_error()` for structured tool error responses

## Testing

- **TDD**: write test first, then implement
- **Mock at importing module level**, not source module
  - `patch("onemancompany.agents.recruitment.load_app_config")` not `patch("onemancompany.core.config.load_app_config")`
- WebSocket tests: don't use Starlette TestClient (`while True` hangs) — mock WebSocket object directly
- Employee IDs: avoid `_EXEC_IDS` (00002-00005) in tests
- Use `.venv/bin/python -m pytest tests/unit/ -x -q` for full suite

## Code Smells to Avoid

- Dead code (empty functions, unused variables, commented-out blocks)
- String literals for values that should be constants/enums
- Duplicate systems (same data via two paths = [[No Duplicate Systems|Critical defect]])
- In-memory flags that are lost on restart (violates [[Design Principles|Complete Data Packages]])

## Compilation Check

```bash
.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"
```

## Related
- [[Design Principles]] — Why these rules exist
- [[Testing Guide]] — Detailed testing patterns
- [[Git Workflow]] — How to submit changes
