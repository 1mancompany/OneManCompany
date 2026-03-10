# Talent Market Platform Design

**Goal:** Extract talent market into an independent platform at `/Users/yuzhengxu/projects/talentmarket`, with a React frontend community, MCP server for HR integration, and support for third-party talent repos.

**Architecture:** FastAPI backend + React (Vite) frontend. Git-based talent discovery. MCP server (SSE + stdio) for OneManCompany integration.

**Tech:** FastAPI, React, Vite, MCP (SSE/stdio), git

---

## 1. Overall Architecture

```
┌─────────────────────────────────────────────┐
│  Talent Market (独立部署, 可配置 IP:PORT)      │
│                                              │
│  FastAPI Backend                             │
│  ├── /api/*        REST API (前端用)          │
│  ├── /mcp          SSE MCP endpoint (远端)   │
│  └── stdio mode    MCP stdio (本地开发)       │
│                                              │
│  React Frontend (Vite)                       │
│  ├── 浏览 Talent 列表                         │
│  ├── 搜索（role/skill/keyword）               │
│  ├── Talent 详情页                            │
│  └── 添加 Talent（git repo URL + 表单）        │
│                                              │
│  Registry (registry.json)                    │
│  └── [{repo_url, talents[], synced_at}]      │
└─────────────────────────────────────────────┘
         ↑ MCP (SSE/stdio)
         │
┌────────┴────────┐
│  OneManCompany   │
│  HR Agent        │
│  (MCP Client)    │
└─────────────────┘
```

## 2. Registry Data Model

```json
[
  {
    "repo_url": "https://github.com/user/my-talents",
    "added_at": "2026-03-10T12:00:00Z",
    "last_synced": "2026-03-10T12:00:00Z",
    "talents": [
      {
        "id": "claude_code",
        "name": "Claude Code Agent",
        "role": "Engineer",
        "description": "Remote AI software engineer...",
        "hosting": "remote",
        "skills": ["autonomous_coding"],
        "personality_tags": ["autonomous"],
        "manifest": { ... },
        "source": "profile.yaml | inferred"
      }
    ]
  }
]
```

## 3. Talent Package Standard Format

```
my-talents-repo/
├── talent-a/              # 目录名 = talent ID
│   ├── profile.yaml       # ✅ 必须（或平台表单生成）
│   ├── manifest.json      # 可选 — 前端 UI、能力声明
│   ├── skills/            # 可选
│   │   └── {skill-name}/
│   │       ├── SKILL.md
│   │       └── references/
│   ├── tools/             # 可选
│   │   ├── manifest.yaml
│   │   └── {name}.py
│   ├── launch.sh          # 可选
│   └── CLAUDE.md          # 可选
├── talent-b/
│   └── profile.yaml
└── README.md
```

### profile.yaml 最小示例
```yaml
id: my-assistant
name: My Custom Assistant
description: A specialized assistant for...
role: Assistant
hosting: company
system_prompt_template: |
  You are a specialized assistant that...
```

### 校验规则
1. 子目录含 `profile.yaml` → 识别为 talent
2. 必须字段: `id`, `name`, `role`, `description`, `hosting`
3. `id` 与目录名一致
4. `role` 枚举: Engineer, Designer, Manager, Analyst, Assistant, DevOps, QA, Marketing
5. `hosting` 枚举: company, self, remote

## 4. Claude Code Agent 兼容

对没有 `profile.yaml` 但有 `CLAUDE.md` 的目录，通过映射函数兼容：

### 添加流程
```
用户输入 git repo URL
  → git clone --depth 1
  → 扫描子目录
  → 有 profile.yaml → 直接注册
  → 有 CLAUDE.md 但没 profile.yaml → 弹出表单
     → 预填从 CLAUDE.md/README.md 推断的值
     → 用户填写: Name, Description, Role, Hosting, Skills, Fee
     → 平台侧缓存生成的 profile 到 registry（不改原 repo）
  → 上架完成
```

### 格式映射
| Claude Code 风格 | 我们的格式 | 映射方式 |
|---|---|---|
| `.mcp.json` | tools MCP 声明 | 提取 server 配置，雇佣时注入 MCP config |
| `tools/*.py` 无 manifest | `tools/manifest.yaml` | 自动扫描生成 |
| `skills/*.md` 扁平文件 | `skills/{name}/SKILL.md` | 文件名→skill name，内容→skill body |
| `CLAUDE.md` | system_prompt | 作为 `_claude_instructions` skill, autoload: true |

### Manifest 自动生成

| 检测到 | 生成的 Settings Section |
|---|---|
| `.mcp.json` 有 `env: {XXX_API_KEY: ""}` | Secret 字段 |
| `hosting: self` | Connection section (OAuth/API key) |
| `hosting: remote` | Endpoint section (URL + Token) |
| `profile.yaml` 有 `llm_model` | LLM section (model + temperature) |
| `launch.sh` 引用 `$ENV_VAR` | 对应 text/secret 字段 |
| `tools/*.py` | Tools section (readonly 列表) |
| `skills/*/SKILL.md` | Skills section (readonly 列表) |

优先级: repo 原文件 > 自动推断 > 表单填写

## 5. MCP Server

暴露工具给 OneManCompany HR:
- `search_candidates(job_description, count)` — 搜索匹配 talent
- `get_talent(talent_id)` — 获取完整 talent 信息
- `list_talents(role?, skills?)` — 列表筛选

传输方式:
- **SSE**: `http://{host}:{port}/mcp` (远端部署)
- **stdio**: `python -m talentmarket.mcp` (本地开发)

## 6. Out of Scope (v1)

- 用户认证/登录
- Talent 评分/评论
- 版本管理（talent 更新）
- 付费/交易
