# Godot 引擎调研报告：AI 驱动游戏开发与发布

## 调研背景
CEO 任务：调研 Godot 引擎，评估 AI 开发游戏 + 发布的可行性，重点关注 API 能力。

---

## 核心结论

**Godot 具备完整的 API 体系，是当前最适合 AI 驱动游戏开发的引擎。** 开源、免费、CLI/headless 原生支持、MCP 生态成熟，"AI 生成代码 -> 自动构建 -> 多平台发布"全流程可实现。

---

## 1. Godot 引擎概述

| 项目 | 说明 |
|------|------|
| **最新版本** | 4.6（2026年1月发布） |
| **开源协议** | MIT（完全免费，无版税） |
| **语言支持** | GDScript、C#（.NET 6+）、C++、GDExtension |
| **适合类型** | 2D/3D 游戏，独立游戏，快速原型 |
| **优势** | 轻量（<100MB）、全平台、场景树架构直观 |
| **劣势** | 3A 级 3D 性能不及 Unreal，C# 移动端支持有限 |

## 2. API 体系（回答核心问题：是否有 API？）

**有，且非常丰富：**

### 2.1 GDScript / C# 编程 API
- 完整的节点/场景树操作 API（创建、修改、删除节点）
- 资源加载/保存 API（`ResourceLoader`/`ResourceSaver`）
- `@tool` 注解：GDScript 可在编辑器内执行，用于自动化
- EditorPlugin 系统：可编程扩展编辑器功能

### 2.2 命令行接口（CLI）
```bash
# headless 模式导入资源
godot -v -e --quit --headless

# headless 模式导出游戏
godot -v --export-release --headless "Windows" output.zip

# 导出调试版本
godot -v --export-debug --headless "Linux/X11" /var/builds/project

# 运行特定场景
godot --path /project res://scene.tscn
```

### 2.3 REST API / 外部控制
Godot **没有内建 REST API**，但生态中已有成熟方案：
- **MCP Server**（多个开源项目）：AI 可完整控制 Godot 编辑器
- **HTTPRequest 节点**：游戏运行时可调用外部 API（OpenAI、Ollama 等）

## 3. AI 开发游戏的可行性

### 3.1 MCP（Model Context Protocol）集成 — 关键突破
多个开源 MCP Server 已可用：
- Godot MCP Server (FlowHunt)
- Coding-Solo/godot-mcp
- GDAI MCP

**AI 代理可以**：
- 启动/关闭 Godot 编辑器
- 创建场景、添加节点、设置属性、附加脚本
- 实时读取编辑器错误和调试输出
- 截取编辑器/游戏画面用于视觉反馈
- 与 Claude Desktop、Cursor、VSCode 集成

### 3.2 AI 辅助开发工具
| 工具 | 类型 | 说明 |
|------|------|------|
| **Godot AI Assistant Hub** | 免费插件 | 嵌入 AI 到编辑器，支持 Ollama/Gemini/OpenRouter |
| **Godot Copilot** | 开源插件 | OpenAI API 驱动的代码补全 |
| **Godot AI Suite** | 付费插件 | 支持 Claude/ChatGPT/Gemini，带 Agent Mode |
| **Workik Godot Generator** | 在线工具 | AI 生成 GDScript 代码 |

### 3.3 Headless 自动化流水线
- 原生 `--headless` 模式，无需 GPU/显示器
- Docker 镜像可用（`barichello/godot-ci`、`robpc/docker-godot-headless`）
- Python 自动化管线
- CI/CD 集成：TeamCity、GitHub Actions 均有成熟方案

**已知问题**：Godot 4.3+ headless 导出偶有冻结 bug，iOS headless 构建存在兼容问题。

## 4. 多平台发布能力

| 平台 | 支持情况 |
|------|---------|
| Windows / macOS / Linux | 完整支持 |
| Android | 完整支持 |
| iOS | 支持（headless 构建有限制） |
| Web (HTML5/WASM) | 完整支持 |
| 主机 (Switch/PS/Xbox) | 需第三方工具/授权 |
| VisionOS | 4.x 新增 |

**分发渠道**：Steam、itch.io（Butler 工具自动上传）、App Store、Google Play。

## 5. 与其他引擎对比

| 特性 | Godot | Unity | Unreal | Roblox |
|------|-------|-------|--------|--------|
| **价格** | 免费/MIT | 订阅制+版税 | 免费+版税(>$1M) | 免费+平台分成 |
| **开源** | 是 | 否 | 部分源码 | 否 |
| **AI 自动化** | MCP+CLI+@tool | 有限 CLI | 有限 | 有限 |
| **Headless 模式** | 原生支持 | 需 batchmode | 需配置 | 不支持 |
| **轻量级** | ~80MB | ~5GB | ~50GB | N/A |
| **2D 能力** | 优秀 | 良好 | 一般 | 一般 |
| **3D 能力** | 良好 | 优秀 | 顶级 | 简单 |
| **学习曲线** | 低 | 中 | 高 | 低 |

**Godot 在 AI 自动化方面的优势最突出**：开源可定制、CLI 完备、MCP 生态活跃、无授权费用。

## 6. 推荐方案

对于我们"AI 开发游戏 -> 自动发布"的目标，推荐架构：

```
AI Agent (Claude/LLM)
    |
    v
Godot MCP Server  <-- AI 控制编辑器
    |
    v
Godot Editor (headless)  <-- 创建/修改场景和脚本
    |
    v
CLI Export (--headless --export-release)  <-- 自动构建
    |
    v
Steam / itch.io / Web  <-- 自动发布
```
