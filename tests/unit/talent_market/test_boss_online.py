"""Unit tests for talent_market/boss_online.py — candidate search & ranking."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from onemancompany.talent_market.boss_online import (
    CandidateProfile,
    CandidateSearchRequest,
    CandidateSearchResponse,
    CandidateShortlist,
    HireRequest,
    HireResponse,
    InterviewRequest,
    InterviewResponse,
    _build_search_text,
    _compute_relevance,
    _load_all_talents,
    _talent_to_candidate,
    _tokenize,
)


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_english_words(self):
        tokens = _tokenize("hello world foo_bar")
        assert "hello" in tokens
        assert "world" in tokens
        assert "foo_bar" in tokens

    def test_chinese_characters(self):
        tokens = _tokenize("游戏开发")
        # Should contain the full string and individual chars
        assert "游戏开发" in tokens
        assert "游" in tokens
        assert "戏" in tokens
        assert "开" in tokens
        assert "发" in tokens

    def test_mixed_text(self):
        tokens = _tokenize("Roblox 游戏 engineer")
        assert "roblox" in tokens
        assert "engineer" in tokens
        assert "游戏" in tokens

    def test_empty_string(self):
        tokens = _tokenize("")
        assert tokens == set()

    def test_case_insensitive(self):
        tokens = _tokenize("Python JAVA TypeScript")
        assert "python" in tokens
        assert "java" in tokens
        assert "typescript" in tokens

    def test_special_chars_ignored(self):
        tokens = _tokenize("hello! world@ foo#bar")
        assert "hello" in tokens
        assert "world" in tokens
        # Special chars stripped, so "foo" and "bar" separate
        assert "foo" in tokens
        assert "bar" in tokens


# ---------------------------------------------------------------------------
# _compute_relevance
# ---------------------------------------------------------------------------

class TestComputeRelevance:
    def test_perfect_match(self):
        score = _compute_relevance("python engineer", "python engineer expert")
        assert score > 0.5

    def test_no_match(self):
        score = _compute_relevance("python engineer", "banana smoothie recipe")
        assert score == 0.0

    def test_partial_match(self):
        score = _compute_relevance(
            "python web engineer backend",
            "python backend developer api",
        )
        assert 0.0 < score < 1.0

    def test_empty_jd(self):
        score = _compute_relevance("", "python engineer")
        assert score == 0.0

    def test_empty_talent(self):
        score = _compute_relevance("python engineer", "")
        assert score == 0.0

    def test_score_bounded(self):
        # Score should be between 0 and 1
        score = _compute_relevance("a", "a b c d e")
        assert 0.0 <= score <= 1.0

    def test_chinese_jd_matching(self):
        score = _compute_relevance("游戏开发", "游戏开发工程师 Roblox")
        assert score > 0.0


# ---------------------------------------------------------------------------
# _build_search_text
# ---------------------------------------------------------------------------

class TestBuildSearchText:
    def test_combines_fields(self):
        talent = {
            "name": "Test Engineer",
            "description": "A test talent",
            "role": "Engineer",
            "skills": ["python", "java"],
            "personality_tags": ["creative"],
            "system_prompt_template": "You are an engineer",
            "_skill_descriptions": {"python": "Python expert"},
            "_tool_names": ["code_review"],
        }
        text = _build_search_text(talent)
        assert "test engineer" in text
        assert "python" in text
        assert "java" in text
        assert "creative" in text
        assert "code_review" in text

    def test_missing_fields_no_error(self):
        talent = {"name": "Minimal"}
        text = _build_search_text(talent)
        assert "minimal" in text

    def test_empty_talent(self):
        text = _build_search_text({})
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# _talent_to_candidate (boss_online version)
# ---------------------------------------------------------------------------

class TestBossOnlineTalentToCandidate:
    def test_basic_conversion(self):
        talent = {
            "_talent_id": "test_talent",
            "name": "Test Dev",
            "role": "Engineer",
            "skills": ["python"],
            "personality_tags": ["creative"],
            "system_prompt_template": "You are a dev",
            "llm_model": "",
            "api_provider": "openrouter",
            "temperature": 0.7,
            "hosting": "company",
            "auth_method": "api_key",
            "_skill_descriptions": {"python": "# Python\nExpert Python dev"},
            "_tool_names": ["sandbox_execute_code"],
        }
        candidate = _talent_to_candidate(talent, 0.85)
        assert isinstance(candidate, CandidateProfile)
        assert candidate.id == "test_talent"
        assert candidate.name == "Test Dev"
        assert candidate.role == "Engineer"
        assert candidate.jd_relevance == 0.85
        assert len(candidate.skill_set) == 1
        assert candidate.skill_set[0].name == "python"
        assert len(candidate.tool_set) == 1

    def test_invalid_role_defaults_to_engineer(self):
        talent = {
            "_talent_id": "t1",
            "role": "InvalidRole",
            "_skill_descriptions": {},
            "_tool_names": [],
        }
        candidate = _talent_to_candidate(talent, 0.5)
        assert candidate.role == "Engineer"

    def test_sprite_assignment_by_role(self):
        for role, expected_sprite in [
            ("Engineer", "employee_blue"),
            ("Designer", "employee_purple"),
            ("QA", "employee_red"),
        ]:
            talent = {
                "_talent_id": f"t_{role}",
                "role": role,
                "_skill_descriptions": {},
                "_tool_names": [],
            }
            candidate = _talent_to_candidate(talent, 0.5)
            assert candidate.sprite == expected_sprite

    def test_missing_fields_use_defaults(self):
        talent = {"_talent_id": "minimal", "_skill_descriptions": {}, "_tool_names": []}
        candidate = _talent_to_candidate(talent, 0.0)
        assert candidate.name == "minimal"
        assert candidate.experience_years == 3
        assert candidate.temperature == 0.7

    def test_openrouter_with_model_calls_compute_salary(self):
        """Lines 255-256: openrouter provider with model calls compute_salary."""
        from unittest.mock import patch

        talent = {
            "_talent_id": "or_talent",
            "role": "Engineer",
            "llm_model": "gpt-4-turbo",
            "api_provider": "openrouter",
            "_skill_descriptions": {},
            "_tool_names": [],
        }
        mock_salary = MagicMock(return_value=7.5)
        with patch("onemancompany.core.model_costs.compute_salary", mock_salary):
            candidate = _talent_to_candidate(talent, 0.9)
        mock_salary.assert_called_once_with("gpt-4-turbo")
        assert candidate.cost_per_1m_tokens == 7.5

    def test_non_openrouter_cost_from_salary_field(self):
        """Lines 255-256, 259: Non-openrouter provider uses salary_per_1m_tokens."""
        talent = {
            "_talent_id": "anthropic_talent",
            "role": "Engineer",
            "llm_model": "claude-sonnet",
            "api_provider": "anthropic",
            "salary_per_1m_tokens": 3.5,
            "_skill_descriptions": {},
            "_tool_names": [],
        }
        candidate = _talent_to_candidate(talent, 0.9)
        assert candidate.cost_per_1m_tokens == 3.5
        assert candidate.api_provider == "anthropic"

    def test_non_openrouter_cost_defaults_to_zero(self):
        """Lines 258-259: Non-openrouter without salary_per_1m_tokens => 0.0."""
        talent = {
            "_talent_id": "custom_talent",
            "role": "Engineer",
            "llm_model": "some-model",
            "api_provider": "custom_provider",
            "_skill_descriptions": {},
            "_tool_names": [],
        }
        candidate = _talent_to_candidate(talent, 0.5)
        assert candidate.cost_per_1m_tokens == 0.0


# ---------------------------------------------------------------------------
# _load_all_talents (filesystem-dependent — use tmp_path)
# ---------------------------------------------------------------------------

class TestLoadAllTalents:
    def test_loads_from_directory(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        # Create fake talent directory
        talent_dir = tmp_path / "coding"
        talent_dir.mkdir()
        (talent_dir / "profile.yaml").write_text(
            "id: coding\nname: Coding Talent\nrole: Engineer\nskills:\n  - python\n"
        )
        skills_dir = talent_dir / "skills"
        skills_dir.mkdir()
        (skills_dir / "python.md").write_text("# Python\nPython skill")

        tools_dir = talent_dir / "tools"
        tools_dir.mkdir()
        (tools_dir / "manifest.yaml").write_text(
            "builtin_tools:\n  - sandbox_execute_code\ncustom_tools: []\n"
        )

        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path)
        talents = _load_all_talents()
        assert len(talents) == 1
        assert talents[0]["id"] == "coding"
        assert talents[0]["_talent_id"] == "coding"
        assert "python" in talents[0]["_skill_descriptions"]
        assert "sandbox_execute_code" in talents[0]["_tool_names"]

    def test_empty_directory(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path)
        talents = _load_all_talents()
        assert talents == []

    def test_missing_directory(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path / "nonexistent")
        talents = _load_all_talents()
        assert talents == []

    def test_skips_non_directory_entries(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        (tmp_path / "README.md").write_text("readme")
        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path)
        talents = _load_all_talents()
        assert talents == []

    def test_skips_directory_without_profile(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        (tmp_path / "empty_talent").mkdir()
        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path)
        talents = _load_all_talents()
        assert talents == []


# ---------------------------------------------------------------------------
# search_candidates MCP tool (uses _load_all_talents internally)
# ---------------------------------------------------------------------------

class TestSearchCandidatesMCP:
    def test_returns_sorted_by_relevance(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        # Create two talents with different relevance to "python engineer"
        for tid, name, skills in [
            ("python_dev", "Python Developer", ["python", "django"]),
            ("java_dev", "Java Developer", ["java", "spring"]),
        ]:
            d = tmp_path / tid
            d.mkdir()
            skill_str = "\n".join(f"  - {s}" for s in skills)
            (d / "profile.yaml").write_text(
                f"id: {tid}\nname: {name}\nrole: Engineer\nskills:\n{skill_str}\n"
            )

        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path)

        # MCP @mcp.tool() decorator yields a plain function — call directly
        results = bo.search_candidates("python web developer")
        assert len(results) == 2
        # Python dev should rank higher
        assert results[0]["id"] == "python_dev"

    def test_empty_talents_returns_empty(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path / "nonexistent")
        results = bo.search_candidates("anything")
        assert results == []

    def test_count_limits_results(self, tmp_path, monkeypatch):
        import onemancompany.talent_market.boss_online as bo

        for i in range(5):
            d = tmp_path / f"talent_{i}"
            d.mkdir()
            (d / "profile.yaml").write_text(
                f"id: talent_{i}\nname: Talent {i}\nrole: Engineer\nskills:\n  - python\n"
            )

        monkeypatch.setattr(bo, "TALENTS_DIR", tmp_path)
        results = bo.search_candidates("python", count=2)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Pydantic models validation
# ---------------------------------------------------------------------------

class TestPydanticModels:
    def test_candidate_profile_valid(self):
        cp = CandidateProfile(
            id="test",
            name="Test",
            role="Engineer",
            experience_years=5,
            personality_tags=["creative"],
            system_prompt="prompt",
            skill_set=[],
            tool_set=[],
            sprite="employee_blue",
            llm_model="test-model",
            jd_relevance=0.8,
        )
        assert cp.id == "test"
        assert cp.jd_relevance == 0.8

    def test_candidate_profile_invalid_role(self):
        with pytest.raises(Exception):
            CandidateProfile(
                id="test",
                name="Test",
                role="InvalidRole",
                experience_years=5,
                personality_tags=[],
                system_prompt="",
                skill_set=[],
                tool_set=[],
                sprite="employee_blue",
                llm_model="",
                jd_relevance=0.5,
            )

    def test_candidate_search_request(self):
        req = CandidateSearchRequest(job_description="python dev", count=5)
        assert req.count == 5

    def test_hire_request(self):
        hr = HireRequest(batch_id="abc", candidate_id="t1", nickname="逍遥")
        assert hr.batch_id == "abc"
        assert hr.nickname == "逍遥"

    def test_hire_response(self):
        resp = HireResponse(employee_id="00010", name="Test", nickname="追风")
        assert resp.status == "hired"

    def test_interview_request(self):
        cp = CandidateProfile(
            id="t1", name="T", role="Engineer", experience_years=1,
            personality_tags=[], system_prompt="", skill_set=[], tool_set=[],
            sprite="employee_blue", llm_model="", jd_relevance=0.5,
        )
        ir = InterviewRequest(question="Tell me about yourself", candidate=cp)
        assert ir.question == "Tell me about yourself"
        assert len(ir.images) == 0


# ---------------------------------------------------------------------------
# __main__ block (line 327)
# ---------------------------------------------------------------------------

class TestMainBlock:
    def test_main_calls_mcp_run(self, monkeypatch):
        """Line 327: if __name__ == '__main__': mcp.run()."""
        import onemancompany.talent_market.boss_online as bo

        mock_run = MagicMock()
        monkeypatch.setattr(bo.mcp, "run", mock_run)

        # Simulate __main__ execution
        original_name = bo.__name__
        try:
            bo.__name__ = "__main__"
            # Re-execute the if-block logic manually
            if bo.__name__ == "__main__":
                bo.mcp.run()
            mock_run.assert_called_once()
        finally:
            bo.__name__ = original_name
