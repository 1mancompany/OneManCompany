# 快速开始

本指南将带你从首次启动到以 CEO 身份完成第一个任务。

## 安装

你只需要 **Node.js 16+** 和 **Git**，其余一切自动安装。

```bash
npx @carbonkite/onemancompany
```

首次运行会自动完成以下步骤：

1. 安装 **UV**（高速 Python 包管理器）
2. 通过 UV 安装 **Python 3.12**（隔离安装，不影响系统环境）
3. 克隆代码仓库
4. 创建虚拟环境并安装依赖
5. 启动配置向导

## 配置向导

向导会引导你完成以下设置：

1. **OpenRouter API Key** — 必填。通过 LLM 驱动你的 AI 员工。前往 [openrouter.ai](https://openrouter.ai) 获取
2. **默认模型** — 浏览并选择可用模型。每位员工之后可以单独分配不同的模型
3. **服务器配置** — 主机和端口（默认：`0.0.0.0:8000`）
4. **可选密钥** — Anthropic API key、FastSkills MCP key、Talent Market API key。没有的可以按 Enter 跳过

!!! tip "推荐注册 Talent Market"
    在 [carbonkites.com](https://carbonkites.com) 注册以获取 Talent Market API key，这样你就可以雇佣社区验证过的 AI 员工。

## 你的第一次会话

配置完成后，在浏览器中打开 `http://localhost:8000`，你会看到：

- **左侧面板** — 员工花名册，显示你的创始团队
- **中央区域** — 像素风办公室，AI 员工们坐在各自的工位上
- **右侧面板** — CEO 控制台，用于发布指令、管理任务和审批

### 你的创始团队

四位高管在第一天就已就位：

| 员工 | 角色 |
| --- | --- |
| **EA** | 任务路由、质量把关 |
| **HR** | 招聘、绩效评估、晋升 |
| **COO** | 运营、任务分发、验收 |
| **CSO** | 销售、客户关系 |

### 发布你的第一个任务

在 CEO 控制台中输入一个任务：

> "做一个简单的益智游戏"

观察接下来会发生什么：

1. **EA** 接收并路由任务
2. **COO** 将任务拆解为子任务
3. 如果需要更多人手，**HR** 会在 Talent Market 中搜索
4. 员工们自主工作，必要时召开会议
5. 工作成果经过评审和质量关卡
6. 最终结果提交给你审批

## 再次启动

```bash
npx @carbonkite/onemancompany
```

有新版本会自动更新。如果服务已在运行，会提示是否停止并重新配置。

## 重新配置

```bash
# 重新运行配置向导
npx @carbonkite/onemancompany init

# 自定义端口
npx @carbonkite/onemancompany --port 8080
```

## 卸载

```bash
npx @carbonkite/onemancompany uninstall
```

停止正在运行的服务，并删除整个安装目录。需要确认后才会执行。

## 配置文件

| 文件 | 用途 |
| --- | --- |
| `.onemancompany/.env` | API 密钥（OpenRouter、Anthropic 等） |
| `.onemancompany/config.yaml` | 应用配置（Talent Market URL 等） |
| 浏览器设置面板 | 前端偏好设置 |

## 下一步

- [执行模式](execution-modes.zh.md) — 在 Company Hosted Agent 和 Claude Code 之间切换
- [任务管理](task-management.zh.md) — 了解完整的任务生命周期
- [招聘](hiring.zh.md) — 从 Talent Market 扩充你的团队
