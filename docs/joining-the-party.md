# Welcome to the Party

> Bring your own Claude configuration (prompts, skills, tools) and OpenClaw setup into OneManCompany — or sell them on the Talent Market.

**[中文版 / Chinese Version](#中文版)**

---

## Overview

Employees (AI Agents) in OneManCompany are defined by **Talent Packages**. You can wrap your existing Claude configuration (system prompt, skills, tools) or OpenClaw setup into a talent package, then let HR hire it directly — or publish it to the Talent Market for others to use.

This guide covers three paths:

| Path | When to use |
|------|-------------|
| [Bring Your Claude Config](#bring-your-claude-config) | You have Claude prompts / skills / MCP tools and want AI employees to use them |
| [Bring Your OpenClaw Config](#bring-your-openclaw-config) | You have an OpenClaw gateway (Telegram / Discord / Slack channels) and want employees to manage multi-channel comms |
| [Sell on the Talent Market](#sell-on-the-talent-market) | You've built a great talent and want to share or sell it to other companies |

---

## Bring Your Claude Config

### What You Already Have

Your Claude configuration is typically spread across:

```text
~/.claude/
├── settings.json          # Global permissions, MCP server list
├── keybindings.json       # Key bindings
└── projects/<project>/
    ├── CLAUDE.md           # Project-level system prompt
    └── memory/             # Auto memory

<your-project>/
├── CLAUDE.md               # Repo-level system prompt
└── .claude/
    └── skills/             # Skill directories (Markdown files)
        ├── my-skill-a/
        │   └── SKILL.md
        └── my-skill-b/
            └── SKILL.md
```

### Mapping Table

| Your Claude Config | Talent Package Location | Notes |
|---|---|---|
| `CLAUDE.md` (system prompt) | `profile.yaml` → `system_prompt_template` | Core instructions injected into the employee agent |
| `.claude/skills/*.md` | `skills/` directory | One subdirectory per skill with a `SKILL.md`, injected into employee prompt |
| MCP tools | `tools/manifest.yaml` → `mcp_servers` | Declare MCP server command and args |
| LangChain @tool | `tools/*.py` + `tools/manifest.yaml` → `custom_tools` | Custom tool implementations |
| Built-in tools (bash, read_file, etc.) | `tools/manifest.yaml` → `builtin_tools` | Reference platform-registered tool names |

### Steps

#### 1. Create Talent Directory

```bash
mkdir -p src/onemancompany/talent_market/talents/my-claude-agent/{skills,tools}
```

#### 2. Write profile.yaml

```yaml
id: my-claude-agent
name: My Claude Agent
description: Based on my own Claude config, good at XXX.
role: Engineer            # Engineer / Designer / QA / Assistant / Manager
remote: false
hosting: company          # company = platform-hosted | self = self-hosted (Claude CLI)
auth_method: api_key
api_provider: openrouter  # openrouter / anthropic / openai
llm_model: ''             # Empty = use company default model
temperature: 0.7
hiring_fee: 0.5
salary_per_1m_tokens: 0.0
skills:
  - my-skill-a
  - my-skill-b
personality_tags:
  - helpful
  - thorough
system_prompt_template: >
  You are a professional XXX assistant.
  (Put the core instructions from your CLAUDE.md here)
```

#### 3. Migrate Skills

Copy each skill directory from your `.claude/skills/` into the talent's `skills/`:

```bash
# Copy from your Claude skills directory
cp -r /path/to/.claude/skills/my-skill-a talents/my-claude-agent/skills/

# Each skill directory should contain a SKILL.md (or *.md)
```

SKILL.md format:

```markdown
---
name: My Skill A
description: What this skill does
autoload: true
---

# My Skill A

Specific skill instructions and knowledge...
```

> **Tip**: You can also symlink to your existing skill directories:

> ```bash
> ln -s /path/to/.claude/skills/my-skill-a talents/my-claude-agent/skills/my-skill-a
> ```

#### 4. Configure Tools

Create `tools/manifest.yaml`:

```yaml
# Built-in tools — reference platform-registered tool names
builtin_tools:
  - read_file
  - write_file
  - list_dir
  - bash

# Custom tools — correspond to .py files under tools/ (without extension)
custom_tools:
  - my_custom_tool

# MCP Servers — external tool services
mcp_servers:
  fastskills:
    command: uvx
    args: ["fastskills", "--skills-dir", "{skills_dir}"]
    env:
      SKILLSMP_API_KEY: "{SKILLSMP_API_KEY}"
```

Custom tool example (`tools/my_custom_tool.py`):

```python
from langchain_core.tools import tool

@tool
def my_custom_tool(query: str) -> str:
    """Tool description — visible to both HR and the employee."""
    # Your implementation
    return f"Result for: {query}"
```

#### 5. (Optional) Create manifest.json

If your talent needs a frontend settings UI (e.g. for users to enter API keys), create `manifest.json`:

```json
{
  "id": "my-claude-agent",
  "name": "My Claude Agent",
  "version": "1.0.0",
  "role": "Engineer",
  "hosting": "company",
  "settings": {
    "sections": [
      {
        "id": "connection",
        "title": "Connection",
        "fields": [
          {"key": "api_key", "type": "secret", "label": "API Key", "required": true}
        ]
      }
    ]
  },
  "prompts": {
    "skills": ["skills/*/SKILL.md"]
  },
  "tools": {
    "builtin": ["read_file", "write_file", "bash"],
    "custom": []
  }
}
```

#### 6. Hire

Once the platform is running, HR will automatically discover your talent. CEO confirms the hire and you're good to go.

---

## Bring Your OpenClaw Config

### What You Have

OpenClaw is a multi-channel AI gateway. You probably have:

```text
~/.openclaw/
└── openclaw.json          # OpenClaw global config (API key, model, etc.)

# Plus channel credentials:
#   - Telegram Bot Token
#   - Discord Bot Token
#   - Slack App Token + Bot Token
#   - ElevenLabs API Key (voice)
#   - Brave Search API Key
```

### OpenClaw Steps

#### 1. Use the Built-in OpenClaw Talent

The project ships with an OpenClaw talent template at:

```text
src/onemancompany/talent_market/talents/openclaw/
├── profile.yaml        # Identity info
├── manifest.json       # Settings UI (channel token input fields)
├── launch.sh           # Startup script (auto-install + start gateway)
└── skills/
    └── multi-channel-comms/
        └── SKILL.md    # Multi-channel comms skill description
```

Just hire the talent and configure channel credentials.

#### 2. Configure Channel Credentials

**Option A — Via Frontend Settings UI**

After hiring the OpenClaw employee, fill in the settings panel:

| Field | Description |
|---|---|
| Telegram Bot Token | Get from @BotFather |
| Discord Bot Token | Discord Developer Portal → Bot → Token |
| Slack App Token | `xapp-...` format, App-Level Token |
| Slack Bot Token | `xoxb-...` format, OAuth Bot Token |
| ElevenLabs API Key | Text-to-speech (optional) |
| Brave Search API Key | Search capability (optional) |

**Option B — Via .env File**

Add to the project root `.env`:

```bash
# OpenClaw
OPENROUTER_API_KEY=sk-or-...

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC...

# Discord
DISCORD_BOT_TOKEN=MTIz...

# Slack
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...

# Optional
ELEVENLABS_API_KEY=...
BRAVE_API_KEY=...
```

#### 3. Customize the OpenClaw Talent

To create a customized version (e.g. Telegram-only with a specific prompt), copy it:

```bash
cp -r talents/openclaw talents/my-openclaw
```

Then edit `my-openclaw/profile.yaml`:

```yaml
id: my-openclaw
name: My OpenClaw Bot
description: Telegram-focused customer service OpenClaw gateway.
system_prompt_template: >
  You are a Telegram customer service bot.
  Only answer product-related questions. Be friendly and concise.
```

Edit `manifest.json` to keep only the channel fields you need.

#### 4. How launch.sh Works

`launch.sh` automatically:

1. Loads environment variables from `.env`
2. Checks and installs `openclaw` (via npm)
3. Auto-configures with `OPENROUTER_API_KEY` on first run
4. Starts the gateway (port 18789)
5. Executes tasks via `openclaw agent`

No manual gateway lifecycle management needed.

---

## Sell on the Talent Market

Once you've built and tested a talent, you can publish it to the Talent Market for other OneManCompany instances to discover, hire, and use.

### What Gets Published

Your entire talent directory becomes the distributable unit:

```text
talents/my-awesome-agent/
├── profile.yaml            # Identity, pricing, description
├── manifest.json           # Settings UI, capability declaration
├── skills/                 # Bundled skills
├── tools/                  # Bundled tools + MCP configs
├── functions/              # Bundled functions
└── launch.sh               # Startup script (if self-hosted)
```

### Pricing Your Talent

Set pricing in `profile.yaml`:

```yaml
hiring_fee: 2.0               # One-time fee when another company hires this talent
salary_per_1m_tokens: 0.5     # Ongoing cost per 1M tokens consumed (0 = auto by model)
```

- `hiring_fee` — charged once when the talent is hired. Think of it as a setup / licensing fee.
- `salary_per_1m_tokens` — ongoing operational cost. Set to `0` to let the platform auto-calculate based on the underlying LLM model pricing.

### Writing a Good Listing

Your `profile.yaml` fields double as the marketplace listing:

| Field | Marketplace role |
|---|---|
| `name` | Listing title |
| `description` | Shown to HR when browsing — make it clear and compelling |
| `role` | Determines which department category the talent appears under |
| `personality_tags` | Searchable tags HR can filter by |
| `skills` | Skill badges shown on the listing card |
| `hiring_fee` | Displayed price tag |

Tips for a good listing:

- **Description**: Lead with what the talent *does*, not what it *is*. "Automates Telegram customer support with 20+ canned responses and escalation logic" beats "A Telegram bot agent."
- **Skills**: List concrete, specific skills rather than vague ones. `telegram-cs`, `escalation-routing` > `helpful`, `versatile`.
- **Personality tags**: Think about what HR would search for. `always-on`, `multi-channel`, `code-review`.

### Publishing

Drop your talent directory into the shared talent registry:

```bash
# Copy into the talent market
cp -r my-awesome-agent src/onemancompany/talent_market/talents/

# The platform auto-discovers talents on startup
```

For distribution beyond a single instance, package and share the talent directory (e.g. as a git repo, zip, or npm package). Any OneManCompany instance can import it by placing the directory under `talent_market/talents/`.

### Versioning

Use `manifest.json` → `version` to track releases:

```json
{
  "version": "1.2.0"
}
```

Follow semantic versioning: bump patch for fixes, minor for new skills/tools, major for breaking changes (e.g. renamed settings keys that require reconfiguration).

---

## Talent Package Structure Reference

```text
talents/{talent-id}/
├── profile.yaml            # [Required] Identity + hiring info
├── manifest.json           # [Optional] Frontend settings UI + capability declaration
├── launch.sh               # [Optional] Self-hosted startup script
├── skills/                 # [Optional] Skills directory
│   └── {skill-name}/
│       └── SKILL.md        #   Skill description (injected into employee prompt)
├── tools/                  # [Optional] Tools directory
│   ├── manifest.yaml       #   Tool manifest (builtin + custom + mcp)
│   └── *.py                #   Custom LangChain @tool implementations
└── functions/              # [Optional] Functions directory
    ├── manifest.yaml       #   Function metadata declarations
    └── {name}.py           #   Function implementations
```

Onboarding flow: HR browses talent market → CEO confirms hire → Platform assigns employee number / department / desk → Copies skills & tools to employee directory → Assigns nickname → Registers with EmployeeManager → Ready to work.

### Customizing Nicknames (花名)

Each employee receives a wuxia-themed 2-character Chinese nickname (花名) on hire. Nicknames are drawn randomly from a pool file:

```
company/human_resource/nicknames.txt
```

The file ships with 1000 pre-built names sourced from classic wuxia novels (金庸、古龙、梁羽生) and jianghu-inspired combinations. You can replace this file with your own list — one nickname per line, any character length. The system picks a random name that doesn't collide with existing employees.

To use a custom pool, place your `nicknames.txt` in `<data_dir>/company/human_resource/nicknames.txt` (the runtime data directory takes priority over the built-in file).

---

## FAQ

**Q: My Claude skills use symlinks. Will they work after onboarding?**
A: Yes. The platform follows symlinks when copying the skills directory. You can also use symlinks directly in the talent package to point to shared skills.

**Q: Self-hosted vs. company-hosted — which should I pick?**
A: `company` = runs inside the platform's LangChain agent loop, suitable for most cases. `self` = the employee brings its own runtime (e.g. Claude Code CLI), for scenarios requiring full local capabilities. OpenClaw uses `company` + `launch.sh` since it needs to manage a gateway process.

**Q: Can I use Claude skills and OpenClaw together?**
A: Yes. Create a talent with Claude skills in `skills/` and an OpenClaw gateway in `launch.sh`. They don't conflict.

**Q: How do I add new skills to an existing employee?**
A: Drop a new SKILL.md into `employees/{id}/skills/`. It's automatically loaded on the next task execution.

**Q: Can I sell a talent that wraps a paid API?**
A: Yes. Use `manifest.json` settings to let the buyer provide their own API keys. Your talent package defines the integration logic; the buyer supplies their credentials at hire time.

---

<!-- zh-CN -->

## 中文版

[English Version / 英文版](#welcome-to-the-party)

---

## 概览

OneManCompany 的员工（AI Agent）通过 **Talent 包** 定义能力。你可以把自己已有的 Claude 配置（system prompt、skills、tools）和 OpenClaw 配置打包成一个 talent，让 HR 直接招聘入职 —— 也可以发布到 Talent Market 供其他公司使用。

本文档覆盖三条路径：

| 路径 | 适合场景 |
|---|---|
| [接入 Claude 配置](#接入-claude-配置) | 你有自己的 Claude prompt / skills / MCP tools，想让公司里的 AI 员工也能用 |
| [接入 OpenClaw 配置](#接入-openclaw-配置) | 你有 OpenClaw gateway 配置（Telegram / Discord / Slack 等渠道），想让员工管理多渠道通讯 |
| [在 Talent Market 售卖](#在-talent-market-售卖) | 你做了一个好用的 talent，想分享或卖给其他公司 |

---

## 接入 Claude 配置

### 你有什么

通常你的 Claude 配置散落在这些地方：

```text
~/.claude/
├── settings.json          # 全局权限、MCP server 列表
├── keybindings.json       # 快捷键
└── projects/<project>/
    ├── CLAUDE.md           # 项目级 system prompt
    └── memory/             # 自动记忆

<your-project>/
├── CLAUDE.md               # 仓库级 system prompt
└── .claude/
    └── skills/             # 技能目录（Markdown 文件）
        ├── my-skill-a/
        │   └── SKILL.md
        └── my-skill-b/
            └── SKILL.md
```

### 映射关系

| 你的 Claude 配置 | Talent 包对应位置 | 说明 |
|---|---|---|
| `CLAUDE.md` (system prompt) | `profile.yaml` → `system_prompt_template` | 主指令注入员工 agent |
| `.claude/skills/*.md` | `skills/` 目录 | 每个子目录放一个 `SKILL.md`，内容注入员工 prompt |
| MCP tools | `tools/manifest.yaml` → `mcp_servers` | 声明 MCP server 命令和参数 |
| LangChain @tool | `tools/*.py` + `tools/manifest.yaml` → `custom_tools` | 自定义工具实现 |
| 内置工具 (bash, read_file 等) | `tools/manifest.yaml` → `builtin_tools` | 引用平台已注册的工具名 |

### 步骤

#### 1. 创建 Talent 目录

```bash
mkdir -p src/onemancompany/talent_market/talents/my-claude-agent/{skills,tools}
```

#### 2. 编写 profile.yaml

```yaml
id: my-claude-agent
name: My Claude Agent
description: 基于我自己的 Claude 配置，擅长 XXX。
role: Engineer            # Engineer / Designer / QA / Assistant / Manager
remote: false
hosting: company          # company = 平台托管 | self = 自托管(Claude CLI)
auth_method: api_key
api_provider: openrouter  # openrouter / anthropic / openai
llm_model: ''             # 留空则使用公司默认模型
temperature: 0.7
hiring_fee: 0.5
salary_per_1m_tokens: 0.0
skills:
  - my-skill-a
  - my-skill-b
personality_tags:
  - helpful
  - thorough
system_prompt_template: >
  你是一个专业的 XXX 助手。
  （把你 CLAUDE.md 里的核心指令放在这里）
```

#### 3. 迁移 Skills

把你的 `.claude/skills/` 下的每个技能目录复制到 talent 的 `skills/` 下：

```bash
# 从你的 Claude 技能目录复制
cp -r /path/to/.claude/skills/my-skill-a talents/my-claude-agent/skills/

# 确保每个技能目录下有 SKILL.md（或 *.md）
```

SKILL.md 格式：

```markdown
---
name: My Skill A
description: 这个技能做什么
autoload: true
---

# My Skill A

具体的技能指令和知识...
```

> **提示**：也可以用符号链接指向你已有的技能目录：

> ```bash
> ln -s /path/to/.claude/skills/my-skill-a talents/my-claude-agent/skills/my-skill-a
> ```

#### 4. 配置 Tools

创建 `tools/manifest.yaml`：

```yaml
# 内置工具 — 引用平台已注册的工具名
builtin_tools:
  - read_file
  - write_file
  - list_dir
  - bash

# 自定义工具 — 对应 tools/ 下的 .py 文件（不含后缀）
custom_tools:
  - my_custom_tool

# MCP Server — 外部工具服务
mcp_servers:
  fastskills:
    command: uvx
    args: ["fastskills", "--skills-dir", "{skills_dir}"]
    env:
      SKILLSMP_API_KEY: "{SKILLSMP_API_KEY}"
```

自定义工具示例（`tools/my_custom_tool.py`）：

```python
from langchain_core.tools import tool

@tool
def my_custom_tool(query: str) -> str:
    """工具描述 — HR 和员工都能看到这段话。"""
    # 你的实现
    return f"Result for: {query}"
```

#### 5. （可选）创建 manifest.json

如果你的 talent 需要前端设置 UI（比如让用户填 API Key），创建 `manifest.json`：

```json
{
  "id": "my-claude-agent",
  "name": "My Claude Agent",
  "version": "1.0.0",
  "role": "Engineer",
  "hosting": "company",
  "settings": {
    "sections": [
      {
        "id": "connection",
        "title": "连接配置",
        "fields": [
          {"key": "api_key", "type": "secret", "label": "API Key", "required": true}
        ]
      }
    ]
  },
  "prompts": {
    "skills": ["skills/*/SKILL.md"]
  },
  "tools": {
    "builtin": ["read_file", "write_file", "bash"],
    "custom": []
  }
}
```

#### 6. 招聘入职

启动平台后，HR 会自动发现你的 talent。CEO 确认招聘即可。

---

## 接入 OpenClaw 配置

### 你已有的配置

OpenClaw 是一个多渠道 AI 网关，你可能已经有：

```text
~/.openclaw/
└── openclaw.json          # OpenClaw 全局配置（API key、模型等）

# 以及各渠道的 credentials：
#   - Telegram Bot Token
#   - Discord Bot Token
#   - Slack App Token + Bot Token
#   - ElevenLabs API Key (语音)
#   - Brave Search API Key
```

### OpenClaw 步骤

#### 1. 使用内置 OpenClaw Talent

项目已自带 OpenClaw talent 模板，路径：

```text
src/onemancompany/talent_market/talents/openclaw/
├── profile.yaml        # 身份信息
├── manifest.json       # 设置 UI（渠道 Token 输入框）
├── launch.sh           # 启动脚本（自动安装 + 启动 gateway）
└── skills/
    └── multi-channel-comms/
        └── SKILL.md    # 多渠道通讯技能描述
```

你只需要在招聘后配置渠道凭证即可。

#### 2. 配置渠道凭证

**方式 A — 通过前端设置 UI**

招聘 OpenClaw 员工后，在员工设置面板中填写：

| Field | Description |
|---|---|
| Telegram Bot Token | 从 @BotFather 获取 |
| Discord Bot Token | Discord Developer Portal → Bot → Token |
| Slack App Token | `xapp-...` 格式，App-Level Token |
| Slack Bot Token | `xoxb-...` 格式，OAuth Bot Token |
| ElevenLabs API Key | 语音合成（可选） |
| Brave Search API Key | 搜索能力（可选） |

**方式 B — 通过 .env 文件**

在项目根目录 `.env` 中添加：

```bash
# OpenClaw
OPENROUTER_API_KEY=sk-or-...

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC...

# Discord
DISCORD_BOT_TOKEN=MTIz...

# Slack
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...

# 可选
ELEVENLABS_API_KEY=...
BRAVE_API_KEY=...
```

#### 3. 自定义 OpenClaw Talent

如果你想基于 OpenClaw 做定制（比如只用 Telegram + 特定 prompt），复制一份：

```bash
cp -r talents/openclaw talents/my-openclaw
```

然后修改 `my-openclaw/profile.yaml`：

```yaml
id: my-openclaw
name: My OpenClaw Bot
description: 专注 Telegram 客服的 OpenClaw 网关。
system_prompt_template: >
  你是一个 Telegram 客服机器人。
  只回答产品相关问题，语气友好简洁。
```

修改 `manifest.json` 只保留你需要的渠道字段。

#### 4. launch.sh 工作原理

`launch.sh` 会自动：

1. 从 `.env` 加载环境变量
2. 检查并安装 `openclaw`（通过 npm）
3. 首次运行时用 `OPENROUTER_API_KEY` 自动配置
4. 启动 gateway（端口 18789）
5. 通过 `openclaw agent` 执行任务

你不需要手动管理 gateway 生命周期。

---

## 在 Talent Market 售卖

做好一个 talent 后，你可以发布到 Talent Market，让其他 OneManCompany 实例发现、招聘和使用。

### 发布的内容

你的整个 talent 目录就是可分发单元：

```text
talents/my-awesome-agent/
├── profile.yaml            # 身份、定价、描述
├── manifest.json           # 设置 UI、能力声明
├── skills/                 # 打包的技能
├── tools/                  # 打包的工具 + MCP 配置
├── functions/              # 打包的函数
└── launch.sh               # 启动脚本（如有）
```

### 定价

在 `profile.yaml` 中设置价格：

```yaml
hiring_fee: 2.0               # 一次性招聘费 — 其他公司招聘此 talent 时收取
salary_per_1m_tokens: 0.5     # 每百万 token 持续成本（0 = 按底层模型自动计算）
```

- `hiring_fee` — 招聘时一次性收取，相当于安装 / 授权费。
- `salary_per_1m_tokens` — 持续运营成本。设为 `0` 则平台根据底层 LLM 模型定价自动计算。

### 写好你的上架信息

`profile.yaml` 的字段同时充当市场展示信息：

| 字段 | 市场展示角色 |
|---|---|
| `name` | 上架标题 |
| `description` | HR 浏览时看到的介绍 — 写清楚、写吸引人 |
| `role` | 决定 talent 出现在哪个部门分类下 |
| `personality_tags` | HR 可搜索的标签 |
| `skills` | 展示在卡片上的技能徽章 |
| `hiring_fee` | 显示的价格标签 |

上架技巧：

- **Description**：先说 talent *能做什么*，而不是 *它是什么*。"自动化 Telegram 客服，内置 20+ 话术模板和升级路由逻辑" 比 "一个 Telegram 机器人 agent" 好。
- **Skills**：列具体的、有辨识度的技能。`telegram-cs`、`escalation-routing` 好过 `helpful`、`versatile`。
- **Personality tags**：想想 HR 会搜什么。`always-on`、`multi-channel`、`code-review`。

### 发布方式

把 talent 目录放进共享的 talent 注册表：

```bash
# 拷入 talent market
cp -r my-awesome-agent src/onemancompany/talent_market/talents/

# 平台启动时自动发现
```

如果要跨实例分发，把 talent 目录打包分享（git repo、zip、npm package 皆可）。任何 OneManCompany 实例只需把目录放到 `talent_market/talents/` 下即可导入。

### 版本管理

通过 `manifest.json` → `version` 跟踪版本：

```json
{
  "version": "1.2.0"
}
```

遵循语义化版本：patch 修 bug，minor 加技能/工具，major 有不兼容变更（如重命名 settings key 需要重新配置）。

---

## Talent 包完整结构参考

```text
talents/{talent-id}/
├── profile.yaml            # [必须] 身份 + 招聘信息
├── manifest.json           # [可选] 前端设置 UI + 能力声明
├── launch.sh               # [可选] 自托管启动脚本
├── skills/                 # [可选] 技能目录
│   └── {skill-name}/
│       └── SKILL.md        #   技能描述（注入员工 prompt）
├── tools/                  # [可选] 工具目录
│   ├── manifest.yaml       #   工具清单（builtin + custom + mcp）
│   └── *.py                #   自定义 LangChain @tool 实现
└── functions/              # [可选] 函数目录
    ├── manifest.yaml       #   函数元信息声明
    └── {name}.py           #   函数实现
```

入职流程：HR 浏览 talent market → CEO 确认招聘 → 平台分配工号/部门/工位 → 复制 skills/tools 到员工目录 → 生成花名 → 注册到 EmployeeManager → 开始工作。

---

## 常见问题

**Q: 我的 Claude skills 用了符号链接，入职后还能用吗？**
A: 可以。平台复制 skills 目录时会 follow symlink，复制实际文件。你也可以在 talent 中直接用 symlink 指向共享技能。

**Q: 自托管 (self) 和公司托管 (company) 怎么选？**
A: `company` = 平台内 LangChain agent loop 执行，适合大多数场景。`self` = 员工自带运行环境（如 Claude Code CLI），适合需要完整本地能力的场景。OpenClaw 因为需要管理 gateway 进程，使用 `company` + `launch.sh`。

**Q: 可以同时用 Claude skills 和 OpenClaw 吗？**
A: 可以。创建一个 talent，在 `skills/` 放 Claude 技能，同时在 `launch.sh` 中启动 OpenClaw gateway。两者互不冲突。

**Q: 怎么给现有员工追加新技能？**
A: 直接把新的 SKILL.md 放到 `employees/{id}/skills/` 目录下，下次任务执行时自动加载。

**Q: 可以售卖依赖付费 API 的 talent 吗？**
A: 可以。通过 `manifest.json` 的 settings 让买家在招聘时填入自己的 API Key。你的 talent 包定义集成逻辑，买家提供凭证。
