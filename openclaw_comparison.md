# OpenClaw vs OneManCompany 员工工作方式对比

## 1. 工具能力

| 能力 | OpenClaw | 我们的员工 |
|---|---|---|
| **读文件** | `read` — 直接读 workspace 文件 | `read_file` — 受权限限制，需 gated |
| **写文件** | `write` — 直接写 | `save_to_project` — 只能写到项目目录 |
| **编辑文件** | `edit` — 直接编辑 | `propose_file_edit` — 需要 CEO 审批 |
| **Bash 执行** | `system.run` — 有权限门控 | **没有** |
| **浏览器** | 专用 Chrome 实例，可截图/操作 | **没有**（sandbox 有限的代码执行） |
| **跨 agent 通信** | `sessions_send/list/history` | `pull_meeting` + `dispatch_task` |
| **自动化** | cron + webhook + Gmail Pub/Sub | routine 系统（workflow_engine） |
| **设备集成** | 摄像头、录屏、定位、通知 | **没有** |

## 2. Memory / 工作文件系统

### OpenClaw
- `~/.openclaw/workspace/` — 持久化工作目录
- **注入式 prompt 文件**: `AGENTS.md`, `SOUL.md`, `TOOLS.md` — agent 自动加载的指令文件
- **Skills 目录**: `workspace/skills/<skill>/SKILL.md` — 可扩展技能
- agent 可以自由在 workspace 里创建/修改 md 文件作为工作记录

### 我们的员工
- `progress.log` — 跨任务上下文（追加式日志，不是结构化 memory）
- `sessions.json` — Claude CLI session resume（保持对话历史）
- 项目文件只能存到 `project_dir/`
- **没有员工私有的 workspace/scratchpad**
- **没有 AGENTS.md / SOUL.md 这样的自维护知识文件**

## 3. 核心差距

1. **没有员工自己的持久化工作空间** — OpenClaw 的 agent 有 `~/.openclaw/workspace/`，可以自由读写 md 文件积累知识。我们的员工只有 `progress.log` 和被管控的项目目录。

2. **没有 Bash** — OpenClaw 有 `system.run`，我们完全没有 shell 能力（sandbox 除外）。

3. **没有自维护的知识文件** — OpenClaw 有 `SOUL.md`、`TOOLS.md` 等 agent 会主动读取和更新的文件。我们的 `work_principles.md` 和 `guidance.yaml` 是系统写入的，员工自己不能修改。

4. **编辑需审批 vs 直接写** — 我们的 `propose_file_edit` 是管控模式，OpenClaw 是直接操作。

## 4. Agent 通信机制对比

| 维度 | OpenClaw | 我们 |
| --- | --- | --- |
| 点对点消息 | `sessions_send` | meeting chat |
| 多人讨论 | **没有** | `pull_meeting` 多轮会议 |
| 任务分发 | **没有**（只能发消息） | `dispatch_task` 结构化队列 |
| 任务分解 | **没有** | `create_subtask` 子任务树 |
| 汇报机制 | **没有** | `report_to_ceo` |
| 团队协作 | **没有** | `dispatch_team_tasks` 分期调度 |
| 同事感知 | `sessions_list` 只看 session | `list_colleagues` 看角色/技能/状态 |

**结论: 我们在通信和协作机制上远强于 OpenClaw。** OpenClaw 本质是 personal assistant，agent 间通信只是消息传递。我们是围绕公司组织架构设计的，有会议、任务、汇报、层级。

## 5. 执行效率对比

| 维度 | OpenClaw | 我们（改造前） | 我们（改造后） |
| --- | --- | --- | --- |
| 工具调用延迟 | 本地调用，极低 | MCP→HTTP→后端，**高** | 同左（MCP 架构不变） |
| 冷启动 | 无（Gateway 常驻） | 每次任务重启 CLI，**慢** | **无**（ClaudeDaemon 常驻） |
| 并发 | 多 session 并行 | 同员工串行锁 | 同左（保持串行安全） |
| 资源占用 | 轻量 | 每任务启停 2 个子进程 | 常驻进程，无启停开销 |
| 上下文保持 | 内存中持续 | session resume（磁盘序列化） | **内存中持续** |

**已完成改造:** `claude_session.py` 从每次任务启动/退出 `claude --print` 改为 `ClaudeDaemon` 常驻进程模式（`--input-format stream-json --output-format stream-json`）。进程只启动一次，后续任务通过 stdin 发送 prompt，从 stdout 读取 NDJSON 响应。进程挂了自动以 `--resume` 重启。

## 6. 改造计划（整体策略：对齐 OpenClaw 的逻辑）

> **重要区分:** 以下改造针对 **company-hosted 员工（LangChain vessel）**。
> Self-hosted 员工（Claude CLI）已经有内置的 Read/Edit/Write/Bash，不需要我们提供这些工具。
> Self-hosted 的改进是 Step 0 的 ClaudeDaemon 常驻进程。

### Step 0: ClaudeDaemon 常驻进程（self-hosted 专属）✅ 已完成

- `claude_session.py` 改为 `ClaudeDaemon` 常驻模式
- `--input-format stream-json --output-format stream-json`
- 进程死了自动 `--resume` 重启
- `main.py` shutdown 时 `stop_all_daemons()`

### Step 1: 员工私有 Workspace（company-hosted）✅ 已完成

**目标:** 每个 company-hosted 员工在 `employees/{id}/workspace/` 下有自己的持久化工作空间

**改动文件:**

- `core/config.py` — 添加 `get_workspace_dir(employee_id)` helper
- `agents/onboarding.py` — 员工 onboarding 时自动创建 workspace 目录
- `agents/common_tools.py` — workspace 相关工具函数

**具体内容:**

- `employees/{id}/workspace/` — 员工私有目录，可自由读写
- 员工 onboarding 时自动创建 workspace 目录

### Step 2: 工具梳理 + Bash + 审批逻辑（company-hosted）✅ 已完成

**目标:** company-hosted 员工的 LangChain 工具名称简化对齐 OpenClaw，添加 bash 能力，实现分区审批

**工具重命名:**

| 旧名称 | 新名称 | 说明 |
| --- | --- | --- |
| `read_file` | `read` | 读文件 |
| `list_directory` | `ls` | 列目录 |
| `propose_file_edit` | `edit` | 编辑文件（分区审批） |
| `save_to_project` | `write` | 写文件（分区审批） |
| `list_project_workspace` | 合并到 `ls` | 不再单独存在 |
| *新增* | `bash` | 执行 shell 命令（gated） |

**分区审批逻辑（`edit` 和 `write`）:**

- 目标路径在 `employees/{id}/workspace/` 内 → **直接执行，不需要审批**
- 目标路径在当前任务的 `project_dir/` 内 → **直接执行，不需要审批**
- 目标路径在其他位置（company/, src/ 等） → **创建 resolution，CEO 审批后执行**

**改动文件:**

- `agents/common_tools.py` — 重写工具函数，新增 bash，实现分区审批
- `core/resolutions.py` — 可能需要适配新的审批类型

**注意:** MCP server (`mcp/server.py`) 是给 self-hosted 员工用的代理层，不在此次改造范围。
Self-hosted 员工通过 Claude CLI 内置工具直接操作文件系统，不走我们的 LangChain 工具。

### Step 3: 自维护知识文件（company-hosted）✅ 已完成

**目标:** company-hosted 员工有 `SOUL.md` 等自维护知识文件，任务结束后自动更新

**文件结构:**

- `employees/{id}/workspace/SOUL.md` — 员工自我认知、经验总结、工作偏好
- `employees/{id}/workspace/NOTES.md` — 自由笔记/scratchpad

**改动文件:**

- `agents/prompt_builder.py` — 构建 prompt 时注入 SOUL.md 内容
- `core/vessel.py` — `_full_cleanup()` 中引导员工更新 SOUL.md
- `agents/onboarding.py` — 新员工入职时创建初始 SOUL.md

### Step 4: 自动化（cron / webhook / Gmail Pub/Sub）✅ 已完成（cron + webhook）

**目标:** 员工可以设置定时任务、监听 webhook、接收 Gmail 通知（company-hosted 和 self-hosted 通用）

**改动文件:**

- 新建 `core/automation.py` — cron 调度器 + webhook 路由
- `api/routes.py` — webhook 接收端点
- `agents/common_tools.py` — 新增 `set_cron`, `register_webhook` 工具

**具体内容:**

- cron: 基于 `asyncio` 的定时调度，配置存 `employees/{id}/crons.yaml`
- webhook: FastAPI 端点 `/api/webhook/{employee_id}/{hook_name}`，收到请求后 dispatch_task
- Gmail Pub/Sub: 监听 Gmail push notification，转为员工任务

### 实施顺序

Step 1 (workspace) → Step 2 (工具梳理) → Step 3 (知识文件) → Step 4 (自动化)

- Step 1 和 Step 2 可以一起做（Step 2 依赖 Step 1 的 workspace 路径判断）
- Step 3 依赖 Step 1 (workspace) + Step 2 (write 工具)
- Step 4 相对独立，可以最后做