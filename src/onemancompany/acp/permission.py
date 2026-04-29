"""ACP Policy Engine — non-blocking, YAML-driven permission evaluation.

Rules are loaded once at construction time and evaluated in-process (<1 ms).
There is no human-in-the-loop; every ``decide()`` call returns instantly.

Usage::

    from onemancompany.acp.permission import PolicyEngine

    engine = PolicyEngine(Path("company_rules/permissions.yaml"))
    decision = engine.decide(tool="write", args={}, context={"owner_is_self": False})
    if not decision.allowed:
        raise PermissionError(decision.reason)

Rule matching semantics
-----------------------
Rules are evaluated **top-to-bottom**; the **first match wins**.

Each rule's ``match`` dict may contain any combination of:

* ``tool`` — exact string match against the *tool* argument.
* ``cost_usd_gt`` — numeric threshold; matches when ``context["cost_usd"] > value``.
* Any other key — looked up in the merged ``{**args, **context}`` dict and compared
  for equality (``True``/``False`` values included).

If no rule matches, the ``default`` action (``allow`` or ``reject``) applies.
If the rules file is missing, the engine logs a warning and defaults to allow-all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass
class PolicyDecision:
    """Result of a single policy evaluation."""

    allowed: bool
    reason: str = ""


@dataclass
class PolicyRule:
    """A single parsed rule from the YAML rules list."""

    match: dict[str, Any]
    action: str  # "allow" | "reject"
    reason: str = ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """Evaluate ACP tool calls against a YAML policy file.

    Parameters
    ----------
    rules_path:
        Path to the YAML policy file.  If the file does not exist the engine
        silently falls back to allow-all and logs a warning.
    """

    def __init__(self, rules_path: Path) -> None:
        self._rules_path = rules_path
        self._rules: list[PolicyRule] = []
        self._default_allow: bool = True
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Parse the YAML file and populate ``_rules`` / ``_default_allow``."""
        if not self._rules_path.exists():
            logger.warning(
                "PolicyEngine: rules file not found at {path!r} — defaulting to allow-all",
                path=str(self._rules_path),
            )
            return

        logger.debug("PolicyEngine: loading rules from {path!r}", path=str(self._rules_path))

        with self._rules_path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}

        default_str: str = data.get("default", "allow")
        self._default_allow = default_str.strip().lower() != "reject"

        raw_rules: list[dict[str, Any]] = data.get("rules", []) or []
        for raw in raw_rules:
            self._rules.append(
                PolicyRule(
                    match=raw.get("match", {}),
                    action=raw.get("action", "allow"),
                    reason=raw.get("reason", ""),
                )
            )

        logger.debug(
            "PolicyEngine: loaded {n} rules, default={default}",
            n=len(self._rules),
            default="allow" if self._default_allow else "reject",
        )

    def _rule_matches(
        self,
        rule: PolicyRule,
        tool: str,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        """Return True iff *all* match conditions in *rule* are satisfied."""
        merged: dict[str, Any] = {**args, **context}

        for key, expected in rule.match.items():
            # Special case: exact tool name match
            if key == "tool":
                if tool != expected:
                    return False
                continue

            # Special case: cost threshold  (cost_usd_gt → context["cost_usd"] > value)
            if key == "cost_usd_gt":
                cost: float = float(merged.get("cost_usd", 0.0))
                if not (cost > float(expected)):
                    return False
                continue

            # General equality check against merged args+context
            if merged.get(key) != expected:
                return False

        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decide(
        self,
        tool: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Evaluate *tool* call against loaded rules.

        Parameters
        ----------
        tool:
            Name of the tool being invoked.
        args:
            Tool arguments dict (may be empty).
        context:
            Caller-supplied context values (e.g. ``cost_usd``, ``owner_is_self``).
            Defaults to an empty dict when ``None``.

        Returns
        -------
        PolicyDecision
            ``allowed=True`` if the call is permitted, ``False`` otherwise.
        """
        ctx: dict[str, Any] = context if context is not None else {}

        for rule in self._rules:
            if self._rule_matches(rule, tool, args, ctx):
                allowed = rule.action.strip().lower() == "allow"
                logger.debug(
                    "PolicyEngine: rule matched — tool={tool!r} action={action!r} reason={reason!r}",
                    tool=tool,
                    action=rule.action,
                    reason=rule.reason,
                )
                return PolicyDecision(allowed=allowed, reason=rule.reason)

        # No rule matched — use default
        logger.debug(
            "PolicyEngine: no rule matched for tool={tool!r} — using default ({default})",
            tool=tool,
            default="allow" if self._default_allow else "reject",
        )
        return PolicyDecision(allowed=self._default_allow)
