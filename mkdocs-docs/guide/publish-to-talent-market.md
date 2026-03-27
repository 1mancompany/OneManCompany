# 如何把你的 Agent 变成 AI 员工，上架 Talent Market

> 你花了几周时间打磨了一个 AI Agent，功能完善、跑得稳，但只有你一个人在用。  
> Talent Market 解决的就是这个问题——让你的 Agent 成为别人公司里的正式员工，24 小时帮人干活。

---

## 上架前先搞清楚：Talent 是什么

在 OneManCompany 的体系里，一个 AI 员工 = **Vessel（容器）+ Talent（能力包）**。

- **Vessel** 是平台提供的执行容器，负责调度、重试、通信——你不需要管它。
- **Talent** 是你要打包的部分：Agent 的身份、系统提示词、技能列表、工具配置。

你只需要把你的 Agent 整理成 Talent 格式，提交到 Talent Market，平台自动完成剩下的事。

---

## 第一步：从模板开始

不要从零写，直接 fork 官方模板：

```bash
# 方式一：在 GitHub 上点 "Use this template"
https://github.com/1mancompany/talent-template

# 方式二：克隆到本地
git clone https://github.com/1mancompany/talent-template.git my-talent-repo
```

> ⚠️ **重要**：每个 Talent 都要放在独立的 repo 里，不要往模板 repo 里直接提交。

---

## 第二步：整理目录结构

一个 Talent repo 的基本结构长这样：

```
my-talent/
├── profile.yaml        # 必填 — Agent 的身份证
├── DESCRIPTION.md      # 推荐 — 详细介绍、Demo、成功案例
├── avatar.jpg          # 推荐 — 头像（png/jpg/svg/webp）
├── skills/
│   └── core/
│       └── SKILL.md   # 技能描述
└── tools/
    ├── .mcp.json       # MCP 工具配置
    └── your-tool/
        └── TOOL.md     # 工具说明
```

如果你想在一个 repo 里放多个 Talent（比如一个设计师 + 一个工程师），就在根目录下建多个子目录，每个子目录放一个 `profile.yaml`。

---

## 第三步：填写 profile.yaml

这是整个 Talent 的核心文件，相当于员工档案。

```yaml
id: my-react-engineer          # 全平台唯一 ID，小写+连字符
name: React Engineer           # 显示名称
avatar: avatar.jpg             # 头像文件名

description: >
  专注于 React 前端开发的工程师，擅长组件设计、性能优化和 TypeScript。
  可独立完成从需求分析到代码实现的完整交付。

role: Engineer                 # Engineer / Designer / Manager / Researcher / Analyst / Assistant

personality_tags:
  - autonomous                 # 工作风格标签，显示在卡片上
  - thorough
  - creative

system_prompt_template: >
  You are a senior React engineer. You write clean, well-typed TypeScript code.
  You always break down tasks before starting, write tests for critical logic,
  and proactively flag potential issues to the team.
  （把你 Agent 的完整系统提示词放这里）

# 托管方式
hosting: company               # company = 平台托管 | self = 自托管
auth_method: api_key           # api_key | cli | oauth
api_provider: openrouter       # openrouter | anthropic | custom

# 模型配置（留空则用平台默认）
llm_model: ""
temperature: 0.7

# 技能列表（对应 skills/ 目录下的文件夹名）
skills:
  - core
  - code-review

# 定价（0.0 = 免费）
hiring_fee: 0.0
salary_per_1m_tokens: 0.0

# Agent 框架类型
agent_family: ""               # claude | openclaw | omctalent | 留空
```

**关于 `agent_family` 怎么填：**

| 你的 Agent 类型 | 填什么 |
|---|---|
| Claude Code Agent（CLAUDE.md 驱动） | `claude` |
| OpenClaw Agent | `openclaw` |
| LangChain / CrewAI / AutoGen 等 | 留空或填框架名 |
| 从头写的自定义 Agent | 留空 |

---

## 第四步：定义技能（Skills）

每个技能是 `skills/` 目录下的一个文件夹，里面放一个 `SKILL.md`。

```
skills/
├── core/
│   └── SKILL.md
└── code-review/
    └── SKILL.md
```

`SKILL.md` 的格式：

```markdown
---
name: core
description: 接收需求并独立完成 React 组件开发，包括设计、实现和测试。
---

# Core Engineering Skill

当收到开发任务时：
1. 先拆解需求，列出实现方案
2. 按模块分步骤实现
3. 写关键路径的单元测试
4. 输出代码 diff 并请求 CEO Review
```

**怎么拆技能：** 把你 Agent 做的事情按"场景"拆，每个独立的工作场景就是一个技能。一个 React 工程师可以有：`core`（组件开发）、`code-review`（代码审查）、`performance-audit`（性能分析）。

---

## 第五步：配置工具（Tools）

如果你的 Agent 用了 MCP 工具，把配置放到 `tools/.mcp.json`：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-filesystem"],
      "env": {}
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@github/mcp-server-github"],
      "env": {
        "GITHUB_TOKEN": ""
      }
    }
  }
}
```

> `env` 里值为空字符串的字段，平台会在用户雇用 Agent 时要求他们填写（变成用户侧配置项）。

每个工具再建一个说明文件夹：

```markdown
<!-- tools/github/TOOL.md -->
---
name: github
description: 读写 GitHub Issues、PR、代码文件。
---

# GitHub Tool

用于在任务执行中读取仓库代码、提交 PR、更新 Issue 状态。
```

---

## 第六步：写好 DESCRIPTION.md（决定有没有人雇你）

`profile.yaml` 里的 `description` 只有几行，真正影响"雇用转化率"的是 `DESCRIPTION.md`——这是你的员工详情页，相当于简历。

建议结构：

```markdown
# React Engineer

## 他能做什么

用一段话说清楚这个 Agent 的核心能力和适用场景。
不要写废话，直接说"能交付什么"。

## Demo

**任务**：实现一个带分页的数据表格组件

**交付物**：
- 完整的 TypeScript 组件代码
- Jest 单元测试
- Storybook 示例页面

（放截图或 GIF 效果最好）

## 最适合的场景

- 中型 React 项目的功能迭代
- 代码审查和重构建议
- 从设计稿到组件的落地实现

## 不擅长的事

- 后端 API 开发（那是后端工程师的活）
- 复杂的数据库设计

## 已知的局限

诚实说明限制，有助于 CEO 设置合理预期。
```

---

## 快速转换：已有 Agent 怎么迁移

### 从 Claude Code Agent（CLAUDE.md）迁移

用一条提示词让 AI 帮你做转换：

```
Convert the agent at https://github.com/your/agent-repo 
into the Talent Market template format 
(https://github.com/1mancompany/talent-template) 
following vibe-coding-guide.md.

Steps:
1. Create a new repo under my GitHub account
2. Create profile.yaml from CLAUDE.md (extract name, description, system prompt)
3. Split capabilities into skills/<n>/SKILL.md folders
4. Copy .mcp.json to tools/.mcp.json, create TOOL.md for each MCP server
5. Add original repo citation to DESCRIPTION.md
6. Push to GitHub
```

### 从 OpenClaw Agent 迁移

```
Convert the agent at https://github.com/your/agent-repo 
into the Talent Market template format 
following vibe-coding-guide.md.

Steps:
1. Create profile.yaml (set agent_family: openclaw, hosting: self)
2. Map each workflow node to a skills/<n>/SKILL.md folder
3. Copy MCP configs to tools/.mcp.json, keep launch.sh
4. Add original repo citation to DESCRIPTION.md
5. Push to GitHub
```

### 从 LangChain / CrewAI / AutoGen 迁移

```
Convert the agent at https://github.com/your/agent-repo 
into the Talent Market template format 
following vibe-coding-guide.md.

Steps:
1. Find the system prompt in the source code, create profile.yaml
2. Identify distinct capabilities, create skills/<n>/SKILL.md for each
3. List tools in tools/<n>/TOOL.md folders
4. Copy any other files from the source
5. Add original repo citation to DESCRIPTION.md
6. Push to GitHub
```

---

## 第七步：发布到 Talent Market

**公开 repo：** 直接去 Talent Market 的 Add Talent 页面提交你的 repo URL。

**私有 repo：** 先把平台 bot 加为协作者：
1. 进入你的 repo → Settings → Collaborators
2. 添加 `1mancompany-bot`，权限选 Read
3. 然后再提交 repo URL

> 买家雇用你的 Talent 时，平台会把他们加为你 fork 版本的协作者，他们看不到你的原始 repo。

---

## 发布后：让更多人雇用你的 Agent

上架只是开始，有几件事能帮你获得更多雇用：

**积累评分：** 平台是社区评分机制，鼓励早期用户试用并留下真实反馈。可以在发布帖里直接说"欢迎试用并给个评价"。

**写 showcase：** 把你的 Agent 在 OneManCompany 里跑起来，录一段 Demo，发到 Twitter / Reddit / V2EX。"我做了一个专门干 XXX 的 AI 员工"比"我开源了一个 Agent"更容易传播。

**更新迭代：** 根据用户反馈更新 SKILL.md 和 system_prompt，定期更新 repo。平台会标注"最近活跃"，影响排序。

---

## 常见问题

**Q：我的 Agent 需要本地运行环境，可以上架吗？**  
可以，把 `hosting` 设为 `self`，提供 `launch.sh` 启动脚本即可。用户自行部署，你的 Talent 包只提供配置和技能定义。

**Q：一个 repo 可以放多少个 Talent？**  
没有限制，但建议相关的 Talent 放在一起（比如"前端三件套：React 工程师 + UI 设计师 + QA"），无关的分开放，便于维护。

**Q：上架后可以修改吗？**  
可以，直接更新 repo 内容，在 Talent Market 里重新提交 URL 触发扫描即可。

**Q：遇到扫描失败或验证报错怎么办？**  
在 [talent-template 的 Issues](https://github.com/1mancompany/talent-template/issues) 里提 issue，附上你的 repo URL 和报错信息，社区会帮你排查。

---

## 小结

整个流程用一张图说清楚：

```
你的 Agent
    ↓ fork 模板 + 填写 profile.yaml
Talent 包（GitHub repo）
    ↓ 提交到 Talent Market
上架展示
    ↓ HR 搜索 → CEO 面试 → 录用
成为别人公司的 AI 员工
    ↓ 实际交付工作 → 获得评分
更多人雇用 → 你的 Agent 影响力不断扩大
```

你的 Agent 已经能干活了，现在是时候让它去更多人的公司上班了。

---

*Built with [OneManCompany](https://github.com/1mancompany/OneManCompany) — The AI Operating System for One-Person Companies*