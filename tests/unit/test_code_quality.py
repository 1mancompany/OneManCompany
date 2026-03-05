"""Code quality lint tests — enforce coding standards across the codebase."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "onemancompany"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iter_python_files():
    """Yield all .py files under src/onemancompany/."""
    for p in sorted(SRC_DIR.rglob("*.py")):
        if p.name.startswith("__"):
            continue
        yield p


def _get_except_handlers(tree: ast.Module) -> list[tuple[int, ast.ExceptHandler, str]]:
    """Walk AST and return all except handlers with their source context."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            handlers.append(node)
    return handlers


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """Return True if the except handler does nothing visible.

    Silent means the body contains ONLY:
      - pass
      - continue
      - bare expressions that aren't function calls (e.g. `...`)

    NOT silent if the body contains any of:
      - function call (logger.warning, print, etc.)
      - raise
      - assignment (capturing the exception for later use)
      - return with a value
    """
    for stmt in handler.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Continue):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            # Bare `...` or string literal (docstring)
            continue
        # Anything else = not silent
        return False
    return True


# Allowed exceptions: patterns where silent handling is intentional.
# Format: (filename_stem, exception_type, line_range_start, line_range_end)
# Keep this list minimal — each entry needs a comment justifying it.
ALLOWLIST: list[tuple[str, str | None, int, int]] = [
    # asyncio.CancelledError during graceful shutdown is expected
    ("main", "CancelledError", 430, 450),
    ("websocket", "CancelledError", 45, 60),
    # int() on non-numeric employee IDs — intentional skip
    ("state", "ValueError", 320, 340),
    ("state", "ValueError", 540, 560),
]


def _is_allowlisted(filepath: Path, handler: ast.ExceptHandler) -> bool:
    stem = filepath.stem
    exc_name = None
    if handler.type:
        if isinstance(handler.type, ast.Name):
            exc_name = handler.type.id
        elif isinstance(handler.type, ast.Attribute):
            exc_name = handler.type.attr
    for a_stem, a_exc, a_start, a_end in ALLOWLIST:
        if stem == a_stem and (a_exc is None or a_exc == exc_name):
            if a_start <= handler.lineno <= a_end:
                return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoSilentExcept:
    """Ensure no except block silently swallows errors without logging."""

    def test_no_silent_except_pass(self):
        """Every except block must do something visible (log, raise, return, assign)."""
        violations = []
        for filepath in _iter_python_files():
            source = filepath.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(filepath))
            except SyntaxError:
                continue
            for handler in _get_except_handlers(tree):
                if _handler_is_silent(handler) and not _is_allowlisted(filepath, handler):
                    rel = filepath.relative_to(SRC_DIR.parent.parent)
                    exc_type = "Exception"
                    if handler.type:
                        exc_type = ast.dump(handler.type)
                    violations.append(
                        f"  {rel}:{handler.lineno} — silent `except {exc_type}:`"
                    )

        if violations:
            report = "\n".join(violations)
            pytest.fail(
                f"Found {len(violations)} silent except block(s) — "
                f"add logging or a comment explaining why:\n{report}"
            )


class TestNoStdlibLogging:
    """Ensure loguru is used instead of stdlib logging."""

    def test_no_import_logging(self):
        """No file should use `import logging` — use `from loguru import logger`."""
        violations = []
        for filepath in _iter_python_files():
            source = filepath.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(filepath))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "logging":
                            rel = filepath.relative_to(SRC_DIR.parent.parent)
                            violations.append(f"  {rel}:{node.lineno} — `import logging`")

        if violations:
            report = "\n".join(violations)
            pytest.fail(
                f"Found {len(violations)} stdlib logging import(s) — "
                f"use `from loguru import logger` instead:\n{report}"
            )

    def test_no_getlogger(self):
        """No file should call logging.getLogger — use loguru's global logger."""
        violations = []
        for filepath in _iter_python_files():
            source = filepath.read_text(encoding="utf-8")
            if "logging.getLogger" in source:
                # Find line numbers
                for i, line in enumerate(source.splitlines(), 1):
                    if "logging.getLogger" in line:
                        rel = filepath.relative_to(SRC_DIR.parent.parent)
                        violations.append(f"  {rel}:{i} — `logging.getLogger`")

        if violations:
            report = "\n".join(violations)
            pytest.fail(
                f"Found {len(violations)} logging.getLogger call(s) — "
                f"use `from loguru import logger` instead:\n{report}"
            )
