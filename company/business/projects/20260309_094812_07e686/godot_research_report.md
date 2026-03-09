# Godot 引擎 AI 驱动游戏开发与发布能力调研报告

## 任务来源
CEO 下发调研任务：调研 Godot 引擎，了解其 API 能力，评估 AI 驱动游戏开发与发布的可行性。

## 任务分派
- **负责人**: Claude Game Dev Engineer (00007, 铸客)
- **分派理由**: 该任务属于游戏引擎技术调研，00007 具备 game-engine、roblox-game-development 等技能，是最合适的执行人
- **分派时间**: 2026-03-09

---

## 调研结论（摘要）

**Godot 是目前最适合 AI 驱动游戏开发的引擎之一。** 其开源特性、完整的 CLI/headless 支持、活跃的 MCP 生态，使得 "AI 生成代码/场景 → 自动构建 → 多平台导出" 的全流程自动化完全可行。

---

## 1. 脚本/自动化 API

- **`@tool` 注解**：GDScript 文件顶部加 `@tool`，脚本可在编辑器内执行，用于自动化场景修改、自定义可视化等
- **EditorPlugin 系统**：继承 `EditorPlugin`，可访问和修改编辑器各种功能
- **编程式场景创建**：通过代码动态创建节点和场景树

## 2. 编程式创建、修改和导出游戏

完全可行：
- **场景树 API**：通过 GDScript/C# 可编程创建场景、添加/删除/移动节点、设置属性、附加脚本
- **资源系统**：所有游戏资源可通过 API 加载和操作
- **CLI 导出**：命令行支持自动化导出

## 3. 发布/导出能力

| 平台 | 说明 |
|------|------|
| **桌面** | Windows、macOS、Linux |
| **移动端** | Android、iOS |
| **Web** | HTML5/WebAssembly |
| **主机** | 需第三方工具/授权 |
| **VisionOS** | 4.x 新增支持 |

分发渠道：Steam、itch.io、App Store、Google Play 等。

## 4. Headless 模式 / CLI 自动构建

原生支持，适合 CI/CD：

```bash
# headless 模式导入资源
godot -v -e --quit --headless

# headless 模式导出游戏
godot -v --export-release --headless "Windows" output.zip

# 导出调试版本
godot -v --export-debug --headless "Linux/X11" /var/builds/project
```

关键参数：`--headless`（无显示器运行）、`--export-release`/`--export-debug`、`--quit`

## 5. GDScript/C# 脚本能力

- **GDScript**：原生脚本语言，类 Python 语法，`@tool` 注解使其在编辑器中运行
- **C#**：Godot 4.x 通过 .NET 6+ 支持，桌面平台全面支持
- **C++ / GDExtension**：可编写高性能扩展，无需重新编译引擎

## 6. REST API / 外部控制 API

Godot 本身**没有内建 REST API**，但生态中已有成熟方案：

- **MCP (Model Context Protocol) 服务器**：AI 驱动开发的关键突破
  - 多个开源项目：GDAI MCP、Coding-Solo/godot-mcp、LeeSinLiang/godot-mcp
  - AI 代理可完整控制 Godot 编辑器（创建场景、添加节点、设置属性、附加脚本）
  - 实时读取编辑器错误和调试输出
  - 截取编辑器/游戏画面用于视觉反馈
  - 与 Claude Desktop、Cursor、VSCode 等 AI 工具集成
- **HTTPRequest 节点**：游戏内可调用外部 REST API（OpenAI、Ollama 等）
- **插件生态**：已有 LM Studio API、OpenAI API 等 Godot 插件

## 7. 综合评估

| 能力 | 可行性 | 备注 |
|------|--------|------|
| AI 编程式创建游戏 | ⭐⭐⭐⭐⭐ | MCP + @tool 脚本 + CLI 全自动化 |
| 自动构建与导出 | ⭐⭐⭐⭐⭐ | 原生 headless CLI，CI/CD 友好 |
| 多平台发布 | ⭐⭐⭐⭐⭐ | 桌面/移动/Web 全覆盖 |
| AI 代理控制编辑器 | ⭐⭐⭐⭐ | MCP 生态已成熟 |
| 无人值守全流程 | ⭐⭐⭐⭐ | 创建→编辑→导出可全 CLI，部分平台需特定 SDK |

## 参考来源

- [Godot Command Line Tutorial (4.4)](https://docs.godotengine.org/en/4.4/tutorials/editor/command_line_tutorial.html)
- [Running Code in the Editor - @tool Scripts](https://docs.godotengine.org/en/stable/tutorials/plugins/running_code_in_the_editor.html)
- [Making Plugins - EditorPlugin](https://docs.godotengine.org/en/stable/tutorials/plugins/editor/making_plugins.html)
- [GDAI MCP Server](https://gdaimcp.com/)
- [Coding-Solo/godot-mcp](https://github.com/Coding-Solo/godot-mcp)
- [LeeSinLiang/godot-mcp](https://github.com/LeeSinLiang/godot-mcp)
- [Automating Godot Builds with TeamCity](https://blog.jetbrains.com/teamcity/2024/10/automating-godot-game-builds-with-teamcity/)
- [C# Platform Support in Godot 4.2](https://godotengine.org/article/platform-state-in-csharp-for-godot-4-2/)
