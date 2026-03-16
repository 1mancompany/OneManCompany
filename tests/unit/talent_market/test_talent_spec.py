"""Unit tests for talent_market/talent_spec.py — dataclass definitions."""

from __future__ import annotations

import pytest

from onemancompany.talent_market.talent_spec import (
    AgentManifest,
    AgentPromptSection,
    AuthMethod,
    FunctionDeclaration,
    FunctionsManifest,
    HostingMode,
    ManifestPrompts,
    ManifestTools,
    SettingField,
    SettingFieldType,
    SettingSection,
    TalentManifest,
    TalentPackage,
    TalentProfile,
    ToolsManifest,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestHostingMode:
    def test_values(self):
        assert HostingMode.COMPANY == "company"
        assert HostingMode.SELF == "self"
        assert HostingMode.REMOTE == "remote"

    def test_is_str(self):
        assert isinstance(HostingMode.COMPANY, str)

    def test_from_string(self):
        assert HostingMode("company") is HostingMode.COMPANY
        assert HostingMode("self") is HostingMode.SELF
        assert HostingMode("remote") is HostingMode.REMOTE

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            HostingMode("invalid")


class TestAuthMethod:
    def test_values(self):
        assert AuthMethod.API_KEY == "api_key"
        assert AuthMethod.OAUTH == "oauth"
        assert AuthMethod.CLI == "cli"
        assert AuthMethod.NONE == "none"

    def test_is_str(self):
        assert isinstance(AuthMethod.API_KEY, str)


class TestSettingFieldType:
    def test_all_types_exist(self):
        expected = [
            "text", "secret", "number", "select", "multi_select",
            "toggle", "textarea", "oauth_button", "color", "file", "readonly",
        ]
        for val in expected:
            assert SettingFieldType(val) is not None


# ---------------------------------------------------------------------------
# SettingField
# ---------------------------------------------------------------------------


class TestSettingField:
    def test_minimal(self):
        sf = SettingField(key="api_key", type=SettingFieldType.SECRET, label="API Key")
        assert sf.key == "api_key"
        assert sf.type == SettingFieldType.SECRET
        assert sf.label == "API Key"
        assert sf.default is None
        assert sf.required is False

    def test_full(self):
        sf = SettingField(
            key="temp",
            type=SettingFieldType.NUMBER,
            label="Temperature",
            default=0.7,
            required=True,
            min=0.0,
            max=2.0,
            step=0.1,
        )
        assert sf.min == 0.0
        assert sf.max == 2.0
        assert sf.step == 0.1
        assert sf.required is True

    def test_select_with_options(self):
        sf = SettingField(
            key="model",
            type=SettingFieldType.SELECT,
            label="Model",
            options=["gpt-4", "claude"],
        )
        assert sf.options == ["gpt-4", "claude"]

    def test_default_lists(self):
        sf = SettingField(key="k", type=SettingFieldType.TEXT, label="L")
        assert sf.options == []
        assert sf.options_from == ""
        assert sf.provider == ""
        assert sf.value_from == ""


# ---------------------------------------------------------------------------
# SettingSection
# ---------------------------------------------------------------------------


class TestSettingSection:
    def test_minimal(self):
        ss = SettingSection(id="connection", title="Connection")
        assert ss.id == "connection"
        assert ss.title == "Connection"
        assert ss.fields == []

    def test_with_fields(self):
        f1 = SettingField(key="k1", type=SettingFieldType.TEXT, label="L1")
        f2 = SettingField(key="k2", type=SettingFieldType.SECRET, label="L2")
        ss = SettingSection(id="conn", title="Conn", fields=[f1, f2])
        assert len(ss.fields) == 2


# ---------------------------------------------------------------------------
# ManifestPrompts
# ---------------------------------------------------------------------------


class TestManifestPrompts:
    def test_defaults(self):
        mp = ManifestPrompts()
        assert mp.system == ""
        assert mp.role == ""
        assert mp.skills == ["skills/*.md"]

    def test_custom(self):
        mp = ManifestPrompts(system="sys.md", role="role.md", skills=["custom/*.md"])
        assert mp.system == "sys.md"
        assert mp.skills == ["custom/*.md"]


# ---------------------------------------------------------------------------
# ManifestTools
# ---------------------------------------------------------------------------


class TestManifestTools:
    def test_defaults(self):
        mt = ManifestTools()
        assert mt.builtin == []
        assert mt.custom == []

    def test_with_values(self):
        mt = ManifestTools(builtin=["web_search"], custom=["my_tool.py"])
        assert "web_search" in mt.builtin
        assert "my_tool.py" in mt.custom


# ---------------------------------------------------------------------------
# TalentManifest
# ---------------------------------------------------------------------------


class TestTalentManifest:
    def test_minimal(self):
        tm = TalentManifest(id="test", name="Test")
        assert tm.id == "test"
        assert tm.name == "Test"
        assert tm.version == "1.0.0"
        assert tm.hosting == HostingMode.COMPANY
        assert tm.settings == []
        assert isinstance(tm.prompts, ManifestPrompts)
        assert isinstance(tm.tools, ManifestTools)
        assert tm.platform_capabilities == []

    def test_full(self):
        tm = TalentManifest(
            id="coder",
            name="Coding Talent",
            version="2.0.0",
            role="Engineer",
            hosting=HostingMode.SELF,
            platform_capabilities=["file_upload", "websocket"],
        )
        assert tm.version == "2.0.0"
        assert tm.hosting == HostingMode.SELF
        assert len(tm.platform_capabilities) == 2


# ---------------------------------------------------------------------------
# TalentProfile
# ---------------------------------------------------------------------------


class TestTalentProfile:
    def test_minimal(self):
        tp = TalentProfile(id="dev1", name="Dev One")
        assert tp.id == "dev1"
        assert tp.name == "Dev One"
        assert tp.role == "Engineer"
        assert tp.remote is False
        assert tp.hosting == "company"
        assert tp.auth_method == "api_key"
        assert tp.api_provider == "openrouter"
        assert tp.temperature == 0.7
        assert tp.hiring_fee == 0.0
        assert tp.skills == []
        assert tp.tools == []
        assert tp.personality_tags == []

    def test_full(self):
        tp = TalentProfile(
            id="designer1",
            name="Design Pro",
            description="A talented designer",
            role="Designer",
            remote=True,
            hosting="remote",
            auth_method="oauth",
            api_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            temperature=0.5,
            image_model="dall-e-3",
            hiring_fee=100.0,
            salary_per_1m_tokens=5.0,
            skills=["ui_design", "figma"],
            tools=["generate_image"],
            personality_tags=["creative", "detail-oriented"],
            system_prompt_template="You are a designer.",
        )
        assert tp.role == "Designer"
        assert tp.remote is True
        assert tp.llm_model == "claude-sonnet-4-6"
        assert len(tp.skills) == 2
        assert tp.salary_per_1m_tokens == 5.0


# ---------------------------------------------------------------------------
# ToolsManifest
# ---------------------------------------------------------------------------


class TestToolsManifest:
    def test_defaults(self):
        tm = ToolsManifest()
        assert tm.builtin_tools == []
        assert tm.custom_tools == []

    def test_with_tools(self):
        tm = ToolsManifest(
            builtin_tools=["sandbox_execute_code", "web_search"],
            custom_tools=["my_analyzer"],
        )
        assert len(tm.builtin_tools) == 2
        assert "my_analyzer" in tm.custom_tools


# ---------------------------------------------------------------------------
# AgentPromptSection / AgentManifest
# ---------------------------------------------------------------------------


class TestAgentPromptSection:
    def test_minimal(self):
        aps = AgentPromptSection(name="system")
        assert aps.name == "system"
        assert aps.file == ""
        assert aps.priority == 50

    def test_full(self):
        aps = AgentPromptSection(name="custom", file="custom.md", priority=10)
        assert aps.file == "custom.md"
        assert aps.priority == 10


class TestAgentManifest:
    def test_defaults(self):
        am = AgentManifest()
        assert am.runner_module == ""
        assert am.runner_class == ""
        assert am.hooks_module == ""
        assert am.pre_task_hook == ""
        assert am.post_task_hook == ""
        assert am.prompt_sections == []

    def test_with_values(self):
        am = AgentManifest(
            runner_module="my_runner",
            runner_class="MyRunner",
            prompt_sections=[
                AgentPromptSection(name="system", file="sys.md", priority=10),
            ],
        )
        assert am.runner_module == "my_runner"
        assert len(am.prompt_sections) == 1
        assert am.prompt_sections[0].name == "system"


# ---------------------------------------------------------------------------
# FunctionDeclaration / FunctionsManifest
# ---------------------------------------------------------------------------


class TestFunctionDeclaration:
    def test_defaults(self):
        fd = FunctionDeclaration(name="my_func")
        assert fd.name == "my_func"
        assert fd.description == ""
        assert fd.scope == "personal"

    def test_company_scope(self):
        fd = FunctionDeclaration(name="deploy", description="Deploy app", scope="company")
        assert fd.scope == "company"


class TestFunctionsManifest:
    def test_defaults(self):
        fm = FunctionsManifest()
        assert fm.functions == []

    def test_with_functions(self):
        fm = FunctionsManifest(functions=[
            FunctionDeclaration(name="f1"),
            FunctionDeclaration(name="f2", scope="company"),
        ])
        assert len(fm.functions) == 2
        assert fm.functions[1].scope == "company"


# ---------------------------------------------------------------------------
# TalentPackage
# ---------------------------------------------------------------------------


class TestTalentPackage:
    def test_minimal(self):
        profile = TalentProfile(id="test", name="Test Talent")
        tp = TalentPackage(profile=profile)
        assert tp.profile.id == "test"
        assert tp.manifest is None
        assert tp.tools_manifest is None
        assert tp.functions_manifest is None
        assert tp.agent_manifest is None
        assert tp.skill_files == []
        assert tp.has_launch_script is False

    def test_full(self):
        profile = TalentProfile(id="coder", name="Coder", role="Engineer")
        manifest = TalentManifest(id="coder", name="Coder")
        tools = ToolsManifest(builtin_tools=["sandbox_execute_code"])
        functions = FunctionsManifest(functions=[FunctionDeclaration(name="build")])
        agent = AgentManifest(runner_module="custom_runner")

        tp = TalentPackage(
            profile=profile,
            manifest=manifest,
            tools_manifest=tools,
            functions_manifest=functions,
            agent_manifest=agent,
            skill_files=["skills/python.md", "skills/java.md"],
            has_launch_script=True,
        )
        assert tp.manifest is not None
        assert tp.manifest.id == "coder"
        assert len(tp.skill_files) == 2
        assert tp.has_launch_script is True
        assert tp.tools_manifest.builtin_tools == ["sandbox_execute_code"]
        assert tp.functions_manifest.functions[0].name == "build"
        assert tp.agent_manifest.runner_module == "custom_runner"

    def test_default_factory_isolation(self):
        """Ensure default list fields don't share state between instances."""
        tp1 = TalentPackage(profile=TalentProfile(id="a", name="A"))
        tp2 = TalentPackage(profile=TalentProfile(id="b", name="B"))
        tp1.skill_files.append("x.md")
        assert tp2.skill_files == []
