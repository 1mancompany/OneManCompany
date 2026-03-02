# AngryBirdsLite (macOS / SpriteKit / Swift)

一个 **可运行的 macOS 简化愤怒小鸟克隆**（弹弓发射、SpriteKit 物理、可倒塌障碍、1 个关卡、胜利判定、一键构建产物 `.app`）。

## 技术路线
- **SpriteKit + Swift（macOS）**
- 理由：
  - macOS 原生 2D 渲染与物理（SpriteKit 内建物理引擎）
  - 不依赖 Unity 编辑器与资源流水线，交付更轻量
  - 便于脚本化构建（`swift build` + 打包 `.app`）

## 目录结构
```
.
├── Package.swift
├── Sources/
│   └── AngryBirdsLite/
│       ├── App.swift
│       ├── GameScene.swift
│       ├── Entities.swift
│       └── Physics.swift
├── scripts/
│   ├── build_app.sh
│   └── run_debug.sh
├── Levels/
│   └── level1.md
├── Resources/
│   └── README.md
└── docs/
    ├── ARCHITECTURE.md
    ├── MILESTONES.md
    ├── ASSET_LICENSES.md
    └── media/
        └── .gitkeep
```

## 运行与构建
### 前置条件
- macOS（建议 12+）
- Xcode Command Line Tools（提供 `swift` / `swift build`）

安装：
```bash
xcode-select --install
```

### Debug 运行（直接跑可执行文件）
```bash
chmod +x scripts/*.sh
bash scripts/run_debug.sh
```

### 一键构建 .app（Release）
```bash
bash scripts/build_app.sh
open build/AngryBirdsLite.app
```

## 操作说明
- **鼠标左键按住小鸟**：拖拽拉弓（有最大拉伸距离限制）
- **松开鼠标**：发射
- **R**：重开当前关卡
- 胜利条件：**所有 Pig HP <= 0 或掉出地图** 后显示 Victory

## 交付证据（截图/录屏）
请将截图/录屏放入：`docs/media/`
- `screenshot-1.png`（关卡初始）
- `screenshot-2.png`（命中/倒塌）
- `demo.mov` 或 `demo.gif`

### 当前状态
- 代码与构建脚本已具备可交付性
- `docs/media/` 尚未提交截图/录屏（需要在本机运行后补充）

> 验收建议：按 `docs/MILESTONES.md` 的步骤跑一遍，然后将录屏/截图补齐。

## 资源许可
见 `docs/ASSET_LICENSES.md`（本项目默认仅使用程序绘制的矢量形状与系统字体，不引入第三方美术/音频资源）。
