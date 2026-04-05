---
tags: [development, testing, tdd]
source: vibe-coding-guide.md, docs/dev-test-workflow.md
---

# Testing Guide

## TDD Workflow

1. Write a failing test
2. Run it to confirm it fails
3. Implement minimum code to pass
4. Run to confirm it passes
5. Refactor if needed
6. Commit

## Test Patterns

### Mock at Importing Module

```python
# Good — patches where it's looked up
monkeypatch.setattr(recruitment, "load_app_config", lambda: {...})

# Bad — patches source module (won't affect importing module)
monkeypatch.setattr("onemancompany.core.config.load_app_config", lambda: {...})
```

### Async Tests

```python
@pytest.mark.asyncio
async def test_something(self, monkeypatch):
    ...
```

### WebSocket Tests

Don't use Starlette TestClient — `while True` receive loop hangs. Instead:

```python
mock_ws = MagicMock()
mock_ws.send_json = AsyncMock()
await handler(mock_ws)
mock_ws.send_json.assert_called_with(...)
```

### Employee ID Gotchas

Avoid `_EXEC_IDS` (00002-00005) in tests — these are founding executives with special behavior.

## Running Tests

```bash
# Full suite
.venv/bin/python -m pytest tests/unit/ -x -q

# Specific test
.venv/bin/python -m pytest tests/unit/agents/test_recruitment.py::TestSearchPassesUseAi -v

# With coverage
.venv/bin/python -m pytest tests/unit/ --cov=src/onemancompany
```

## Related
- [[Coding Standards]] — Code quality rules
- [[Design Principles]] — Why TDD is mandatory
