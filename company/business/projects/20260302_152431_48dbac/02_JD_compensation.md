# Job Description — Code Engineer / Software Engineer (AI Agent Platform)

> 公司：One Man Company (OMC)

## 0) 版本与变更记录
- 当前版本：v0.2
- 最近更新：纳入「技术栈&级别 / 面试官安排与题纲 / 30-60-90 交付物 / 入职设备&账号&权限清单与准备周期 / 周报里程碑跟踪」框架
- 说明：若 CEO 确认路线/技术栈变更，以 CEO 最新确认版本为准，并在本节补充变更记录。

---

## 中文 JD

### 1) 岗位名称
代码工程师 / 软件工程师（AI Agent 方向）

### 2) 职级与部门
- **职级**：Lv.1（初级工程师；但按 OMC 模式期望可独立交付）
- **部门**：技术研发部

### 3) 路线与技术栈（对齐口径）
> 口径：以「可上线交付」为主，后端 + Agent 工作流 + 工程化。

- 后端：**Python 3.10+**，**FastAPI**，Pydantic
- 数据：PostgreSQL/MySQL（其一），SQLAlchemy（或同级 ORM）
- LLM/Agent：结构化 prompt、tool/function calling；必要时 RAG（embedding + 向量检索，如 pgvector/Milvus/FAISS）
- 工程化：Git、Linux、Docker、CI（GitHub Actions/GitLab CI）

> 【待 CEO/用人方最终确认】如路线/栈调整（例如语言、框架、是否强制 RAG、是否需要前端/TypeScript 等），请在此处更新并同步 HR 做 JD 与题库微调。

### 4) 你将负责
- 基于 **Python + FastAPI** 开发内部产品后端服务与 API。
- 将业务能力实现为可复用的 **LLM/Agent 工作流**：prompt 结构化、工具调用（function calling）、必要时做 RAG。
- 端到端交付：需求澄清、方案设计、开发测试、上线发布、监控与故障定位。
- 提升工程质量：测试、CI、重构、文档、代码评审。

### 5) 我们希望你具备
- 扎实的 Python 工程能力：类型标注、异步、模块化、依赖管理。
- Web 后端经验：FastAPI/Flask，熟悉 REST API 设计。
- 熟悉 Git / Linux / Docker，有 CI/CD 基本概念。
- 对 LLM 应用落地有实践：能把“对话能力”做成“稳定、可评测、可迭代”的系统。
- 高度自驱与主人翁意识，能在不确定需求下推进落地。

### 6) 加分项
- TypeScript/Node 或前端联调经验。
- 向量数据库/检索（FAISS、Milvus、pgvector 等）经验。
- 观测性（Sentry、Prometheus/Grafana）或安全（RBAC/Secret 管理）经验。

### 7) 面试流程（对外口径）
- HR 初筛（15–30min）
- 技术初筛（30min）
- 作业/笔试（2–3h 时间盒）
- 技术面（60–90min：代码走查 + 系统设计/Debug）
- 文化/价值观面（30–45min）
- CEO 终面（30min）

> 详细题纲见：`04_interview_process_question_bank.md`

### 8) 面试官安排（内部执行）
> 面试官与分工以 CEO/COO 最终确认排期为准；本节用于候选人沟通“我们会由哪些角色参加”。

- HR 初筛：Sam HR（00002）
- 技术面试官（主面）：Alex COO（00003）
- 文化/价值观面：Sam HR（00002） +（必要时）Alex COO（00003）
- 终面：CEO

### 9) 试用期 30/60/90 天交付物（对外承诺 + 内部验收）
> 原则：目标清晰、可验收、与 OMC 模式匹配（独立交付/工程化/文档与自测）。

- **30 天（Onboarding + 第一个可运行闭环）**
  - 完成环境与权限配置；熟悉现有代码结构与发布流程
  - 交付 1 个可运行的最小功能闭环（含：基础测试、日志、README/使用说明）
  - 输出《系统现状与改进清单》（Top 5 风险/债务 + 2 周内可落地优化）

- **60 天（可上线功能 + 工程化补齐）**
  - 交付 1 个可上线功能（API/Agent workflow），具备监控点位/错误处理/回归用例
  - 补齐工程化一项（如：CI、测试策略、Lint/Format、发布脚本、容器化）
  - 产出一次复盘（质量/稳定性/效率数据）

- **90 天（稳定交付节奏 + 可迭代体系）**
  - 交付第 2 个核心能力或完成一个业务流程 Agent 化（含评测与回归）
  - 建立/完善评测（eval）与回归机制（含用例与指标）
  - 形成可复用的开发规范与文档（面向后续扩招/协作）

> 【待 CEO/用人方最终确认】若你已整理更具体的 30/60/90 交付物，请贴到项目目录或同步 HR，我将以“唯一口径”更新此节。

### 10) 入职设备/账号/权限清单与准备周期（候选人预期管理）
- **T-7 ~ T-3 天（建议）**：启动设备与账号申请（尽量在候选人进入终面后开始准备）
- 设备：工作电脑（如需）、基础网络/远程会议设备
- 账号：公司邮箱/IM（如有）、代码仓库（Git）、CI、日志/监控平台、文档权限
- 权限：项目仓库读写、部署/环境变量（按最小权限原则）、数据库/向量库（如有）

> 详细清单与责任人/周期请见：`07_candidate_communication_materials.md`（Pre-boarding 部分）。

---

## English JD

### Title
Software Engineer (AI Agent Platform)

### Level & Team
- **Level**: Lv.1 (Junior Engineer; expected to work independently in OMC model)
- **Department**: Engineering

### Responsibilities
- Build backend services/APIs with **Python + FastAPI**.
- Implement **LLM/agent workflows** (structured prompting, tool/function calling, optional RAG).
- Own end-to-end delivery: design → implement → test → deploy → monitor.
- Improve engineering excellence: testing, CI, refactoring, docs.

### Requirements
- Strong Python engineering fundamentals (typing, async, packaging).
- Backend API experience (FastAPI/Flask), solid REST design.
- Git/Linux/Docker; familiarity with CI/CD.
- Hands-on experience shipping LLM features into production-like systems.
- Ownership, speed, and clear communication.

### Nice to have
- TypeScript/Node, vector DB/pgvector, observability, security basics.

---

## 3) 薪酬区间建议（给 CEO 参考）
> 说明：未限定地区与用工形式，以下给两套可选方案；最终以候选人资历与市场为准。

### A. 国内（全职）建议
- **月薪**：25k–45k RMB（税前）
- **年薪**：30–55 万 RMB（含 12–14 薪常见）
- **绩效/期权**：建议提供期权/奖金作为吸引高自驱候选人的杠杆。

### B. 海外/远程（合同制）建议
- **时薪**：USD 40–80 / hour（按经验浮动）
- **月预算**：USD 6k–12k（兼职/全职折算）

### 定档规则（面试后快速定薪）
- 重点看：独立交付能力（上线/稳定性/测试） > LLM 实战深度 > 沟通与 ownership。
