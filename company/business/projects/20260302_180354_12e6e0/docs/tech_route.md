# macOS《愤怒的小鸟》MVP 技术路线（推荐原生 Swift + SpriteKit）

## 0. 目标范围（MVP）
- 单关卡固定布局
- 弹弓拖拽发射（鼠标）
- 2D 物理（重力、碰撞、可推动/可倒塌结构）
- 目标（猪）命中判定 + 胜负提示
- HUD：剩余小鸟数、重开按钮

## 1. 技术选型对比
### 方案 A：Swift + SpriteKit（优先）
**优点**
- Apple 原生 2D 引擎，物理与渲染一体化，MVP 落地最快
- Xcode 直接运行，发布/签名链路简单
- 适合单人/小团队快速迭代玩法

**缺点/风险**
- 高速刚体可能出现穿透/抖动（需要速度上限、增大碰撞体、启发式连续检测等兜底）
- 编辑器/关卡工具较弱（MVP 用代码写死布局即可）

### 方案 B：Swift + SceneKit
- 更偏 3D；做 2D 玩法也能实现但不如 SpriteKit 直接
- 若未来要 3D 特效/相机，可考虑，但不作为 MVP 首选

### 方案 C：Unity
**优点**：成熟物理/编辑器/跨平台
**缺点**：工程与包体较重、macOS 原生体验与交付复杂度高，且本需求优先原生

**结论**：MVP 优先 **SpriteKit**。同时在架构上保持“物理层/关卡数据/输入-发射器接口”可替换，必要时可迁移 Unity。

## 2. 架构设计（最小但可扩展）
### 2.1 模块划分
- **UI（SwiftUI）**：HUD（剩余鸟数、胜负、重开）
- **Scene（SpriteKit）**：GameScene 负责渲染、物理世界、输入（mouse）
- **Game（纯 Swift）**：
  - `GameConfig`：统一物理参数与调参入口
  - `GameState`：胜负/回合/剩余鸟数状态（ObservableObject）
- **Entities**：Bird/Pig/Block 节点构造（形状、物理体、bitmask）

### 2.2 关键数据/状态机
- `RoundState`: `.idle`（待发射） / `.aiming`（拖拽中） / `.flying`（已发射） / `.win` / `.fail`
- `remainingBirds`: Int
- `pigsRemaining`: Int（或命中即胜）

### 2.3 碰撞与判定
- 使用 `SKPhysicsContactDelegate`。
- `categoryBitMask` 规划（示例）：
  - bird: 1<<0
  - pig: 1<<1
  - block: 1<<2
  - world: 1<<3
- 规则：bird 与 pig 发生 contact => 胜利（MVP 简化）

### 2.4 弹弓实现
- 鼠标拖拽矢量：`stretch = anchor - bird.position`
- 限制最大拉伸 `maxStretch`
- 松开：对 bird 施加 `applyImpulse(stretch * launchPower)`
- 兜底：限制最大速度；必要时 `usesPreciseCollisionDetection = true`

## 3. 风险与兜底
1. **高速穿透**：
   - `usesPreciseCollisionDetection = true`
   - 限制最大速度（在 `update` 中 clamp velocity）
   - 适当放大碰撞体半径/厚度
2. **物理不稳定**：
   - 合理设置 `linearDamping`、`restitution`、`friction`
   - 降低发射冲量上限
3. **关卡扩展**：
   - MVP 先硬编码；后续引入 JSON/Plist 描述关卡布局

## 4. 里程碑（建议）
见 `docs/milestones.md`。
