# Godot 游戏引擎深度调研报告

> 调研日期：2026-03-09
> 当前最新版本：Godot 4.6.1 (2026年2月16日发布)

---

## 一、API 体系

### 1.1 GDScript API

GDScript 是 Godot 的内置脚本语言，语法类似 Python，专为游戏开发优化，与引擎深度集成。

**核心语言特性 (Godot 4.x)：**
- **可选静态类型**：`var speed: float = 10.0`，类型标注提升性能和可读性
- **类型数组**：`var enemies: Array[Enemy] = []`，运行时类型检查
- **协程**：`await` 关键字（替代 3.x 的 `yield`）
- **一等信号**：`button.pressed.connect(_on_pressed)`
- **Lambda 函数**：`var my_lambda = func(x): return x * 2`
- **注解系统**：`@export`、`@onready`、`@tool`、`@icon` 等
- **模式匹配**：`match` 语句支持解构

**核心类层级：**
```
Object
├── RefCounted (引用计数，自动释放)
│   ├── Resource (可序列化数据：纹理、网格、脚本)
│   │   ├── PackedScene
│   │   ├── Texture2D / Texture3D
│   │   ├── Material / ShaderMaterial / StandardMaterial3D
│   │   └── Script (GDScript, CSharpScript 等)
│   └── Tween
├── Node (场景树基类)
│   ├── Node2D (2D 定位)
│   │   ├── Sprite2D, CharacterBody2D, Camera2D, Area2D, TileMapLayer
│   ├── Node3D (3D 定位，原 Spatial)
│   │   ├── MeshInstance3D, CharacterBody3D, Camera3D, Light3D
│   ├── Control (UI 基类)
│   │   ├── Label, Button, LineEdit, Container, Panel
│   └── CanvasLayer, AudioStreamPlayer
└── MainLoop → SceneTree (管理活动场景)
```

**全局 API：**
- `@GlobalScope`：数学函数（`lerp`、`clamp`、`randf`）、类型构造器
- `@GDScript`：`preload()`、`load()`、`print()`、`range()`
- 内置单例：`Engine`、`Input`、`OS`、`DisplayServer`、`RenderingServer`、`PhysicsServer2D/3D`、`ResourceLoader`、`ResourceSaver`、`ClassDB`

### 1.2 C# API (.NET)

Godot 4.x 基于 **.NET 6+**（推荐 .NET 8）提供完整 C# 支持。

**核心特性：**
- 完整 Godot API 绑定，`Godot` 命名空间下所有引擎类均可用
- `[Export]` 属性暴露到编辑器
- `[Signal]` 委托声明自定义信号
- 支持 NuGet 包管理器
- IDE 支持：Visual Studio, Rider, VS Code

**平台支持状态：**

| 平台 | 状态 | 备注 |
|------|------|------|
| Windows/macOS/Linux | ✅ 稳定 | .NET 运行时 |
| Android | ⚠️ 实验性 | Mono bionic |
| iOS | ⚠️ 实验性 | NativeAOT，有 trimming 问题 |
| **Web** | **❌ 不支持** | WASM 动态链接限制，架构性阻断 |

**关键限制：**
1. 无 Web 导出能力
2. Variant 编组开销（每次 C#↔引擎调用经过 Variant 类型系统）
3. 需下载单独的 ".NET" 版 Godot 编辑器
4. C# 无法直接调用 GDExtension（需通过 GDScript 中转）

**成熟度评估：** 桌面端生产就绪，移动端实验性，Web 端缺失。长期计划将 C# 迁移为 GDExtension 插件形式。

### 1.3 GDExtension（原 GDNative）

GDExtension 是 Godot 4.x 的 **C ABI 原生扩展接口**，允许动态库（.dll/.so/.dylib）在运行时注册自定义类。

**工作流程：**
1. 使用 `gdextension_interface.h` 编写原生代码
2. 定义入口函数注册自定义类到 `ClassDB`
3. 编译为平台特定共享库
4. 创建 `.gdextension` 清单文件映射平台路径
5. 放入项目目录，引擎自动加载

**语言绑定生态：**

| 语言 | 绑定项目 | 成熟度 |
|------|----------|--------|
| **C/C++** | godot-cpp（官方） | 生产就绪 |
| **Rust** | gdext (godot-rust) | 活跃，大部分 API 已映射 |
| **Swift** | SwiftGodot | 活跃社区项目 |
| **D/Nim/Haxe** | 各社区绑定 | 社区维护 |
| **Kotlin/JVM** | godot-jvm | 社区维护 |

**核心优势：**
- 热重载支持（4.2+）
- 自定义类出现在编辑器"添加节点"对话框
- 无需引擎重编译
- 接近原生性能

### 1.4 场景与资源文件格式

| 格式 | 类型 | 特点 |
|------|------|------|
| `.tscn` | 文本场景 | **人类可读**，类 INI 格式，版本控制友好 |
| `.tres` | 文本资源 | **人类可读**，存储材质、脚本资源等 |
| `.scn` | 二进制场景 | 体积小，加载快，不可读 |
| `.res` | 二进制资源 | 同上 |

**`.tscn` 文件结构：**
```ini
[gd_scene load_steps=4 format=3 uid="uid://cecaux1sm7mo0"]

[ext_resource type="Texture2D" uid="uid://abc123" path="res://icon.svg" id="1_abc"]
[ext_resource type="Script" uid="uid://def456" path="res://player.gd" id="2_def"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_abc"]
size = Vector2(32, 64)

[node name="Player" type="CharacterBody2D"]
script = ExtResource("2_def")

[node name="Sprite" type="Sprite2D" parent="."]
texture = ExtResource("1_abc")
position = Vector2(0, -16)

[node name="CollisionShape" type="CollisionShape2D" parent="."]
shape = SubResource("RectangleShape2D_abc")

[connection signal="body_entered" from="Area2D" to="." method="_on_body_entered"]
```

**关键要点：** UID 可在外部生成时省略（Godot 首次加载时自动分配）；资源 ID 只需文件内唯一；节点层级通过 `parent` 属性定义。

---

## 二、AI 驱动开发可行性

### 2.1 命令行与无头模式

Godot 提供**完善的 CLI 支持**，完全满足 AI 自动化管线需求：

| 命令 | 用途 |
|------|------|
| `godot --headless` | 无图形界面启动（适用于 CI/自动化） |
| `godot --headless -e --quit` | 无头模式打开编辑器导入所有资源后退出 |
| `godot --headless --export-release "preset" path` | 无头导出发布版本 |
| `godot --headless --export-debug "preset" path` | 无头导出调试版本 |
| `godot --headless -s script.gd --quit` | 无头执行独立脚本 |
| `godot --headless --export-pack "preset" path` | 仅导出 PCK/ZIP 数据包 |
| `godot --path <dir>` | 指定项目路径 |
| `godot -q` / `--quit` | 首次迭代后退出 |

**无头脚本执行要求：**
```gdscript
extends SceneTree
func _init():
    print("Running headless script")
    # 执行生成/验证逻辑...
    quit()
```

### 2.2 LLM 生成代码/场景的可行性评估

| 维度 | 评分 | 说明 |
|------|------|------|
| GDScript 代码生成 | ⭐⭐⭐⭐ | 语法类 Python，LLM 适配性强 |
| .tscn 场景生成 | ⭐⭐⭐⭐⭐ | 纯文本格式，结构规范，可直接生成 |
| .tres 资源生成 | ⭐⭐⭐ | 简单资源可生成，复杂资源（着色器）较难 |
| 项目结构生成 | ⭐⭐⭐⭐⭐ | project.godot 是文本配置，结构简单 |
| 可视化资源 | ⭐⭐ | 需外部工具（Stable Diffusion 等） |

**GDScript 对 AI 生成的有利因素：**
- Python-like 语法，LLM 训练数据中 Python 占比极高
- 简洁性强，无花括号/分号样板代码
- 单文件 = 单类，结构清晰
- API 命名规范统一（`_ready()`、`_process()`、`_physics_process()`）
- 内置类型提示辅助类型安全生成

**GDScript 对 AI 生成的不利因素：**
- 小众语言，LLM 训练数据占比远低于 Python/JS/C#
- 易与 Python 混淆（`import` vs `preload()`）
- 3.x vs 4.x API 差异大，LLM 可能混用版本

**外部程序化生成示例 (Python):**
```python
def generate_scene(enemy_positions):
    lines = ['[gd_scene load_steps=2 format=3]']
    lines.append('[ext_resource type="PackedScene" path="res://enemy.tscn" id="1_enemy"]')
    lines.append('')
    lines.append('[node name="Level" type="Node2D"]')
    for i, pos in enumerate(enemy_positions):
        lines.append(f'[node name="Enemy{i}" parent="." instance=ExtResource("1_enemy")]')
        lines.append(f'position = Vector2({pos[0]}, {pos[1]})')
    return '\n'.join(lines)
```

### 2.3 现有 AI/LLM 集成工具

**编辑器内 AI 助手：**

| 工具 | 描述 | 特点 |
|------|------|------|
| **AI Assistant Hub** | 嵌入式 AI 助手 | 支持 Ollama 本地 LLM，可读写编辑器内代码 |
| **Godot Copilot** | OpenAI API 代码补全 | 活跃开发 |
| **Ziva** | 智能 AI 助手 | 理解整个项目上下文 |
| **AI Assistants For Godot 4** | 专业级 AI 编码助手 | 高级 Markdown 渲染 |

**LLM 运行时集成：**

| 工具 | 描述 |
|------|------|
| **Godot LLM Framework** | 游戏内集成 LLM，基于 llama.cpp |
| **Godot LLM** | GdLlama/GDEmbedding/GDLlava 节点 |
| **godot-dodo** | GDScript 专用 LLM 微调管线 |

**godot-dodo 微调实验关键发现：**
- 从 GitHub MIT 仓库爬取 GDScript 代码，用 GPT 生成指令标注
- 微调后模型在 GDScript 语法准确性上**显著优于 GPT-4/GPT-3.5-turbo**
- 代码专用基座模型微调后在复杂指令上甚至超越通用大模型

### 2.4 建议的 AI 开发架构

```
[LLM Agent (Claude/GPT)]
        |
        v
[代码/场景生成器] -- 生成 --> .gd 脚本文件
        |                      .tscn 场景文件
        |                      .tres 资源文件
        |                      project.godot 配置
        v
[Godot CLI 验证] -- godot --headless -e --quit
        |
        v
[Godot CLI 构建] -- godot --headless --export-release
        |
        v
[多平台发布包]
```

### 2.5 编辑器插件系统

Godot 的 `@tool` 脚本 + `EditorPlugin` 系统支持深度自定义：
- 自定义编辑器面板/Dock：`add_control_to_dock()`
- 自定义节点类型：`add_custom_type()`
- 自定义导入器/导出器：`EditorImportPlugin` / `EditorExportPlugin`
- Inspector 插件：`EditorInspectorPlugin`
- 热加载，无需重启编辑器

可据此开发深度集成的 AI 辅助工具插件。

---

## 三、发布能力

### 3.1 支持平台

| 平台 | GDScript | C# | 备注 |
|------|----------|------|------|
| **Windows** | ✅ | ✅ | x86_64, ARM64 |
| **macOS** | ✅ | ✅ | Universal (Intel + Apple Silicon) |
| **Linux** | ✅ | ✅ | x86_64, ARM64 |
| **Android** | ✅ | ⚠️ 实验性 | ARM64, x86_64 |
| **iOS** | ✅ | ⚠️ 实验性 | 需 Xcode |
| **Web (HTML5)** | ✅ | ❌ | WebAssembly + WebGL 2.0，仅兼容渲染模式 |
| **Nintendo Switch** | ⚠️ 第三方 | — | W4 Games 提供，需开发者许可 |
| **PlayStation 5** | ⚠️ 第三方 | — | W4 Games 提供，需开发者许可 |
| **Xbox Series X/S** | ⚠️ 第三方 | — | W4 Games 提供，需开发者许可 |

### 3.2 Web 导出限制
- 仅支持 **Compatibility 渲染模式**（WebGL 2.0）
- Forward+ 和 Mobile 渲染方法不可用
- C# 项目不支持 Web 导出
- 性能低于原生平台

### 3.3 主机发布
- Godot 开源许可导致无法直接包含主机 SDK
- 通过 **W4 Games**（Godot 核心开发者创办）提供官方中间件移植
- 开发者需有平台方批准的开发者资格

### 3.4 自动化发布

**CLI 导出命令：**
```bash
# 导入资源（导出前必须执行）
godot --headless -e --quit --path /path/to/project

# 各平台导出
godot --headless --path /project --export-release "Windows Desktop" ./build/game.exe
godot --headless --path /project --export-release "Linux/X11" ./build/game
godot --headless --path /project --export-release "Web" ./build/index.html
godot --headless --path /project --export-release "Android" ./build/game.apk
```

**CI/CD 解决方案：**

| 方案 | 特点 |
|------|------|
| **godot-ci** (Docker) | GitHub Actions/GitLab CI 模板，部署到 itch.io/GitHub Pages |
| **W4 Build** | Godot 专用云端 CI，由 W4 Games 提供 |
| **Codemagic** | 支持 Godot 的通用 CI/CD 平台 |
| **TeamCity** | JetBrains 官方博客有 Godot 集成教程 |
| **自定义脚本** | CLI 简单，自建管线门槛低 |

---

## 四、与其他引擎对比（AI 开发场景）

### 4.1 综合对比表

| 维度 | Godot | Unity | Unreal | Roblox |
|------|-------|-------|--------|--------|
| **开源** | ✅ MIT | ❌ 专有 | ❌ 专有 | ❌ 专有 |
| **价格** | 完全免费，零分成 | $200K 以下免费 | $1M 以下免费，之后 5% | 免费（平台分成） |
| **文本化场景** | ✅ .tscn 纯文本 | ⚠️ YAML（复杂 GUID） | ❌ 二进制为主 | ❌ XML（极复杂） |
| **CLI/无头模式** | ✅ 完善 | ✅ batchmode | ⚠️ 有限 | ❌ 无 |
| **脚本语言** | GDScript/C#/C++ | C# | C++/Blueprint | Luau |
| **LLM 代码生成适配** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ |
| **场景程序化生成** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| **引擎体积** | ~100MB | ~5GB | ~50GB | ~1GB |
| **学习曲线** | 低 | 中 | 高 | 低 |
| **3D 能力** | 中等（快速进步） | 强 | 顶级 | 中等 |
| **2D 能力** | 顶级 | 强 | 弱 | 中等 |
| **内置发布平台** | 无 | 无 | 无 | ✅ Roblox 平台 |

### 4.2 各引擎 AI 开发优劣分析

#### Godot — AI 开发最佳选择

**优势：**
1. **文本优先**：.tscn/.tres/.gd 全部纯文本，LLM 可直接生成完整场景和代码
2. **轻量级**：~100MB，安装快、启动快，适合 CI/CD 高频迭代
3. **完全开源 MIT**：可深度定制引擎，无许可证限制和收入分成
4. **CLI 完善**：无头模式支持自动编译、测试、导出全流程
5. **GDScript 简洁**：类 Python 语法，LLM 生成质量高

**劣势：**
1. 3D 能力不适合 AAA 级游戏
2. Asset Library 规模远小于 Unity Asset Store (~4,750 vs ~100,000+)
3. 主机发布需第三方（W4 Games），额外成本和门槛
4. C# Web 导出不支持
5. 大型商业成功案例有限

#### Unity

- ✅ C# 生态成熟，AI 代码生成适配性好
- ✅ Asset Store 庞大，加速开发
- ✅ 跨平台支持最全面
- ❌ 场景文件为复杂 YAML+GUID，程序化生成困难
- ❌ 许可证政策引发社区信任危机（2023年事件）
- ❌ 引擎体积大（~5GB），CI/CD 环境搭建慢

#### Unreal Engine

- ✅ 3D 画质顶级，AAA 标准
- ✅ Blueprint 可视化编程
- ❌ C++ 代码 LLM 生成质量低、编译慢、调试难
- ❌ 缺乏合理的无头模式自动化
- ❌ 引擎极其庞大（~50GB），自动化成本高

#### Roblox

- ✅ 自带发布平台和数亿用户群
- ✅ Luau 脚本简单易学
- ❌ 完全封闭平台，无 CLI/无头模式
- ❌ 场景格式为复杂 XML (.rbxlx)，程序化生成极难
- ❌ 平台限制多，创意自由度低

---

## 五、社区与生态

### 5.1 社区规模

| 指标 | 数据 |
|------|------|
| GitHub Stars | **107,000+** |
| GitHub Forks | 24,400+ |
| GitHub Contributors | 5,000+（历史累计） |
| Reddit r/godot | ~307,000 成员 |
| 官方 Discord | ~65,400 成员 |
| Godot Cafe Discord | ~85,600 成员 |
| 许可证 | MIT |

Godot 是 GitHub 上 star 数最高的开源项目之一。社区自 2023 年 Unity 定价风波后**爆发式增长**。

### 5.2 Godot 基金会

- **性质**：非营利组织，与 Software Freedom Conservancy (SFC) 合作治理
- **资金来源**：社区捐款、企业赞助（Re-Logic / Terraria 开发商等）
- **月支出**：约 $40,000，养 10 名全/兼职合同工
- **面临挑战**：支出增长快于收入；AI 生成低质量 PR 增加维护负担（2026年初报道）

### 5.3 Asset Library（插件市场）

- **总量**：约 4,746 项（2026年3月）
- **全部免费**：官方 Asset Library 不支持付费资源
- **2025年推出 Asset Store（beta）**：计划引入付费资源
- 第三方市场（itch.io、Gumroad）提供更多资源

**热门插件：**

| 插件 | 类别 | 说明 |
|------|------|------|
| Godot Jolt | 物理 | Jolt 物理引擎，4.4+ 默认 |
| Terrain3D | 3D 地形 | C++ GDExtension 高性能地形 |
| GodotSteam | 平台集成 | Steamworks SDK 完整集成 |
| LimboAI | AI/游戏逻辑 | 行为树 + 状态机框架 |
| Dialogue Manager | 叙事 | 分支对话编辑器和运行时 |
| GUT | 测试 | GDScript 单元测试框架 |

### 5.4 文档质量

- **官方文档**：质量优秀，与引擎源码自动同步，持续更新
- **编辑器内文档**：可直接查阅所有 API 文档，无需离开 IDE
- **社区用户注释**：受 PHP 文档启发的社区注释系统
- **教程生态**：GDQuest（领先教程提供者）、Brackeys 等大量 YouTube 教程
- **评估**：初学者优秀，中级足够，高级 3D 领域偏弱

### 5.5 代表性商业游戏

| 游戏 | 类型 | 成绩 |
|------|------|------|
| **Brotato** | Roguelike 射击 | 1000万+ 销量，Overwhelmingly Positive |
| **Dome Keeper** | 塔防/挖矿 | $6.1M 收入，Very Positive |
| **Backpack Battles** | 自走棋 | $5.2M，17K+ 评测，91% 好评 |
| **Until Then** | 叙事冒险 | $5.1M，12K 评测，97% 好评 |
| **Cassette Beasts** | 回合制 RPG | $4.1M，9.8K 评测，95% 好评 |
| **Halls of Torment** | 幸存者类 | Steam 热销 |
| **Sonic Colors: Ultimate** | 3D 平台 | AAA 移植（Blind Squirrel） |

### 5.6 版本演进

| 版本 | 发布时间 | 重大变化 |
|------|----------|----------|
| 4.0 | 2023.03 | Vulkan 渲染器、GDExtension、引擎重写 |
| 4.1 | 2023.07 | 稳定性/性能优化 |
| 4.2 | 2023.11 | 更多渲染特性、平台支持 |
| 4.3 | 2024.08 | GPU 同步、合成器效果、动画重定向 |
| 4.4 | 2025.03 | Jolt 物理引擎集成（默认） |
| 4.5 | 2025.09 | 模板缓冲、TileMapLayer 碰撞重做 |
| 4.6 | 2026.01 | LibGodot（可作为独立库构建）、新默认主题 |
| 4.6.1 | 2026.02 | 关键修复 |

发布节奏：约 **4-6 个月一个小版本**，补丁版本数周内发布。

---

## 六、结论与建议

### 6.1 核心结论

**Godot 是当前最适合 AI/LLM 驱动游戏开发的引擎**，核心原因：

| 优势 | 说明 |
|------|------|
| 文本优先 | .tscn/.tres/.gd 全部纯文本，LLM 可直接生成 |
| CLI 完善 | 无头模式全流程自动化：导入→验证→导出 |
| 开源零成本 | MIT 许可，无收入分成，无许可证风险 |
| 轻量高效 | ~100MB，秒级启动，适合高频 AI 迭代 |
| GDScript 友好 | 类 Python 语法，LLM 生成质量高 |

### 6.2 推荐策略

| 场景 | 推荐引擎 | 理由 |
|------|----------|------|
| AI 驱动 2D 游戏 | **Godot** | 最佳选择：文本格式 + CLI + 2D 顶级 |
| AI 驱动 3D 独立游戏 | **Godot** | 可行，3D 能力持续改善 |
| AI 驱动 AAA 3D | Unity/Unreal | Godot 3D 能力不足 |
| 需要内置发布平台 | Roblox | 自带用户群，但自动化能力差 |
| 最大化自动化程度 | **Godot** | CLI 最完善，文件格式最友好 |

### 6.3 风险提示

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 基金会资金压力 | 中 | 关注捐款趋势，Godot 开源保底 |
| 3D 能力不足 | 中 | 专注 2D/风格化 3D，避免写实 AAA |
| GDScript 语法错误 | 中 | CLI 验证 + RAG 增强 + 微调模型 |
| 主机发布门槛 | 中 | W4 Games 合作或先发 PC/Mobile/Web |
| 复杂游戏逻辑生成 | 高 | 分解为小模块，分步迭代 |
| 美术资源自动化 | 高 | 结合图像生成 AI（DALL-E/SD） |

---

## Sources

- [Godot Engine Official Site](https://godotengine.org/)
- [GDScript Reference (Godot 4.4)](https://docs.godotengine.org/en/4.4/tutorials/scripting/gdscript/gdscript_basics.html)
- [C#/.NET in Godot](https://docs.godotengine.org/en/stable/tutorials/scripting/c_sharp/index.html)
- [GDExtension Documentation](https://docs.godotengine.org/en/stable/tutorials/scripting/gdextension/what_is_gdextension.html)
- [TSCN File Format](https://docs.godotengine.org/en/4.4/contributing/development/file_formats/tscn.html)
- [Command Line Tutorial](https://docs.godotengine.org/en/4.4/tutorials/editor/command_line_tutorial.html)
- [Exporting for Dedicated Servers](https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_dedicated_servers.html)
- [Console Support – Godot Engine](https://godotengine.org/consoles/)
- [W4 Games](https://www.w4games.com/)
- [godot-ci — Docker Export Templates](https://github.com/abarichello/godot-ci)
- [Godot CI/CD Guide (Codemagic)](https://blog.codemagic.io/godot-games-cicd/)
- [Automating Godot Builds (TeamCity)](https://blog.jetbrains.com/teamcity/2024/10/automating-godot-game-builds-with-teamcity/)
- [GDScript vs C# in Godot 4](https://chickensoft.games/blog/gdscript-vs-csharp)
- [Godot Copilot — GitHub](https://github.com/minosvasilias/godot-copilot)
- [Godot LLM Framework — Asset Library](https://godotengine.org/asset-library/asset/3282)
- [AI Assistant Hub — Asset Library](https://godotengine.org/asset-library/asset/3427)
- [godot-dodo: LLM Finetuning for GDScript](https://github.com/minosvasilias/godot-dodo)
- [Text-based Development with Godot & LLM (DevelopersIO)](https://dev.classmethod.jp/en/articles/godot-text-based-development-with-llm/)
- [Running Local LLMs in Godot + Ollama](https://dev.to/ykbmck/running-local-llms-in-game-engines-heres-my-journey-with-godot-ollama-4hhd)
- [Godot Development Fund](https://fund.godotengine.org/)
- [Godot Foundation](https://godot.foundation/)
- [Godot Foundation Funding Breakdown](https://godotengine.org/article/funding-breakdown-and-hiring-process/)
- [Game Engine Showdown 2025 (itch.io)](https://itch.io/blog/1067028/game-engine-showdown-2025-unity-vs-godot-vs-unreal-which-should-you-choose)
- [Godot Engine Review 2025 (ThinkGamerz)](https://www.thinkgamerz.com/godot-engine-review-2025/)
- [Most Successful Godot Games (GodotAwesome)](https://godotawesome.com/godot-games-table/)
- [Godot AI Slop Issues (PC Gamer)](https://www.pcgamer.com/software/platforms/open-source-game-engine-godot-is-drowning-in-ai-slop-code-contributions-i-dont-know-how-long-we-can-keep-it-up/)
- [Godot 4.6 Release Notes](https://godotengine.org/releases/4.6/)
- [godot-cpp Official C++ Bindings](https://github.com/godotengine/godot-cpp)
- [godot-rust/gdext — Rust Bindings](https://github.com/godot-rust/gdext)

---

*本报告基于 2026年3月公开信息编写。Godot 引擎发展迅速，建议定期更新。*
