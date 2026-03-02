# 代码工程师（Engineer）岗位画像（OMC）

## 1) 岗位定位
- **部门**：技术研发部
- **职级/职称**：**Lv.1 初级工程师**（公司制度：新入职统一 Lv.1）
- **角色本质**：在极精简团队中独立交付“可上线的代码”，覆盖后端/Agent 工作流/工程化。

## 2) 目标与产出（6–12 周）
- 交付 1–2 个可上线的核心功能（API/Agent workflow），具备：日志、监控点位、测试、文档。
- 将一个业务流程 Agent 化：工具调用、RAG（如需要）、评估（eval）与回归测试。
- 建立/完善工程基建：CI、代码规范、测试策略、发布脚本或容器化。

## 3) 核心职责
- 设计与实现后端服务/API（Python + FastAPI 为主）。
- 实现 LLM/Agent 能力：prompt、function calling、RAG、评测与迭代。
- 端到端交付：需求澄清 → 方案 → 开发 → 测试 → 部署 → 监控 → 复盘。
- 性能与质量：异步、缓存、数据库建模、测试覆盖、代码评审。

## 4) 核心技能栈（Must-have）
- **Python 工程能力**：类型标注、异步（asyncio）、结构化项目、依赖管理（poetry/pip-tools）。
- **Web 后端**：FastAPI/Flask、REST/（加分：WebSocket）、鉴权与权限。
- **数据层**：PostgreSQL/MySQL 其一 + ORM（SQLAlchemy 等），基本索引与查询优化。
- **LLM 应用**：至少熟悉一种生态（OpenAI/Anthropic/Qwen 等）、prompt 结构化、工具调用；理解 embedding 与向量检索的基本原理。
- **工程化**：Git、Linux、Docker；基础 CI（GitHub Actions/GitLab CI）。

## 5) 加分项（Nice-to-have）
- TypeScript/Node 或简单前端联调能力。
- 观测性：Sentry/Prometheus/Grafana、结构化日志。
- 基础安全：密钥管理、RBAC、输入校验、依赖漏洞意识。
- DevOps：K8s/Terraform/Helm（非必需）。

## 6) 软技能与文化契合
- 强 Ownership：能把问题闭环，而非只写“局部代码”。
- 快节奏与不确定性适应：敢做取舍，能用数据/实验验证。
- 沟通清晰：会写设计说明与复盘。

## 7) 胜任力雷区（淘汰信号）
- 只会“写代码片段”，无法负责上线与稳定性。
- 缺乏测试/质量意识，或无法解释基本工程实践。
- 对 LLM 只停留在“会用 Chat”，无法做结构化、可评测的迭代。
