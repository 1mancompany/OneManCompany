# Talent Development Guide for AI Coders

> 给 AI Coder（Claude、GPT 等）的 talent 开发完整指南。
> 读完此文档后，你应该能独立完成一个 talent 包的创建。

---

## Quick Start — 最小可用 Talent

```
talents/my_talent/
├── profile.yaml        # 必须
├── launch.sh           # 推荐（company-hosted 执行入口）
└── skills/
    └── core_skill.md   # 至少一个
```

```yaml
# profile.yaml
id: my_talent
name: My Talent Name
description: 一句话描述这个 talent 的能力。
role: Engineer                    # Engineer / Designer / Manager / QA / ...
remote: false
hosting: company                  # company / self / remote
auth_method: api_key
api_provider: openrouter
llm_model: google/gemini-3.1-pro-preview
temperature: 0.7
hiring_fee: 0.50
salary_per_1m_tokens: 0
skills:
  - core_skill                    # 对应 skills/core_skill.md
tools: []
personality_tags:
  - efficient
system_prompt_template: >
  You specialize in [领域]. [工作方式]. [决策风格].
```

这就够了。平台会在入职时自动补全缺失的配置。

---

## 目录结构（完整版）

```
talents/{talent_id}/
├── profile.yaml              # 必须 — 身份、模型、技能、工具
├── launch.sh                 # 推荐 — 任务执行脚本
├── manifest.json             # 可选 — 前端设置 UI（OAuth、参数调整）
├── CLAUDE.md                 # 可选 — Claude CLI 项目指令
├── skills/                   # 推荐 — 技能知识库
│   └── *.md                  # 每个文件 = 一项技能，内容注入 system prompt
├── tools/                    # 可选 — 工具声明
│   ├── manifest.yaml         # 工具清单
│   └── *.py                  # 自定义 LangChain @tool
├── functions/                # 可选 — 贡献给公司的工具
│   ├── manifest.yaml
│   └── *.py
└── vessel/                   # 可选 — 高级 agent 配置
    ├── vessel.yaml
    └── prompt_sections/*.md
```

---

## profile.yaml 字段详解

### 身份字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | str | 是 | 唯一标识符，与目录名一致 |
| `name` | str | 是 | 显示名称 |
| `description` | str | 是 | 招聘时展示给 CEO 的描述 |
| `role` | str | 是 | 角色类型，决定入职部门 |

`role` 与部门的映射关系：

| role | 部门 |
|------|------|
| Engineer | 技术研发部 |
| Designer | 设计创意部 |
| Manager | 运营管理部 |
| QA | 质量保障部 |
| 其它 | 综合事务部 |

### 模型字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `api_provider` | str | LLM 提供商：`openrouter`, `anthropic`, `openai` |
| `llm_model` | str | 模型 ID（如 `claude-sonnet-4-20250514`） |
| `temperature` | float | 推理温度 0–2 |
| `image_model` | str | 图像生成模型（Designer 专用，可选） |

### 部署字段

| 字段 | 说明 |
|------|------|
| `hosting: company` | 平台托管 — 通过 `SubprocessExecutor` 运行 `launch.sh` |
| `hosting: self` | 自托管 — Claude CLI 独立进程 |
| `hosting: remote` | 远程 — 外部 worker 通过 HTTP 轮询 |
| `auth_method: api_key` | 使用 API key 调用 LLM |
| `auth_method: cli` | 使用本机 CLI 凭证 |
| `auth_method: oauth` | OAuth PKCE 登录 |
| `auth_method: none` | 无需认证 |

### 能力字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `skills` | list | 技能 ID 列表，对应 `skills/*.md` 文件名 |
| `tools` | list | 可用工具名列表 |
| `personality_tags` | list | 性格标签（HR 匹配用） |
| `system_prompt_template` | str | 灵魂 prompt — 定义 talent 怎么思考 |

---

## system_prompt_template 写法

这是 talent 的**核心人格定义**。它被注入到 prompt 的第 12 优先级（Identity 之后，Skills 之前）。

### Prompt 分层

```
Priority 10: Identity        — "You are 小明, Manager in 产品部"（系统生成）
Priority 12: Talent Persona  — ← system_prompt_template 在这里
Priority 30: Skills           — skills/*.md 的全部内容
Priority 35: Tools            — 已授权工具列表
Priority 50: Work Principles  — CEO 1-on-1 后的个人工作准则
Priority 70: Context          — 当前时间、团队状态
```

### 规则

1. **不要写身份信息** — Identity 层已提供 name/role/department
2. **写能力定位** — 这个 talent 擅长什么、怎么工作
3. **写行为指引** — 如何使用 skills、如何做决策
4. **保持简洁** — 2-5 句话，不超过一段
5. **用第二人称** — "You" 或 "你" 开头

### 示例

```yaml
# ✅ 好的写法
system_prompt_template: >
  You specialize in full-stack development with deep expertise in Python,
  TypeScript, and cloud infrastructure. Write production-ready code with
  tests. Prefer simple, maintainable solutions over clever abstractions.
  Always verify assumptions by reading existing code before proposing changes.

# ✅ 好的写法
system_prompt_template: >
  You are equipped with 46 professional PM frameworks. Use your skills
  library to select the right tool for each challenge. Ground analysis
  in frameworks, not generic advice.

# ❌ 不好的写法 — 重复 identity
system_prompt_template: >
  You are a senior engineer named Alice in the Engineering Department.

# ❌ 不好的写法 — 太模糊
system_prompt_template: >
  You are a helpful assistant. Be professional and do your best.
```

---

## Skills 编写

### 文件约定

- 路径：`skills/{skill_id}.md`
- `skill_id` 必须出现在 `profile.yaml` 的 `skills` 列表中
- 内容直接注入 system prompt，所以是**声明式知识**，不是可执行代码

### 结构模板

```markdown
# Skill Name

## Purpose
一句话说明：这个技能做什么、什么时候用。

## Key Concepts
核心框架、定义、思维模型。
- 用列表或表格
- 定义可能混淆的术语
- 包含"不是什么"（anti-patterns）

## Application
具体场景的分步指导。
- 写成 agent 可以直接执行的指令
- 序列操作用编号步骤
- 标注决策点和分支逻辑

## Examples
真实案例展示技能的应用。
- 展示"好"和"不好"的对比
- 具体而非泛化

## Common Pitfalls
常见错误及其后果。
- 命名失败模式
- 说明后果
- 给出纠正方法
```

### 要点

- **简洁** — Agent 的 context window 有限，每个 skill 不要超过 500 行
- **可操作** — 写成指令，不是教科书
- **自包含** — 不要假设 agent 有外部知识

---

## launch.sh 编写

### Company-Hosted（前台模式）

平台通过 `SubprocessExecutor` 调用，每个任务一个进程：

```
bash launch.sh <employee_dir>
```

**环境变量（自动注入）：**

| 变量 | 说明 |
|------|------|
| `OMC_EMPLOYEE_ID` | 员工 ID |
| `OMC_TASK_ID` | 任务 ID |
| `OMC_PROJECT_ID` | 项目 ID |
| `OMC_PROJECT_DIR` | 项目工作目录 |
| `OMC_TASK_DESCRIPTION` | 完整任务描述 |
| `OMC_SERVER_URL` | 后端 URL |
| `OMC_MAX_ITERATIONS` | 最大 agent 迭代数（默认 20） |

**输出约定：**
- `stdout` → 仅一行 JSON（结果）
- `stderr` → 日志
- `exit 0` → 成功，非零 → 失败

```json
{"output": "任务结果", "model": "model-id", "input_tokens": 100, "output_tokens": 50}
```

**超时/取消：** 平台管理。SIGTERM → 30s → SIGKILL。用 `trap cleanup EXIT` 响应。

**模板：** 参考 `company/assets/tools/launch_template.sh`

### Self-Hosted（后台模式）

入职时复制到员工目录，手动启动：

```bash
#!/usr/bin/env bash
EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
# ... 启动 worker ...
nohup "$PYTHON" "$WORKER_SCRIPT" "$EMPLOYEE_DIR" > "$LOG_FILE" 2>&1 &
echo $! > "$EMPLOYEE_DIR/worker.pid"
```

---

## Tools 开发

### 使用平台内置工具

在 `profile.yaml` 的 `tools` 列表中声明即可：

```yaml
tools:
  - sandbox_execute_code
  - sandbox_run_command
```

### 自定义 LangChain 工具

1. 创建 `tools/manifest.yaml`：
```yaml
builtin_tools:
  - sandbox_execute_code
custom_tools:
  - my_analyzer
```

2. 创建 `tools/my_analyzer.py`：
```python
from langchain_core.tools import tool

@tool
def my_analyzer(code: str) -> str:
    """Analyze code quality and return suggestions."""
    # 实现逻辑
    return f"Analysis: {len(code)} chars, looks good."
```

### 贡献公司级工具（functions/）

如果你的工具应该**共享给其它员工**：

1. 创建 `functions/manifest.yaml`：
```yaml
functions:
  - name: "shared_tool"
    description: "A tool everyone can use"
    scope: "company"        # "company" = 全公司, "personal" = 仅自己
```

2. 创建 `functions/shared_tool.py`（同上格式）

入职时自动安装到 `company/assets/tools/shared_tool/`。

---

## manifest.json — 前端设置 UI

只在需要自定义设置界面时创建。支持的字段类型：

| type | 渲染 | 场景 |
|------|------|------|
| `text` | 单行输入 | 模型名、URL |
| `secret` | 密码输入 | API key |
| `number` | 数字输入 | 温度、超时 |
| `select` | 下拉选择 | 模型列表 |
| `toggle` | 开关 | 开启/关闭功能 |
| `oauth_button` | OAuth 按钮 | 第三方登录 |
| `readonly` | 只读显示 | 状态信息 |

```json
{
  "id": "my-talent",
  "name": "My Talent",
  "version": "1.0.0",
  "settings": {
    "sections": [
      {
        "id": "connection",
        "title": "Connection",
        "fields": [
          {"key": "api_key", "type": "secret", "label": "API Key", "required": true},
          {"key": "temperature", "type": "number", "label": "Temperature", "default": 0.7, "min": 0, "max": 2, "step": 0.1}
        ]
      }
    ]
  }
}
```

---

## vessel.yaml — 高级 Agent 配置

只在需要自定义 agent 行为时创建：

```yaml
# vessel/vessel.yaml
runner:
  module: ""                    # 自定义 runner 模块（vessel/ 下的 .py）
  class_name: ""                # BaseAgentRunner 子类名
hooks:
  module: ""                    # 钩子模块
  pre_task: ""                  # 任务开始前回调
  post_task: ""                 # 任务完成后回调
context:
  prompt_sections:              # 额外 prompt 注入
    - file: prompt_sections/guide.md
      name: guide
      priority: 40              # 10-80，越小越靠前
  inject_progress_log: true     # 注入历史进度日志
  inject_task_history: true     # 注入任务历史
limits:
  max_retries: 3
  task_timeout_seconds: 600     # 默认超时
```

---

## 入职时发生了什么

当 CEO 批准招聘后，`onboarding.py` 按以下顺序执行：

```
1. 分配员工号（如 00042）
2. 创建员工目录 company/human_resource/employees/00042/
3. 复制 skills/*.md → 员工目录/skills/
4. 复制 manifest.json、CLAUDE.md（如果存在）
5. 复制 launch.sh、heartbeat.sh（如果存在，chmod +x）
6. 复制 vessel/ 或 agent/ 配置
7. 注册工具权限（tools/manifest.yaml 中的 custom_tools）
8. 安装 functions/（复制到公司中心工具目录）
9. 生成花名（武侠风格中文昵称）
10. 写入 work_principles.md（初始工作准则）
11. 写入 system_prompt_template → prompts/talent_persona.md
12. 注册到 EmployeeManager（开始接受任务）
```

**关键：** 入职后 talent 目录不再被引用。所有运行时文件在员工目录中。

---

## 三种 Hosting 模式对比

| | Company | Self-Hosted | Remote |
|---|---|---|---|
| **执行方式** | `SubprocessExecutor` 前台 | Claude CLI 子进程 | 外部 HTTP 轮询 |
| **任务传递** | 环境变量 | CLI 参数 | HTTP API |
| **结果返回** | stdout JSON | CLI 输出 | HTTP POST |
| **超时/取消** | 平台 SIGTERM→SIGKILL | 平台管理 | worker 自管 |
| **launch.sh** | 前台模式（推荐） | 后台模式 | 不需要 |
| **skills 复制** | 是 | 是 | 否 |
| **tools 复制** | 是 | 是 | 否 |
| **典型场景** | OpenRouter/OpenAI 员工 | Claude Code 员工 | GPU 集群推理节点 |

---

## MCP 工具访问

Company-hosted 员工可通过 MCP stdio 协议访问公司工具：

```bash
# 在 launch.sh 中启动 MCP server
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

coproc MCP_PROC {
    exec "$PYTHON" -m onemancompany.tools.mcp.server 2>/dev/null
}
# 通过 JSON-RPC 2.0 与 MCP server 交互
```

可用工具（取决于权限）：
- `dispatch_child` — 派发子任务
- `accept_child` / `reject_child` — 验收/驳回子任务
- `list_colleagues` — 列出同事
- `pull_meeting` — 拉人对齐

---

## Checklist — 发布前检查

- [ ] `profile.yaml` 有 `id`, `name`, `role`, `skills` 字段
- [ ] `skills/` 下每个 `profile.yaml` 列出的 skill 都有对应 `.md` 文件
- [ ] `system_prompt_template` 不重复 identity 信息，2-5 句话
- [ ] `launch.sh`（如果有）以 `set -euo pipefail` 开头
- [ ] `launch.sh` 日志写 stderr，结果 JSON 写 stdout
- [ ] `tools/` 下的 `.py` 文件都有 `@tool` 装饰器
- [ ] `manifest.json`（如果有）的 `id` 与 `profile.yaml` 的 `id` 一致
- [ ] 没有硬编码路径（使用 `$1` 和环境变量）
- [ ] 没有遗留的测试数据或占位符
