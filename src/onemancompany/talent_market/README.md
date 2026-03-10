# Talent Market

The talent market provides structured **talent packages** for hiring and
multiple **connection modes** for running agents.

---

## Talent Package Structure

Each talent lives in `talents/{talent_id}/`:

```
talents/{talent_id}/
├── profile.yaml          # 必须 — 身份 + 招聘信息 + system_prompt_template
├── CLAUDE.md             # 可选 — Claude CLI 项目指令（入职时复制到员工目录）
├── manifest.json         # 可选 — 前端设置 UI + 能力声明
├── launch.sh             # 可选 — 自托管员工的启动脚本
├── run_worker.py         # 可选 — 远程员工的 worker 入口
├── skills/               # 可选 — 技能（文件夹制，见下方技能规范）
│   └── {skill-name}/     # 每个技能一个文件夹
│       └── SKILL.md      # 技能定义（含 YAML frontmatter）
├── agent/                # 可选 — agent 配置（额外 prompt sections）
│   ├── manifest.yaml     # prompt section 声明
│   └── prompt_sections/  # 额外 prompt 片段
└── tools/                # 可选 — 工具声明与自定义工具
    ├── manifest.yaml     # 工具清单（builtin_tools + custom_tools）
    └── *.py              # 自定义 LangChain @tool 实现
```

> 数据类定义见 `talent_spec.py`，包含所有文件的字段说明。

### `profile.yaml`（必须）

Talent 的身份信息，HR 招聘时展示给 CEO。

| Field | Type | Description |
|---|---|---|
| `id` | str | Talent 唯一标识符，与目录名一致 |
| `name` | str | 显示名称（如 "Coding Talent"） |
| `description` | str | Talent 描述文字 |
| `role` | str | 角色类型（Engineer, Designer, QA 等），决定入职部门 |
| `remote` | bool | `true` = 远程工作（不分配工位），`false` = 本地办公 |
| `hosting` | str | 运行模式：`company` / `self` / `remote`（见下方连接模式） |
| `auth_method` | str | 认证方式：`api_key` / `oauth` / `cli` / `none` |
| `api_provider` | str | LLM 提供商（`openrouter`, `anthropic` 等） |
| `llm_model` | str | 默认 LLM 模型标识符 |
| `temperature` | float | 默认推理温度 |
| `image_model` | str | 图像生成模型（可选，Designer 使用） |
| `hiring_fee` | float | 招聘费用 |
| `salary_per_1m_tokens` | float | 每百万 token 薪酬（0 = 按模型自动计算） |
| `skills` | list | 技能标识符列表，对应 `skills/` 下的文件夹名 |
| `tools` | list | 工具名列表 |
| `personality_tags` | list | 性格标签（HR 匹配用） |
| `system_prompt_template` | str | Talent Persona prompt（见下方详细说明） |

### `system_prompt_template` 写作规范

`system_prompt_template` 是 talent 的**灵魂 prompt**——定义这个 talent 的核心能力、
工作方式和思维框架。入职时写入 `employees/{id}/prompts/talent_persona.md`，
在所有 prompt 路径中以 **Talent Persona** 层注入。

#### Prompt 分层协议

员工的最终 system prompt 按 priority 从小到大拼接：

```
Priority 10: Identity       — "You are 小明 (花名: 铁匠), Manager in 产品部 (Lv.3)"
                               [系统自动生成，来自员工档案]
Priority 12: Talent Persona  — system_prompt_template 的内容
                               [来自 talent profile，入职时固化]
Priority 15: Work Approach   — 工作方法论（通用或自定义）
Priority 30: Skills          — skills/*.md 的全部内容
Priority 35: Tools           — 已授权工具列表 + 使用说明
Priority 40: Direction       — 公司战略方向
Priority 45: Culture         — 公司文化准则
Priority 50: Principles      — 员工个人工作准则（CEO 1-on-1 后更新）
Priority 55: Guidance        — CEO 指导批注
Priority 70: Context         — 当前时间、团队状态、活跃任务
Priority 80: Efficiency      — 效率指南
```

`system_prompt_template` 位于 **Identity 之后、Skills 之前**。
它不需要重复 identity 信息（name/role/department），这些由 Identity 层提供。
它应当聚焦于：这个 talent **怎么思考**、**怎么工作**、**擅长什么**。

#### 写作要点

1. **不要写身份信息** — 不需要 "You are {name}" 或 "Your role is {role}"，
   Identity 层已经提供
2. **写能力定位** — 说明这个 talent 的核心专长和工作方式
3. **写行为指引** — 如何使用 skills、如何做决策、输出标准
4. **保持简洁** — 2-5 句话为佳，不要超过一段。Skills 的细节由 skills/*.md 承载
5. **用第二人称** — 用 "You" 或 "你" 开头

#### 示例

**好的写法**（聚焦能力 + 行为方式）：

```yaml
# Product Manager talent
system_prompt_template: >
  You are equipped with 46 professional PM frameworks covering user stories,
  PRDs, positioning, discovery, prioritization, roadmapping, and financial
  analysis. Use your skills library to select the right framework for each
  product challenge. Ground analysis in frameworks, not generic advice.
  When unsure which framework to apply, ask clarifying questions first.
```

```yaml
# Full-stack Engineer talent
system_prompt_template: >
  You specialize in full-stack development with deep expertise in Python,
  TypeScript, and cloud infrastructure. Write production-ready code with
  tests. Prefer simple, maintainable solutions over clever abstractions.
  Always verify assumptions by reading existing code before proposing changes.
```

```yaml
# Data Analyst talent
system_prompt_template: >
  You excel at turning raw data into actionable insights. Use SQL for
  data extraction, Python for analysis, and clear visualizations to
  communicate findings. Always validate data quality before drawing
  conclusions. Present results with confidence intervals where applicable.
```

**不好的写法**：

```yaml
# ✗ 重复 identity 层信息
system_prompt_template: >
  You are a senior product manager named Alice in the Product Department.
  Your role is Manager and you work at level 3.

# ✗ 太模糊，没有提供具体能力或行为指引
system_prompt_template: >
  You are a helpful assistant. Be professional and do your best.

# ✗ 太长，把 skill 细节塞进来了
system_prompt_template: >
  You are a PM who can write user stories using the Mike Cohn format
  with Gherkin-style acceptance criteria. The format is: As a [user],
  I want to [action], so that [outcome]. Acceptance criteria follow
  the Given-When-Then pattern: Given [context], When [event], Then
  [expected result]. You can also create PRDs with the following
  sections: Overview, Problem Statement, Goals, User Stories...
  [300 more words]
```

#### CLAUDE.md 与 system_prompt_template 的关系

| 文件 | 用途 | 生效路径 |
|------|------|---------|
| `system_prompt_template` | 简洁的 persona prompt | Company-hosted (LangChain) + Self-hosted 的 PromptBuilder |
| `CLAUDE.md` | 完整的项目级指令 | Self-hosted Claude CLI 自动发现（cwd 下的 CLAUDE.md） |

- 如果 talent 来自 GitHub 仓库，`CLAUDE.md` 会被原样保存到 talent 目录，
  入职时复制到 `employees/{id}/CLAUDE.md`，供 Claude CLI 自动加载
- `system_prompt_template` 应始终独立于 `CLAUDE.md` 存在——
  它是简洁的 persona 定义，而 `CLAUDE.md` 可能包含详细的操作手册

### `manifest.json`（可选）

驱动前端设置 UI 和能力声明。无 manifest 的老员工走默认 UI。

```json
{
  "id": "claude-code-onsite",
  "name": "Claude Code Engineer",
  "version": "1.0.0",
  "role": "Engineer",
  "hosting": "self",
  "settings": {
    "sections": [
      {
        "id": "connection",
        "title": "Connection",
        "fields": [
          {"key": "oauth", "type": "oauth_button", "label": "Anthropic Login", "provider": "anthropic"},
          {"key": "llm_model", "type": "text", "label": "Model", "default": "claude-sonnet-4-20250514"},
          {"key": "temperature", "type": "number", "label": "Temperature", "default": 0.7, "min": 0, "max": 2, "step": 0.1}
        ]
      }
    ]
  },
  "prompts": {"skills": ["skills/*/SKILL.md"]},
  "tools": {"builtin": ["sandbox_execute_code"], "custom": ["tools/my_tool.py"]},
  "platform_capabilities": ["file_upload", "websocket"]
}
```

**Settings 字段类型**：

| type | 渲染 | 附加属性 |
|---|---|---|
| `text` | 单行文本输入 | — |
| `secret` | 密码输入（掩码） | — |
| `number` | 数字输入 | `min`, `max`, `step` |
| `select` | 单选下拉 | `options` 或 `options_from` |
| `multi_select` | 多选下拉 | `options` 或 `options_from` |
| `toggle` | 开关 | — |
| `textarea` | 多行文本 | — |
| `oauth_button` | OAuth 登录按钮 | `provider`（如 `"anthropic"`） |
| `color` | 颜色选择器 | — |
| `file` | 文件上传 | — |
| `readonly` | 只读显示 | `value_from`（数据源，如 `"api:sessions"`） |

### `tools/manifest.yaml`（可选）

```yaml
builtin_tools:          # 平台内置工具名
  - sandbox_execute_code
  - sandbox_run_command
custom_tools:           # 同目录下 .py 模块名（不含后缀）
  - custom_build        # → tools/custom_build.py 中导出 @tool 函数
```

### `skills/{name}/SKILL.md`（可选 — 技能规范）

技能采用**文件夹制**，每个技能一个目录，核心定义文件为 `SKILL.md`。
不再支持 `skills/*.md` 散文件格式。

```
skills/
├── ontology/
│   ├── SKILL.md              # 必须 — 技能定义（含 YAML frontmatter）
│   ├── references/           # 可选 — 参考文档
│   └── scripts/              # 可选 — 辅助脚本
├── proactive-agent/
│   └── SKILL.md
└── work-principles/
    └── SKILL.md
```

#### SKILL.md 格式

```markdown
---
name: Ontology
description: Typed knowledge graph for structured agent memory
autoload: true          # true = 全文注入 prompt; false/省略 = 仅出现在目录，按需加载
---

# Ontology

技能正文...
```

| Frontmatter 字段 | 类型 | 说明 |
|---|---|---|
| `name` | str | 技能显示名称（省略时用文件夹名） |
| `description` | str | 一句话描述（出现在技能目录中） |
| `autoload` | bool | `true` = 始终注入 prompt；`false`/省略 = 员工通过 `load_skill` 工具按需加载 |

#### 技能加载机制

遵循 Claude 的 skill 规范：

1. **Autoloaded 技能**（`autoload: true`）— 全文注入 system prompt，适合工作准则等始终生效的内容
2. **目录技能**（默认）— prompt 中仅展示名称 + 描述，员工调用 `load_skill("skill-name")` 工具获取全文

> **开发规范**：所有新 talent 必须使用文件夹制技能。入职流程 (`onboarding.py`) 会自动
> 将 talent 的 `skills/` 复制到员工目录，并注入三个默认技能（ontology, proactive-agent,
> self-improving-agent）。

---

## Connection Modes（连接模式）

平台支持三种连接模式，由 `profile.yaml` 中的 `hosting` 字段决定。

### 1. Company-Hosted（`hosting: "company"`）

**公司托管** — 最常见的模式。平台通过 `SubprocessExecutor` 运行 `launch.sh` 脚本。

```yaml
# profile.yaml
hosting: company          # 或不填，默认值
auth_method: api_key      # 使用 API key 调用 LLM
api_provider: openrouter  # LLM 提供商
llm_model: google/gemini-3.1-pro-preview-customtools
```

**工作方式**：
- 平台使用 `SubprocessExecutor` 以**前台进程**运行 `launch.sh`
- 每次任务 = 一次 `launch.sh` 调用，任务描述通过 `OMC_TASK_DESCRIPTION` 环境变量传入
- 脚本将结果 JSON 输出到 stdout，日志输出到 stderr
- 超时和取消由平台管理（SIGTERM → 30s → SIGKILL）
- 如无自定义 `launch.sh`，平台使用 `LangChainLauncher` 作为回退
- 公司工具通过 MCP stdio 协议提供（可选）

**认证配置**：
- `auth_method: api_key` — manifest 中使用 `{"type": "secret", "key": "api_key"}` 字段
- `auth_method: none` — 无需认证（免费模型或提供商已配置）

**适用场景**：使用 OpenRouter、OpenAI 等 API 的标准 AI 员工

### 2. Self-Hosted（`hosting: "self"`）

**自托管** — 员工自带运行环境，作为独立进程运行。

```yaml
# profile.yaml
hosting: self
auth_method: oauth        # OAuth PKCE 登录
api_provider: anthropic
llm_model: claude-sonnet-4-20250514
```

**工作方式**：
- 平台使用 `ClaudeSessionLauncher` 按需启动 `claude --print` CLI 进程
- 每次任务 = 一次 CLI 调用，任务完成后进程退出
- 通过 `sessions.json` 维护会话上下文（同一 project 的后续调用使用 `--resume`）
- 如果 talent 提供了 `launch.sh`，入职时复制到员工目录

**认证配置**：
- `auth_method: oauth` — manifest 中使用 `{"type": "oauth_button", "provider": "anthropic"}` 字段，
  触发 Anthropic OAuth PKCE 流程，token 存储在员工配置中
- `auth_method: cli` — 使用本机已登录的 Claude CLI 凭证，无需额外配置

**入职时额外生成的文件**：
- `connection.json` — 包含 `employee_id`, `company_url`, `talent_id`
- `launch.sh`（从 talent 复制）— 启动脚本
- `sessions.json`（运行时生成）— 会话记录

**适用场景**：Claude Code CLI、本地 AI 工具等自带运行环境的员工

### 3. Remote（`hosting: "remote"` / `remote: true`）

**远程** — 员工在外部节点运行，通过 HTTP 协议与公司通信。

```yaml
# profile.yaml
remote: true
hosting: remote           # 可省略，remote: true 时自动推断
```

**工作方式**：
- 员工在外部机器上运行 worker 进程（继承 `RemoteWorkerBase`）
- 通过 HTTP 轮询获取任务、提交结果、发送心跳
- 平台不管理其进程生命周期，只提供任务队列
- Skills 和 tools 不复制到本地 — 远程 worker 自带

**入职时额外生成的文件**：
- `connection.json` — worker 启动时读取，包含公司 URL 等连接信息

**适用场景**：在远程服务器运行的 AI worker、GPU 集群上的推理节点

---

## Connection Mode Comparison

| | Company | Self-Hosted | Remote |
|---|---|---|---|
| **hosting 值** | `company`（默认） | `self` | `remote` |
| **进程管理** | `SubprocessExecutor` 前台进程 | 平台按需启动 CLI | 外部自行管理 |
| **Launcher** | `SubprocessExecutor`（有 launch.sh）/ `LangChainLauncher`（回退） | `ClaudeSessionLauncher` | 无（HTTP 任务队列） |
| **LLM 调用** | launch.sh 内自行调用 LLM API | CLI 自带凭证/OAuth | Worker 自行调用 |
| **认证方式** | api_key / none | oauth / cli | — |
| **skills/tools 复制** | 是 | 是 | 否 |
| **工位分配** | 是 | 是 | 否（远程标记） |
| **connection.json** | 否 | 是 | 是 |
| **launch.sh** | 推荐（前台模式） | 可选（后台模式） | 否 |
| **会话管理** | 无状态（每次任务一个进程） | sessions.json | worker 自行管理 |
| **超时/取消** | 平台管理（SIGTERM→SIGKILL） | 平台管理 | worker 自行管理 |

---

## Remote Worker Protocol

远程 worker 通过四个 HTTP 端点与公司通信：

| Endpoint | Method | Description |
|---|---|---|
| `/api/remote/register` | POST | Worker 注册 |
| `/api/remote/tasks/{employee_id}` | GET | 轮询待办任务 |
| `/api/remote/results` | POST | 提交任务结果 |
| `/api/remote/heartbeat` | POST | 心跳保活 |

### Flow

1. **Register** — Worker 启动后 POST `/api/remote/register`，携带 `employee_id`、回调 URL、能力列表
2. **Poll** — 定期 GET `/api/remote/tasks/{employee_id}`，有任务时返回 `TaskAssignment`
3. **Execute** — Worker 在自己的环境中执行任务
4. **Submit** — POST `TaskResult` 到 `/api/remote/results`
5. **Heartbeat** — 定期 POST `/api/remote/heartbeat` 报告存活

### Data Models

见 `remote_protocol.py`：`RemoteWorkerRegistration`, `TaskAssignment`, `TaskResult`, `HeartbeatPayload`

---

## Writing launch.sh（启动脚本编写指南）

`launch.sh` 是 company-hosted 员工的核心执行入口。平台通过 `SubprocessExecutor` 以**前台进程**
运行此脚本，与 self-hosted 的后台 worker 模式不同。

> 模板文件：`company/assets/tools/launch_template.sh`

### 两种 launch.sh 模式

| | Company-Hosted（前台） | Self-Hosted（后台） |
|---|---|---|
| **运行方式** | `SubprocessExecutor` 直接调用 | 入职时复制到员工目录，手动启动 |
| **生命周期** | 每个任务一个进程，任务完成后退出 | 长驻后台，轮询任务队列 |
| **任务来源** | `OMC_TASK_DESCRIPTION` 环境变量 | HTTP 轮询 `/api/remote/tasks/` |
| **结果输出** | stdout JSON | HTTP POST `/api/remote/results` |
| **超时/取消** | 平台管理（SIGTERM → 30s → SIGKILL） | 自行管理 |
| **PID 管理** | 不需要 | `worker.pid` 文件 |

**本节仅描述 Company-Hosted 前台模式。** Self-Hosted 后台模式见各 talent 的 `launch.sh` 实现。

### 调用约定

```
SubprocessExecutor 调用方式:
    bash launch.sh <employee_dir>

参数:
    $1 = employee_dir  (如 company/human_resource/employees/00010/)

环境变量（自动注入）:
    OMC_EMPLOYEE_ID      — 员工 ID
    OMC_TASK_ID          — 任务 ID
    OMC_PROJECT_ID       — 项目 ID
    OMC_PROJECT_DIR      — 项目工作目录（cwd）
    OMC_TASK_DESCRIPTION — 完整任务描述
    OMC_SERVER_URL       — 后端 URL (http://localhost:8000)
    OMC_MAX_ITERATIONS   — 最大 agent 迭代次数（默认 20）

输出:
    stdout → JSON（唯一一行）
    stderr → 日志（仅供调试）
    exit 0 → 成功    exit 非零 → 失败
```

### stdout JSON 格式

```json
{
  "output": "任务结果文本",
  "model": "google/gemini-3.1-pro-preview",
  "input_tokens": 1234,
  "output_tokens": 567
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `output` | string | 是 | 任务执行结果（纯文本） |
| `model` | string | 否 | 使用的 LLM 模型标识符 |
| `input_tokens` | int | 否 | 输入 token 数量 |
| `output_tokens` | int | 否 | 输出 token 数量 |

如果 stdout 不是合法 JSON，`SubprocessExecutor` 会将原始文本作为 `output` 返回。

### 超时与取消

- 默认超时 3600s（1 小时），可由父任务通过 `dispatch_child(timeout_seconds=...)` 调整
- 超时或手动取消时，平台向进程发送 **SIGTERM**
- 30 秒内未退出则强制 **SIGKILL**
- 脚本应使用 `trap cleanup EXIT` 响应 SIGTERM，清理子进程

```bash
cleanup() {
    # 清理 MCP server 等子进程
    if [ -n "${MCP_PID:-}" ] && kill -0 "$MCP_PID" 2>/dev/null; then
        kill "$MCP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
```

### 使用 MCP 工具

Company-hosted 员工可通过 MCP stdio 协议访问公司工具（`dispatch_child`、`accept_child`、
`reject_child`、`list_colleagues` 等）。

```bash
# 启动 MCP server 为 coprocess
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

coproc MCP_PROC {
    exec "$PYTHON" -m onemancompany.tools.mcp.server 2>/dev/null
}
MCP_PID=$MCP_PROC_PID

# MCP_PROC[0] = stdout fd (读)
# MCP_PROC[1] = stdin fd (写)
# 通过 JSON-RPC 2.0 协议与 MCP server 交互
```

MCP server 会根据员工权限（`tool_permissions`）过滤可用工具。

### 完整示例

```bash
#!/usr/bin/env bash
set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

cleanup() { :; }
trap cleanup EXIT

>&2 echo "[launch.sh] Employee=${OMC_EMPLOYEE_ID} Task=${OMC_TASK_ID}"

# 调用 LLM（以 OpenRouter 为例）
RESULT=$(curl -s https://openrouter.ai/api/v1/chat/completions \
    -H "Authorization: Bearer ${OPENROUTER_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
      \"model\": \"google/gemini-3.1-pro-preview\",
      \"messages\": [{\"role\": \"user\", \"content\": $(echo "$OMC_TASK_DESCRIPTION" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}]
    }")

# 解析响应
OUTPUT=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["choices"][0]["message"]["content"])')
MODEL=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("model",""))' 2>/dev/null || echo "")
IN_TOKENS=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("usage",{}).get("prompt_tokens",0))' 2>/dev/null || echo "0")
OUT_TOKENS=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("usage",{}).get("completion_tokens",0))' 2>/dev/null || echo "0")

# 输出 JSON（仅此一行到 stdout）
python3 -c "
import json, sys
print(json.dumps({
    'output': sys.argv[1],
    'model': sys.argv[2],
    'input_tokens': int(sys.argv[3]),
    'output_tokens': int(sys.argv[4]),
}))
" "$OUTPUT" "$MODEL" "$IN_TOKENS" "$OUT_TOKENS"
```

### 最佳实践

1. **所有日志写 stderr** — stdout 仅用于最终 JSON 输出，`>&2 echo "..."` 记录日志
2. **响应 SIGTERM** — 使用 `trap cleanup EXIT` 清理子进程和临时文件
3. **不要后台化** — 脚本必须前台运行到完成，不要使用 `nohup` 或 `&`
4. **使用 python3 做 JSON** — 避免 shell 字符串拼接导致的 JSON 格式错误
5. **set -euo pipefail** — 任何命令失败立即退出，避免静默错误
6. **环境变量即上下文** — 不要硬编码任务信息，从 `OMC_*` 环境变量读取

---

## Creating a New Talent

### 最小可用包（Company-Hosted）

```
talents/my_talent/
├── profile.yaml      # 必须
├── launch.sh         # 推荐 — 前台任务执行脚本 / Ralph-style agent loop
└── skills/
    └── my-skill/
        └── SKILL.md  # 文件夹制技能
```

```yaml
# profile.yaml
id: my_talent
name: My Talent
description: A new talent for the company.
role: Engineer
remote: false
hosting: company
auth_method: api_key
api_provider: openrouter
llm_model: ''
temperature: 0.7
hiring_fee: 0.50
skills:
  - my-skill
personality_tags:
  - efficient
system_prompt_template: >
  You are a skilled engineer. Complete tasks efficiently.
```

### 完整包（Self-Hosted with manifest）

```
talents/my_self_hosted/
├── profile.yaml
├── manifest.json
├── launch.sh
├── skills/
│   ├── coding/
│   │   └── SKILL.md
│   └── review/
│       └── SKILL.md
└── tools/
    ├── manifest.yaml
    └── my_custom_tool.py
```

### Steps

1. 在 `talents/` 下创建目录
2. 编写 `profile.yaml`（必填字段：`id`, `name`, `role`, `skills`）
3. 编写 `system_prompt_template`——聚焦能力定位和行为指引，不重复 identity 信息
4. 在 `skills/` 下创建文件夹制技能（`{name}/SKILL.md`，含 YAML frontmatter）
5. 编写 `launch.sh`——参考 `company/assets/tools/launch_template.sh` 模板
   - Company-hosted：前台模式，从 `OMC_*` 环境变量获取任务，JSON 输出到 stdout
   - Self-hosted：后台模式，nohup 启动 worker，写 PID 文件
6. 如需自定义工具，创建 `tools/manifest.yaml` + `.py` 文件
7. 如需自定义设置 UI，创建 `manifest.json`
8. 如有 Claude CLI 项目指令，放入 `CLAUDE.md`

> **必须**：所有技能使用文件夹制（`skills/{name}/SKILL.md`），不再支持散文件格式。
> 入职时会自动注入三个默认技能（ontology, proactive-agent, self-improving-agent）。

---

## Standalone Execution（独立启动）

Company-hosted talent 支持**脱离公司系统独立运行**。入职时 `onboarding.py` 会在员工目录
生成一个自包含的 `run.py`（由 `standalone_runner.py` 模板生成），包含完整的 LangChain
ReAct agent、技能加载和内置工具。

### 前置条件

```bash
pip install langchain-openai langgraph pyyaml
# 如果使用 Anthropic:
pip install langchain-anthropic
```

### 独立启动步骤

```bash
# 1. 在 talent 目录下生成 run.py（一次性，或入职时自动生成）
cd talents/general-assistant
python -c "
from onemancompany.talent_market.standalone_runner import generate_run_py
generate_run_py('.', 'General Assistant', 'standalone')
"

# 2. 配置 API Key（二选一）
#    方式 A：环境变量
export OPENROUTER_API_KEY=sk-or-v1-xxx
#    方式 B：写入 profile.yaml 的 api_key 字段

# 3. 配置模型（在 profile.yaml 中设置 llm_model）
#    如 anthropic/claude-sonnet-4, google/gemini-2.5-pro 等
```

### 三种运行模式

| 模式 | 命令 | 场景 |
|------|------|------|
| **单次任务** | `python run.py "分析项目结构"` | 一次性问答或操作 |
| **管道输入** | `echo "写个方案" \| python run.py` | 脚本/CI 集成 |
| **Agent Loop** | `./launch.sh [max_iterations]` | 复杂多步任务，自动迭代直到完成 |

### Agent Loop 模式（Ralph 风格）

`launch.sh` 采用 [Ralph](https://github.com/snarktank/ralph) 风格的迭代执行模式：

```
┌─────────────────────────────────────────┐
│  读取 task.txt 或 TASK 环境变量          │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  Iteration N: 调用 run.py               │
│  - 注入上轮 progress.log 作为上下文      │
│  - 输出追加到 progress.log               │
└──────────────┬──────────────────────────┘
               ▼
         ┌───────────┐
         │ 输出包含    │──── 是 ──→ 退出（成功）
         │ <done>     │
         │ COMPLETE   │
         │ </done>?   │
         └─────┬─────┘
               │ 否
               ▼
         ┌───────────┐
         │ 达到最大   │──── 是 ──→ 退出（未完成）
         │ 迭代次数？ │
         └─────┬─────┘
               │ 否
               └──→ 下一轮迭代
```

```bash
# 示例：用 agent loop 执行复杂任务
echo "重构这个项目的测试框架，要求覆盖率达到80%" > task.txt
OPENROUTER_API_KEY=sk-or-v1-xxx ./launch.sh 20
```

### run.py 内置工具

独立运行时，agent 拥有以下内置工具（无需公司后端）：

| 工具 | 说明 |
|------|------|
| `read_file(path)` | 读取文件内容（上限 50KB） |
| `write_file(path, content)` | 写入/创建文件 |
| `list_dir(path)` | 列出目录内容 |
| `bash(command)` | 执行 Shell 命令（120s 超时） |
| `load_skill(name)` | 按需加载技能全文 |

### 自包含要求

> **开发规范**：每个 talent 必须是自包含的。独立启动所需的所有文件依赖
> （profile.yaml、skills/、tools/ 等）都必须在 talent 自己的目录下。
> 不得依赖 talent 目录外的文件。`run.py` 生成后可以在任意机器上运行，
> 只需安装 Python 依赖和配置 API Key。

### 开发者检查清单

新建 talent 时，请确认以下独立启动支持：

- [ ] `profile.yaml` 包含完整的 `api_provider` 和 `llm_model` 配置
- [ ] 技能使用文件夹制（`skills/{name}/SKILL.md`），含 YAML frontmatter
- [ ] `tools/manifest.yaml` 声明所需的内置工具
- [ ] `launch.sh` 支持 Ralph-style agent loop（可参考 `general-assistant/launch.sh`）
- [ ] `generate_run_py()` 能正确生成可运行的 `run.py`
- [ ] 所有依赖文件在 talent 目录内，无外部路径引用

---

## Extending `RemoteWorkerBase`

`remote_worker_base.py` 提供远程 worker 的抽象基类：

```python
from onemancompany.talent_market.remote_worker_base import RemoteWorkerBase
from onemancompany.talent_market.remote_protocol import TaskAssignment, TaskResult


class MyCodingWorker(RemoteWorkerBase):
    def setup_tools(self) -> list:
        return [my_sandbox_tool, my_web_search_tool]

    async def process_task(self, task: TaskAssignment) -> TaskResult:
        return TaskResult(
            task_id=task.task_id,
            employee_id=self.employee_id,
            status="completed",
            output="Task done!",
        )


import asyncio
worker = MyCodingWorker(
    company_url="http://localhost:8000",
    employee_id="00010",
    capabilities=["coding", "web_research"],
)
asyncio.run(worker.start())
```

基类自动处理注册、任务轮询和心跳循环，只需实现 `setup_tools()` 和 `process_task()`。
