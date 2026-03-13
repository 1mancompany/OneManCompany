# 架构

> 面向开发者和贡献者的技术参考。

## 技术栈

- **后端**：Python 3.12+ / UV，FastAPI + WebSocket，LangChain（`create_react_agent`）
- **LLM**：OpenRouter API（每个员工可独立配置），Anthropic API（OAuth / API Key）
- **前端**：原生 JS + Canvas 2D 像素画（零构建工具）
- **基础设施**：Docker 沙箱，MCP 服务器，Watchdog 热重载
- **数据**：YAML 档案 + Markdown 工作流 + JSON 项目归档（git-friendly，无数据库依赖）

## 架构概览

```mermaid
graph TB
    CEO["👤 CEO（人类）<br>浏览器 — CEO 控制台"]

    subgraph ExecFloor["C-Suite — 创始高管（Lv.4，永久）"]
        HR["HR<br>招聘 / 绩效 / 晋升"]
        COO["COO<br>运营 / 资产 / 验收"]
        EA["EA<br>CEO 质量把关"]
        CSO["CSO<br>销售 / 客户"]
    end

    subgraph Departments["部门 — 动态招聘的员工（Lv.1-3）"]
        Eng["工程部<br>工程师 / DevOps / QA"]
        Design["设计部<br>设计师"]
        Analytics["分析部<br>分析师"]
        Marketing["市场部<br>市场营销"]
        General["综合部<br>其他角色"]
    end

    subgraph TalentMkt["人才市场 — 插件商店"]
        TP["Talent 包<br>profile / skills / tools<br>functions / agent"]
        MCP["Boss Online<br>MCP 招聘服务"]
    end

    subgraph CompanyAssets["公司资产"]
        Tools["工具 & 设备<br>company/assets/tools/"]
        Rooms["会议室<br>company/assets/rooms/"]
        Knowledge["知识库<br>workflows / SOPs / culture"]
    end

    CEO -->|"下达指令 / 审批"| ExecFloor
    HR -->|"招聘"| TalentMkt
    HR -->|"入职 / 离职"| Departments
    COO -->|"分配 / 验收"| Departments
    COO -->|"注册 / 管理"| CompanyAssets
    EA -->|"审查质量"| COO
    CSO -->|"协调"| Departments
    Departments -->|"使用工具"| CompanyAssets
    TalentMkt -.->|"安装 talent"| Departments
```

## 系统分层

```mermaid
graph TB
    subgraph Presentation["表现层 — Frontend"]
        HTML["index.html<br>三栏布局"]
        OfficeJS["office.js<br>Canvas 2D 像素画"]
        AppJS["app.js<br>CEO 控制台 + WS 客户端"]
    end

    subgraph Gateway["网关层 — FastAPI"]
        REST["routes.py<br>/task /hire /fire /state"]
        WSS["websocket.py<br>/ws 实时推送"]
    end

    subgraph AgentLayer["Agent 层 — LangChain Agents"]
        Runners["Agent Runners<br>BaseAgentRunner / EmployeeAgent / Custom"]
        Founding["创始 Agent<br>HR / COO / EA / CSO"]
        Infra["Agent 基础设施<br>PromptBuilder / common_tools / onboarding"]
    end

    subgraph CoreEngine["核心引擎"]
        EM["EmployeeManager<br>按需任务调度"]
        EB["EventBus<br>异步 pub/sub"]
        CS["CompanyState<br>单例 + 热重载"]
        PA["ProjectArchive<br>生命周期 / 成本"]
    end

    subgraph VesselLayer["Vessel 执行层"]
        LC["LangChainExecutor<br>公司托管"]
        CL["ClaudeSessionExecutor<br>自托管（CLI）"]
        SL["ScriptExecutor<br>bash 脚本"]
    end

    subgraph Persistence["持久层 — company/"]
        EMP["employees/ — 档案、技能、agent"]
        AST["assets/ — 工具、会议室"]
        BIZ["business/ — 工作流、项目"]
    end

    AppJS -->|"HTTP + WS"| Gateway
    REST --> EM
    WSS --> EB
    EM -->|"调度"| VesselLayer
    EM --> EB
    EB --> WSS
    VesselLayer --> Runners
    Founding --> Runners
    Runners --> Infra
    CoreEngine --> Persistence
```

## 运转模式

### 模式 A：CEO 驱动 — 内部经营

```mermaid
sequenceDiagram
    actor CEO
    participant FE as Frontend
    participant API as routes.py
    participant EM as EmployeeManager
    participant Officer as COO / HR
    participant Worker as Employee Agent
    participant EA as EA Agent

    CEO->>FE: 输入任务（CEO 控制台）
    FE->>API: POST /task
    API->>EM: push_task(officer_id, task)
    EM->>Officer: 通过 Executor 执行
    Officer->>EM: dispatch_task(worker, 子任务)
    EM->>Worker: 通过 Executor 执行
    Worker->>EM: 任务完成
    EM->>Officer: 推送验收任务
    Officer->>Officer: 逐条验证验收标准
    EM->>EA: 推送 EA 复核
    EA->>EA: 最终质量检查
    EA-->>CEO: 通知完成
```

### 模式 B：互联网任务单 — 对外接单（规划中）

外部客户通过 Sales API 提交任务 → CSO 评估 → 内部团队交付。公司作为服务商运转。

## 模块索引

| 层级 | 模块 | 职责 |
|------|------|------|
| **入口** | `main.py` | FastAPI 应用，生命周期 |
| **API** | `routes.py` | REST 端点 |
| **API** | `websocket.py` | WS 实时推送 |
| **Agents** | `base.py` | `BaseAgentRunner`、`EmployeeAgent` |
| **Agents** | `hr_agent.py` | 招聘、绩效、晋升 |
| **Agents** | `coo_agent.py` | 运营、资产、验收 |
| **Agents** | `ea_agent.py` | CEO 质量把关 |
| **Agents** | `cso_agent.py` | 销售管线 |
| **Agents** | `common_tools.py` | 共享工具（dispatch、meeting、文件操作） |
| **Agents** | `prompt_builder.py` | 可组合提示词系统 |
| **Agents** | `onboarding.py` | 入职流程 + talent 安装 |
| **Agents** | `termination.py` | 离职流程 + 清理 |
| **Core** | `config.py` | 路径、常量、配置加载器 |
| **Core** | `state.py` | `CompanyState` 单例、热重载 |
| **Core** | `events.py` | 异步 `EventBus` pub/sub |
| **Core** | `vessel.py` | `Vessel`、`EmployeeManager`、`Executor` 协议 |
| **Core** | `vessel_config.py` | `VesselConfig`（DNA）加载/保存/迁移 |
| **Core** | `vessel_harness.py` | 6 类 Harness 协议 |
| **Core** | `routine.py` | 任务后工作流调度 |
| **Core** | `workflow_engine.py` | Markdown → `WorkflowDefinition` |
| **Core** | `project_archive.py` | 项目 CRUD、成本追踪 |
| **Core** | `layout.py` | 办公室网格分配 |
| **Talent** | `talent_spec.py` | `TalentPackage`、`AgentManifest` |
| **Talent** | `boss_online.py` | MCP 招聘服务 |
| **Infra** | `tools/sandbox/` | Docker 代码执行 |
| **Infra** | `claude_session.py` | Claude CLI 会话管理 |
| **Frontend** | `index.html` | 三栏布局 |
| **Frontend** | `office.js` | Canvas 2D 像素画渲染 |
| **Frontend** | `app.js` | CEO 控制台、WebSocket 处理 |

## 设计哲学

1. **系统性设计，不打补丁** — 每个变更都是结构性的，不写 `if id == "特殊情况"`
2. **Registry/Dispatch 优于 if-elif** — 数据驱动的模式
3. **完整数据包** — 每个状态都可序列化、可恢复、有注册、有终结
4. **禁止静默异常** — 必须 log，必须 re-raise `CancelledError`
5. **磁盘即唯一真相源** — 业务数据不做内存缓存
6. **零空转** — 没有 `while True` 轮询，事件驱动，按需执行
7. **Git-Friendly 持久化** — YAML + Markdown + JSON，可 `git diff`、`git blame`、`git revert`
8. **最小复杂度** — 三行重复代码好过一个过早的抽象
