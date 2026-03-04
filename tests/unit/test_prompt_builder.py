"""Unit tests for PromptBuilder."""

from onemancompany.agents.prompt_builder import PromptBuilder


class TestPromptBuilder:
    def test_add_and_build(self):
        pb = PromptBuilder()
        pb.add("role", "You are an engineer.", priority=10)
        pb.add("skills", "You know Python.", priority=20)
        result = pb.build()
        assert "You are an engineer." in result
        assert "You know Python." in result
        # Role should come before skills
        assert result.index("engineer") < result.index("Python")

    def test_priority_ordering(self):
        pb = PromptBuilder()
        pb.add("last", "C", priority=30)
        pb.add("first", "A", priority=10)
        pb.add("middle", "B", priority=20)
        result = pb.build()
        assert result.index("A") < result.index("B") < result.index("C")

    def test_empty_content_skipped(self):
        pb = PromptBuilder()
        pb.add("role", "You are X.", priority=10)
        pb.add("empty", "", priority=20)
        pb.add("whitespace", "   ", priority=30)
        assert len(pb.section_names()) == 1
        assert "empty" not in pb.section_names()

    def test_remove_section(self):
        pb = PromptBuilder()
        pb.add("role", "X", priority=10)
        pb.add("skills", "Y", priority=20)
        pb.remove("skills")
        assert "Y" not in pb.build()
        assert not pb.has("skills")

    def test_has_section(self):
        pb = PromptBuilder()
        pb.add("role", "X")
        assert pb.has("role") is True
        assert pb.has("nonexistent") is False

    def test_get_section(self):
        pb = PromptBuilder()
        pb.add("role", "You are X.")
        assert pb.get("role") == "You are X."
        assert pb.get("nonexistent") == ""

    def test_replace_section(self):
        pb = PromptBuilder()
        pb.add("role", "Old role", priority=10)
        pb.add("role", "New role", priority=10)
        assert "New role" in pb.build()
        assert "Old role" not in pb.build()

    def test_section_names(self):
        pb = PromptBuilder()
        pb.add("c", "3", priority=30)
        pb.add("a", "1", priority=10)
        pb.add("b", "2", priority=20)
        assert pb.section_names() == ["a", "b", "c"]

    def test_chaining(self):
        result = (PromptBuilder()
                  .add("a", "X", priority=10)
                  .add("b", "Y", priority=20)
                  .remove("b")
                  .build())
        assert result == "X"
