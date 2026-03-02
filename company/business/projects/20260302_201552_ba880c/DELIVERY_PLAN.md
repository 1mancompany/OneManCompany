# macOS 版「愤怒小鸟（近似玩法）」交付与验收组织（方案 + 里程碑）

> 项目目标：在 **macOS** 上实现一款「弹弓发射-物理破坏-消灭目标」的 **近似玩法** Demo。
> 技术建议：**SwiftUI + SpriteKit**（渲染/输入/物理/碰撞）。
> 合规要求：**不使用任何疑似侵权素材**（角色/音效/关卡/字体/图标等）。本项目全部使用 **程序化几何图形（SKShapeNode）+ 纯色**。

---

## 1. 实现方案（技术架构）

### 1.1 总体结构
- **SwiftUI App**：负责窗口、基础 UI（关卡信息、按钮）。
- **SpriteKit (SKScene)**：承载游戏循环、物理世界、节点管理、输入（鼠标拖拽）。

模块拆分：
- `ContentView.swift`：SwiftUI 容器，创建并持有 `GameState` 与 `GameScene`。
- `GameState.swift`：可观察状态（当前关卡、剩余尝试次数、胜负状态），用于 SwiftUI overlay。
- `GameScene.swift`：核心玩法（弹弓拖拽、发射、碰撞伤害、胜负判断、重置）。
- `Level.swift`：关卡数据（猪/方块的初始位置与尺寸/生命值/材质参数）。

### 1.2 关键机制设计
**A. 弹弓/发射（拖拽-蓄力-释放）**
- 左侧固定一个 `anchorPoint`（弹弓中心点）。
- “小鸟”初始放在 anchor 上：
  - 未发射时：`isDynamic = false`（跟随鼠标拖拽）。
  - 释放时：计算 `impulse = (anchor - currentDragPoint) * power`，设置 `isDynamic = true`，施加 `applyImpulse`。
- 拖拽范围：限制最大半径 `maxStretch`，避免无穷大力量。
- 视觉辅助：用 `SKShapeNode` 画一条“拉伸线”。

**B. 物理/碰撞/伤害**
- SpriteKit 内置物理：重力、碰撞、摩擦、反弹。
- `SKPhysicsContactDelegate` 监听碰撞：
  - 根据相对速度（或接触冲量近似）计算伤害。
  - 猪（目标）有 `health`，<=0 则移除。
  - 方块（结构）同样可损坏（演示破坏感）。

**C. 关卡系统**
- `Level` 数据包含：
  - `pigs: [EntitySpec]`（圆形）
  - `blocks: [EntitySpec]`（矩形）
- 提供 3 个示例关卡：从易到难。

**D. 胜负条件**
- 胜利：关卡内所有 pigs 被消灭/移出场景。
- 失败：尝试次数耗尽且仍有 pigs 存活。

### 1.3 macOS 输入适配
- `GameScene` 重写：`mouseDown/mouseDragged/mouseUp` 实现拖拽瞄准。

---

## 2. 里程碑拆分（Milestones）

### M1 — 物理世界与基础可玩（Physics）
交付内容：
- SpriteKit 场景搭建（重力、地面边界）
- Bird 物理体（圆形）
- 基础拖拽/释放/冲量发射
- 摄像机（可选，本 demo 固定视角）

验收点：
- 鼠标拖拽发射可稳定复现；发射后物体受重力与碰撞影响

### M2 — 关卡与目标（Level）
交付内容：
- Level 数据结构
- Pig/Block 节点生成
- 胜利判断（消灭全部 pigs）

验收点：
- 能切换至少 3 个关卡；每关能正确判断胜利

### M3 — 伤害/破坏反馈（Gameplay Polish）
交付内容：
- 碰撞伤害计算
- Pig/Block 生命值与移除动画
- 尝试次数与失败判断

验收点：
- 强碰撞能击毁目标；尝试次数耗尽判负

### M4 — 构建产物与文档（Build + README）
交付内容：
- 可打开/可运行的 Xcode 工程
- `README.md`（构建步骤、玩法说明、非侵权声明、结构说明）

验收点：
- 从 workspace 打开工程，Xcode 直接 Run，能玩到完整 loop（发射-击中-胜负/重开/换关）

---

## 3. 验收清单（建议作为最终验收标准）
1. **工程可运行**：Xcode（推荐 15+）打开工程可直接 Run（macOS 12+）。
2. **核心玩法闭环**：拖拽蓄力 → 发射 → 碰撞破坏 → 胜利/失败 → 重开/下一关。
3. **关卡**：至少 3 个可切换关卡。
4. **合规**：无任何 Angry Birds 原版素材/角色/关卡图/音效；全部用几何形状与纯色。
5. **文档完整**：README 写清楚构建/运行步骤与玩法说明。

