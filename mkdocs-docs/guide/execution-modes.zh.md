# 执行模式

创始员工（EA、HR、COO、CSO）支持两种执行模式。你可以在浏览器设置面板中切换。

## Company Hosted Agent

默认模式。OneManCompany 内置的 Agent 框架处理一切。

- **工作原理**：任务由 OMC 内部基于 LangChain 的 Agent 执行，通过 OpenRouter 调用 LLM
- **前置要求**：OpenRouter API Key（在配置时设定）
- **适用场景**：快速上手、控制成本、完全掌控模型选择
- **模型灵活性**：每位员工可以在其 profile 中分配不同的模型

## Claude Code

员工以 Claude Code CLI 会话的形式运行，利用 Anthropic 最强大的编码 Agent。

- **工作原理**：每个任务会启动一个 Claude Code CLI 会话，通过 MCP 工具连接回公司
- **前置要求**：[Claude Pro 或 Max 订阅](https://claude.ai)，并安装 Claude Code CLI
- **适用场景**：复杂编码任务、更强的自主推理能力、访问 Claude 完整的工具生态

!!! note "需要订阅"
    Claude Code 模式需要有效的 Claude Pro（$20/月）或 Max（$100/月）订阅。订阅通过你的 Anthropic 账户管理，与 OpenRouter 独立。

## 切换模式

1. 在浏览器中打开**设置**面板
2. 选择你要配置的员工
3. 在 **Company Hosted** 和 **Claude Code** 之间选择
4. 更改将在下一个任务时生效

## 对比

| | Company Hosted Agent | Claude Code |
| --- | --- | --- |
| **计费模式** | 通过 OpenRouter 按 token 付费 | 固定订阅费 |
| **配置** | 仅需一个 API key | Claude CLI + 订阅 |
| **模型选择** | OpenRouter 上的任意模型 | Claude（Anthropic） |
| **编码能力** | 良好（取决于模型） | 优秀 |
| **工具访问** | OMC 内置工具 | 完整的 Claude Code + MCP 工具 |
| **最佳用途** | 通用任务、预算控制 | 复杂开发工作 |

## 混合配置

你可以为不同员工配置不同模式。例如：

- **EA、HR、CSO** → Company Hosted（主要是沟通协调任务）
- **COO** → Claude Code（复杂的任务拆解和代码审查）

这样可以按角色优化成本和能力的平衡。
