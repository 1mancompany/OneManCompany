"""Composable prompt builder — eliminates duplicated _build_prompt() across agents.

Each agent adds named sections with priorities. The builder sorts by priority
and joins them. Subclasses only need to override _customize_prompt().
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PromptSection:
    """A named section of the system prompt."""
    name: str
    content: str
    priority: int = 50  # lower = appears earlier in prompt


class PromptBuilder:
    """Composable prompt section system.

    Usage::

        pb = PromptBuilder()
        pb.add("role", "You are an engineer...", priority=10)
        pb.add("skills", skills_content, priority=20)
        pb.add("efficiency", rules, priority=80)
        prompt = pb.build()
    """

    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}

    def add(self, name: str, content: str, priority: int = 50) -> "PromptBuilder":
        """Add or replace a named section. Empty content is silently skipped."""
        if content and content.strip():
            self._sections[name] = PromptSection(name=name, content=content, priority=priority)
        return self

    def remove(self, name: str) -> "PromptBuilder":
        """Remove a section by name."""
        self._sections.pop(name, None)
        return self

    def has(self, name: str) -> bool:
        """Check if a section exists."""
        return name in self._sections

    def get(self, name: str) -> str:
        """Get a section's content, or empty string."""
        s = self._sections.get(name)
        return s.content if s else ""

    def build(self) -> str:
        """Build the final prompt by joining sections sorted by priority."""
        sections = sorted(self._sections.values(), key=lambda s: s.priority)
        return "\n\n".join(s.content for s in sections)

    def section_names(self) -> list[str]:
        """Return all section names in priority order."""
        sections = sorted(self._sections.values(), key=lambda s: s.priority)
        return [s.name for s in sections]
