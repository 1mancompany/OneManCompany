# Talent Market

The talent market provides structured **talent packages** for hiring and
multiple **connection modes** for running agents.

---

## Talent Package Structure

Each talent lives in `talents/{talent_id}/`:

```
talents/{talent_id}/
├── profile.yaml          # 必须 — 身份 + 招聘信息
├── manifest.json         # 可选 — 前端设置 UI + 能力声明
├── launch.sh             # 可选 — 自托管员工的启动脚本
├── run_worker.py         # 可选 — 远程员工的 worker 入口
├── skills/               # 可选 — 技能描述
│   └── *.md              # 每个文件描述一项技能，内容注入员工 prompt
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
| `skills` | list | 技能标识符列表，对应 `skills/` 下的 `.md` 文件名 |
| `tools` | list | 工具名列表 |
| `personality_tags` | list | 性格标签（HR 匹配用） |
| `system_prompt_template` | str | 系统 prompt 模板 |

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
  "prompts": {"skills": ["skills/*.md"]},
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

### `skills/*.md`（可选）

每个 `.md` 文件描述一项技能，入职时复制到员工目录，内容注入员工 prompt。

---

## Connection Modes（连接模式）

平台支持三种连接模式，由 `profile.yaml` 中的 `hosting` 字段决定。

### 1. Company-Hosted（`hosting: "company"`）

**公司托管** — 最常见的模式。平台内部运行 LangChain agent。

```yaml
# profile.yaml
hosting: company          # 或不填，默认值
auth_method: api_key      # 使用 API key 调用 LLM
api_provider: openrouter  # LLM 提供商
llm_model: google/gemini-3.1-pro-preview-customtools
```

**工作方式**：
- 平台使用 `LangChainLauncher` 在内部创建 `create_react_agent`
- 每次任务通过 `EmployeeManager.push_task()` 触发一次 agent 调用
- LLM API key 由公司统一管理（`api_key` 字段或环境变量）
- 员工工具由 `tools/manifest.yaml` 声明，运行时注入 agent

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
| **进程管理** | 平台内部 | 平台按需启动 CLI | 外部自行管理 |
| **Launcher** | `LangChainLauncher` | `ClaudeSessionLauncher` | 无（HTTP 任务队列） |
| **LLM 调用** | 平台通过 API key | CLI 自带凭证/OAuth | Worker 自行调用 |
| **认证方式** | api_key / none | oauth / cli | — |
| **skills/tools 复制** | 是 | 是 | 否 |
| **工位分配** | 是 | 是 | 否（远程标记） |
| **connection.json** | 否 | 是 | 是 |
| **launch.sh** | 否 | 可选 | 否 |
| **会话管理** | agent 内存 | sessions.json | worker 自行管理 |

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

## Creating a New Talent

### 最小可用包（Company-Hosted）

```
talents/my_talent/
├── profile.yaml      # 必须
└── skills/
    └── my_skill.md   # 至少一个技能
```

```yaml
# profile.yaml
id: my_talent
name: My Talent
description: A new talent for the company.
role: Engineer
remote: false
llm_model: google/gemini-3.1-pro-preview-customtools
temperature: 0.7
hiring_fee: 0.50
skills:
  - my_skill
tools:
  - sandbox_execute_code
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
│   ├── coding.md
│   └── review.md
└── tools/
    ├── manifest.yaml
    └── my_custom_tool.py
```

### Steps

1. 在 `talents/` 下创建目录
2. 编写 `profile.yaml`（必填字段：`id`, `name`, `role`, `skills`）
3. 在 `skills/` 下编写技能 `.md` 文件
4. 如需自定义工具，创建 `tools/manifest.yaml` + `.py` 文件
5. 如需自定义设置 UI，创建 `manifest.json`
6. 如为 self-hosted，创建 `launch.sh`（接收 `$1 = employee_dir`）

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
