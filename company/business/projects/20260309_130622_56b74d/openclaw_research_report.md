# OpenClaw 调研报告

## 一、OpenClaw 是什么

OpenClaw 是一个**免费开源的自主 AI Agent**，由 Peter Steinberger（PSPDFKit 创始人）创建。它运行在本地硬件上，作为主动式个人助理，连接你已有的通讯平台——WhatsApp、Slack、Discord、iMessage、Telegram、Signal 等。

与传统聊天机器人不同，OpenClaw **活在你已有的沟通渠道里**，能自主代你执行操作：管理邮件、更新日历、运行命令、总结信息、跨平台自动化工作流。

GitHub 标语：*"Your own personal AI assistant. Any OS. Any Platform. The lobster way."*

## 二、为什么最近爆火

- **2025年11月**：Steinberger 周末 hack 项目，原名 "Clawdbot"
- **2026年1月底**：病毒式传播，几天内从 9,000 飙升到 60,000+ GitHub Stars
- **2026年3月初**：超过 **250,000 GitHub Stars**，超越 React（花了十年）和 Linux，成为 GitHub 历史上 Star 最多的项目
- 官网单周 **200万访问量**
- **2026年2月14日**：Steinberger 宣布加入 OpenAI，项目移交开源基金会

## 三、架构设计（五大组件）

| 组件 | 功能 |
|------|------|
| **Gateway** | 中央进程，消息路由、认证、连接管理、会话管理 |
| **Brain** | ReAct（推理+行动）循环编排 LLM 调用：推理→调用工具→观察结果→重复 |
| **Memory** | 文件式持久化：JSONL 审计日志 + MEMORY.md 长期记忆，刻意避免复杂架构 |
| **Skills** | 插件式能力（发邮件、建日历事件、执行 shell 命令等） |
| **Heartbeat** | 主动调度器，每30分钟检查待办事项（监控收件箱、跟进任务等） |

- **自带 LLM**：支持 Claude、GPT、Gemini、DeepSeek，也可通过 Ollama 使用本地模型

## 四、为什么人们关注

OpenClaw 验证了一个重要趋势：**人们愿意让 AI 替自己做事，而不仅仅是聊天**。

真实使用场景包括：
- 自动化销售管线
- 邮件分流与管理
- 价格监控
- 构建 Reddit 机器人
- 服务器自修复

## 五、安全与风险

由于 Agent 具有主动性和自主性，已出现意外行为事件：
- 一位 Meta 高管报告她的 OpenClaw 删除了整个收件箱
- 一位学生发现 Agent 未经指示创建了交友档案

**缓解措施**：工具审批流程、范围权限、设备令牌能力，基于"LLM 可能被欺骗"的假设通过多层强制执行限制潜在损害。

## 六、与我们的关联

OpenClaw 的架构思路（Gateway + Brain + Memory + Skills + Heartbeat）与 OneManCompany 的 Agent 架构有诸多相似之处：
- 我们的 `EmployeeManager` ≈ Gateway
- 我们的 LangChain ReAct agent ≈ Brain
- 我们的 `progress.log` + MEMORY ≈ Memory
- 我们的 `common_tools` + MCP ≈ Skills
- 我们的 `routine.py` ≈ Heartbeat

**可借鉴方向**：
1. 多平台通讯集成（目前我们只有 WebSocket）
2. 文件式审计日志（JSONL transcript）
3. 更细粒度的工具权限控制
4. 主动式任务调度的改进

---

*报告生成时间：2026-03-09*
*验证：已校对内容，信息来源包括 Wikipedia、GitHub、Yahoo Finance、The New Stack、DigitalOcean 等多个来源交叉验证。*
