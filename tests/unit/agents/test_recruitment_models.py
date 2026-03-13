"""Tests for Pydantic models migrated to recruitment.py."""

from __future__ import annotations

import pytest


class TestCandidateSkill:
    def test_creation_with_defaults(self):
        from onemancompany.agents.recruitment import CandidateSkill

        skill = CandidateSkill(name="python", description="Python programming")
        assert skill.name == "python"
        assert skill.description == "Python programming"
        assert skill.code == ""

    def test_creation_with_code(self):
        from onemancompany.agents.recruitment import CandidateSkill

        skill = CandidateSkill(name="python", description="Python", code="print('hi')")
        assert skill.code == "print('hi')"


class TestCandidateProfile:
    def test_creation_with_nested_skills(self):
        from onemancompany.agents.recruitment import CandidateProfile, CandidateSkill, CandidateTool

        profile = CandidateProfile(
            id="t001",
            name="Alice",
            role="Engineer",
            experience_years=5,
            personality_tags=["diligent"],
            system_prompt="You are Alice.",
            skill_set=[CandidateSkill(name="python", description="Python dev")],
            tool_set=[CandidateTool(name="debugger", description="Debug tool")],
            sprite="employee_blue",
            llm_model="gpt-4",
            jd_relevance=0.9,
        )
        assert profile.id == "t001"
        assert profile.name == "Alice"
        assert profile.role == "Engineer"
        assert len(profile.skill_set) == 1
        assert profile.skill_set[0].name == "python"
        assert profile.remote is False
        assert profile.talent_id == ""
        assert profile.cost_per_1m_tokens == 0.0
        assert profile.api_provider == "openrouter"
        assert profile.temperature == 0.7
        assert profile.hosting == "company"
        assert profile.auth_method == "api_key"


class TestHireRequest:
    def test_creation_with_defaults(self):
        from onemancompany.agents.recruitment import HireRequest

        req = HireRequest(batch_id="abc123", candidate_id="t001")
        assert req.batch_id == "abc123"
        assert req.candidate_id == "t001"
        assert req.nickname == ""

    def test_creation_with_nickname(self):
        from onemancompany.agents.recruitment import HireRequest

        req = HireRequest(batch_id="abc", candidate_id="t001", nickname="飞鸟")
        assert req.nickname == "飞鸟"


class TestInterviewRequest:
    def test_creation_with_candidate(self):
        from onemancompany.agents.recruitment import (
            CandidateProfile,
            CandidateSkill,
            CandidateTool,
            InterviewRequest,
        )

        candidate = CandidateProfile(
            id="t002",
            name="Bob",
            role="Designer",
            experience_years=3,
            personality_tags=["creative"],
            system_prompt="You are Bob.",
            skill_set=[CandidateSkill(name="figma", description="Figma design")],
            tool_set=[CandidateTool(name="sketch", description="Sketch tool")],
            sprite="employee_purple",
            llm_model="claude-3",
            jd_relevance=0.8,
        )
        req = InterviewRequest(question="Tell me about yourself", candidate=candidate)
        assert req.question == "Tell me about yourself"
        assert req.candidate.id == "t002"
        assert req.images == []


class TestInterviewResponse:
    def test_creation(self):
        from onemancompany.agents.recruitment import InterviewResponse

        resp = InterviewResponse(
            candidate_id="t002",
            question="Tell me about yourself",
            answer="I am Bob, a designer.",
        )
        assert resp.candidate_id == "t002"
        assert resp.question == "Tell me about yourself"
        assert resp.answer == "I am Bob, a designer."
