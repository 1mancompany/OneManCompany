"""Talent Package Specification — 人才包规范定义

定义一个 talent 包需要包含哪些目录、文件以及各字段的含义。
平台按此规范加载 talent 并驱动招聘、onboarding 和运行时行为。

目录结构:
    talents/{talent_id}/
    ├── profile.yaml          # 必须 — 身份 + 招聘信息
    ├── manifest.json         # 可选 — 前端设置 UI + 能力声明
    ├── launch.sh             # 可选 — 自托管员工的启动脚本
    ├── run_worker.py         # 可选 — 远程员工的 worker 入口
    ├── skills/               # 可选 — 技能 Markdown 文件
    │   ├── *.md              # 每个文件描述一项技能，内容注入员工 prompt
    ├── tools/                # 可选 — 工具声明与自定义工具
    │   ├── manifest.yaml     # 工具清单（builtin_tools + custom_tools）
    │   └── *.py              # 自定义 LangChain @tool 实现
    └── functions/            # 可选 — talent 自带的函数实现
        ├── manifest.yaml     # 声明每个函数的元信息（name, description, scope）
        └── {name}.py         # LangChain @tool 实现（一个 .py 可导出多个 @tool）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HostingMode(str, Enum):
    """员工运行模式。"""
    COMPANY = "company"     # 公司托管：平台内部 LangChain agent loop
    SELF = "self"           # 自托管：员工自带运行环境（如 Claude Code CLI）
    REMOTE = "remote"       # 远程：通过 HTTP 轮询接收任务


class AuthMethod(str, Enum):
    """认证方式。"""
    API_KEY = "api_key"     # 使用 API key 调用 LLM
    OAUTH = "oauth"         # OAuth PKCE 登录（如 Anthropic OAuth）
    CLI = "cli"             # 使用本机已登录的 CLI 凭证
    NONE = "none"           # 无需认证（免费模型或自带凭证）


class SettingFieldType(str, Enum):
    """manifest.json settings 中支持的字段类型。

    前端根据 type 动态渲染对应的 UI 控件。
    """
    TEXT = "text"                 # 单行文本输入
    SECRET = "secret"            # 密码输入（掩码显示）
    NUMBER = "number"            # 数字输入（支持 min/max/step）
    SELECT = "select"            # 单选下拉
    MULTI_SELECT = "multi_select"  # 多选下拉
    TOGGLE = "toggle"            # 开关（布尔值）
    TEXTAREA = "textarea"        # 多行文本
    OAUTH_BUTTON = "oauth_button"  # OAuth 登录按钮（触发 PKCE 流程）
    COLOR = "color"              # 颜色选择器
    FILE = "file"                # 文件上传
    READONLY = "readonly"        # 只读显示（value_from 指定数据源）


# ---------------------------------------------------------------------------
# manifest.json 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SettingField:
    """manifest.json 中一个设置字段的定义。

    Attributes:
        key:          字段标识符，对应 profile.yaml 中的键名
        type:         字段类型，决定前端渲染方式
        label:        前端显示的标签文字
        default:      默认值（可选）
        required:     是否必填
        min:          数字类型的最小值
        max:          数字类型的最大值
        step:         数字类型的步长
        options:      select/multi_select 的选项列表
        options_from: 动态选项数据源（如 "api:models"）
        provider:     OAuth provider 标识（如 "anthropic"）
        value_from:   readonly 字段的数据源（如 "api:sessions"）
    """
    key: str
    type: SettingFieldType
    label: str
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] = field(default_factory=list)
    options_from: str = ""
    provider: str = ""
    value_from: str = ""


@dataclass
class SettingSection:
    """manifest.json 中一个设置分组。

    Attributes:
        id:     分组标识符（如 "connection", "session"）
        title:  前端显示的分组标题
        fields: 该分组下的字段列表
    """
    id: str
    title: str
    fields: list[SettingField] = field(default_factory=list)


@dataclass
class ManifestPrompts:
    """manifest.json 中的 prompt 文件声明。

    Attributes:
        system: 系统 prompt 文件路径（相对 talent 目录），覆盖默认系统 prompt
        role:   角色 prompt 文件路径，覆盖默认角色描述
        skills: 技能文件 glob 模式列表（如 ["skills/*.md"]）
    """
    system: str = ""
    role: str = ""
    skills: list[str] = field(default_factory=lambda: ["skills/*.md"])


@dataclass
class ManifestTools:
    """manifest.json 中的工具声明。

    Attributes:
        builtin: 平台内置工具名列表（在 SANDBOX_TOOLS/COMMON_TOOLS 中注册）
        custom:  自定义工具文件路径列表（相对 talent 目录的 .py 文件）
    """
    builtin: list[str] = field(default_factory=list)
    custom: list[str] = field(default_factory=list)


@dataclass
class TalentManifest:
    """manifest.json 完整结构 — 驱动前端设置 UI 和能力声明。

    manifest.json 是可选文件。如果 talent 没有提供 manifest.json，
    平台将使用 profile.yaml 中的信息降级渲染默认设置 UI。

    Attributes:
        id:                     talent 唯一标识符
        name:                   talent 显示名称
        version:                版本号（语义化版本）
        role:                   角色类型（Engineer, Designer, QA 等）
        hosting:                运行模式
        settings:               设置分组列表，驱动前端动态 UI
        prompts:                prompt 文件声明
        tools:                  工具声明
        platform_capabilities:  平台能力需求列表（如 file_upload, websocket）
    """
    id: str
    name: str
    version: str = "1.0.0"
    role: str = ""
    hosting: HostingMode = HostingMode.COMPANY
    settings: list[SettingSection] = field(default_factory=list)
    prompts: ManifestPrompts = field(default_factory=ManifestPrompts)
    tools: ManifestTools = field(default_factory=ManifestTools)
    platform_capabilities: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# profile.yaml 数据结构
# ---------------------------------------------------------------------------

@dataclass
class TalentProfile:
    """profile.yaml 完整结构 — talent 的身份和招聘信息。

    profile.yaml 是必须文件，平台通过它识别 talent 并展示给 HR。

    Attributes:
        id:                     talent 唯一标识符（与目录名一致）
        name:                   显示名称（如 "Coding Talent"）
        description:            talent 描述文字，HR 招聘时展示
        role:                   角色类型，决定入职后的部门分配
                                (Engineer → 技术研发部, Designer → 设计部, etc.)
        remote:                 是否远程工作（True = 不分配工位）
        hosting:                运行模式（默认 "company"）
        auth_method:            认证方式（默认 "api_key"）
        api_provider:           LLM API 提供商（"openrouter", "anthropic" 等）
        llm_model:              默认 LLM 模型标识符
        temperature:            默认推理温度
        image_model:            图像生成模型标识符（可选，Designer 等角色使用）
        hiring_fee:             招聘费用（虚拟货币，HR 评估用）
        salary_per_1m_tokens:   每百万 token 薪酬（0 表示自动按模型计算）
        skills:                 技能标识符列表，对应 skills/ 下的 .md 文件
        tools:                  工具名列表（声明此 talent 使用的工具）
        personality_tags:       性格标签列表（HR 匹配用）
        system_prompt_template: 系统 prompt 模板（注入员工 agent 的基础指令）
    """
    id: str
    name: str
    description: str = ""
    role: str = "Engineer"
    remote: bool = False
    hosting: str = "company"
    auth_method: str = "api_key"
    api_provider: str = "openrouter"
    llm_model: str = ""
    temperature: float = 0.7
    image_model: str = ""
    hiring_fee: float = 0.0
    salary_per_1m_tokens: float = 0.0
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    personality_tags: list[str] = field(default_factory=list)
    system_prompt_template: str = ""


# ---------------------------------------------------------------------------
# tools/manifest.yaml 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ToolsManifest:
    """tools/manifest.yaml 完整结构 — 工具清单声明。

    声明此 talent 使用的内置工具和自定义工具。
    内置工具名引用 COMMON_TOOLS 或 SANDBOX_TOOLS 中注册的工具。
    自定义工具指向同目录下的 .py 文件，每个文件导出一个 LangChain @tool。

    Attributes:
        builtin_tools:  内置工具名列表
                        常见值: sandbox_execute_code, sandbox_run_command,
                        sandbox_write_file, sandbox_read_file, web_search,
                        generate_image
        custom_tools:   自定义工具模块名列表（不含 .py 后缀）
                        每个名字对应 tools/{name}.py 中导出的 @tool 函数
    """
    builtin_tools: list[str] = field(default_factory=list)
    custom_tools: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# functions/manifest.yaml 数据结构
# ---------------------------------------------------------------------------

@dataclass
class AgentPromptSection:
    """agent/manifest.yaml 中的一个 prompt section 覆盖。"""
    name: str
    file: str = ""
    priority: int = 50


@dataclass
class AgentManifest:
    """agent/manifest.yaml — talent agent loop 定制声明。"""
    runner_module: str = ""
    runner_class: str = ""
    hooks_module: str = ""
    pre_task_hook: str = ""
    post_task_hook: str = ""
    prompt_sections: list[AgentPromptSection] = field(default_factory=list)


@dataclass
class VesselManifest:
    """vessel/vessel.yaml — talent 自带的躯壳 DNA 声明。

    当 talent 包含 vessel/ 目录时，使用此结构替代 AgentManifest。
    字段对应 VesselConfig 的各个子配置。
    """
    runner: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    prompt_sections: list[AgentPromptSection] = field(default_factory=list)


@dataclass
class FunctionDeclaration:
    """functions/manifest.yaml 中的单个函数声明。"""
    name: str
    description: str = ""
    scope: str = "personal"  # "company" | "personal"


@dataclass
class FunctionsManifest:
    """functions/manifest.yaml — talent 自带函数声明。"""
    functions: list[FunctionDeclaration] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 完整 talent 包
# ---------------------------------------------------------------------------

@dataclass
class TalentPackage:
    """一个完整的 talent 包，聚合所有组成部分。

    文件系统布局:
        talents/{id}/
        ├── profile.yaml          # → self.profile (必须)
        ├── manifest.json         # → self.manifest (可选)
        ├── launch.sh             # → self.has_launch_script (可选, self-hosted)
        ├── run_worker.py         # → (可选, remote)
        ├── skills/
        │   └── *.md              # → self.skill_files
        ├── tools/
        │   ├── manifest.yaml     # → self.tools_manifest (可选)
        │   └── *.py              # → 自定义工具实现
        └── functions/            # → self.functions_manifest (可选)
            ├── manifest.yaml     # 声明每个函数的元信息
            └── {name}.py         # LangChain @tool 实现

    入职流程 (onboarding.py):
        1. HR 从 talent market 浏览可用 talent
        2. CEO 确认招聘 → execute_hire()
        3. 分配工号、部门、工位
        4. 从 talent 目录复制 skills/ 和 tools/ 到员工目录
        5. 自托管员工额外复制 launch.sh 和 connection.json
        6. 生成花名、工作原则
        7. 注册到 EmployeeManager

    运行时行为按 hosting 不同:
        - company: 平台内 LangChain agent，由 LangChainLauncher 执行
        - self:    独立进程（如 Claude Code CLI），由 ScriptLauncher 启动
        - remote:  外部 worker 通过 HTTP 轮询任务队列
    """
    profile: TalentProfile
    manifest: TalentManifest | None = None
    tools_manifest: ToolsManifest | None = None
    functions_manifest: FunctionsManifest | None = None
    vessel_manifest: VesselManifest | None = None
    agent_manifest: AgentManifest | None = None  # 保留向后兼容
    skill_files: list[str] = field(default_factory=list)
    has_launch_script: bool = False
