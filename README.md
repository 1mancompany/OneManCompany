# OneManCompany

一人公司模拟器 — 像素风可视化 + LangChain AI Agent

## 架构

- **CEO** (真人) — 通过浏览器输入任务和目标
- **HR Agent** — 季度评价员工表现，通过 MCP 服务器招聘新 AI 员工
- **COO Agent** — 添置工具/技能，在像素画面中显示为办公设备

## 技术栈

- Python 3.12+ / UV 包管理
- LangChain `create_react_agent` 创建所有 AI 角色
- FastAPI + WebSocket 实时通信
- MCP (Model Context Protocol) 服务器提供招聘能力
- 纯 Canvas 2D 像素画前端（无需构建工具）

## 启动

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY

# 3. 启动服务器
uv run onemancompany

# 4. 打开浏览器
open http://localhost:8000
```

## 使用

- 在 CEO Console 输入任务（Ctrl+Enter 提交）
- 包含 "招聘/hire/employee" 等关键词 → 自动分配给 HR
- 其他任务 → 自动分配给 COO 添加工具
- 点击 "季度评价" 按钮 → HR 审查所有员工并招聘新人
- 悬停像素画中的角色可查看详情
