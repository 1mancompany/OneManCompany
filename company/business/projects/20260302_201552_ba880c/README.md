# FlingBirds（macOS）— SpriteKit 物理弹射 Demo

一款「愤怒小鸟（近似玩法）」的 **macOS** 小型 Demo：拖拽“鸟”蓄力发射，利用物理碰撞击毁目标（圆形“猪”）和结构（方块）。

## 合规与素材声明（重要）
- 本项目 **不包含** 任何 Angry Birds 原版或相似度高的素材（角色贴图、音效、关卡图、字体、Logo 等）。
- 所有可视元素均使用 **SKShapeNode**（圆/矩形）与纯色渲染。

---

## 系统要求
- macOS 12.0+
- Xcode 15+（建议）

---

## 如何运行（Xcode）
1. 用 Xcode 打开本目录下：`FlingBirds.xcodeproj`
2. 选择 scheme：`FlingBirds`
3. 选择运行目标：`My Mac`
4. 点击 **Run**

如果首次运行遇到签名问题：
- Xcode → Target Signing & Capabilities → 勾选 *Automatically manage signing*，选择你的个人 Team。

---

## 玩法说明
- 在左侧弹弓位置的圆形“鸟”上按住鼠标：
  - 拖拽越远，发射越有力（有最大拉伸距离限制）。
  - 松开鼠标即发射。
- 目标：消灭关卡中所有“猪”（绿色圆形）。
- 有限次数尝试：用尽仍有猪存活则失败。

SwiftUI 右上角提供按钮：
- `Restart`：重开当前关卡
- `Prev/Next`：切换关卡

---

## 项目结构
- `FlingBirdsApp.swift`：SwiftUI App 入口
- `ContentView.swift`：SwiftUI 容器 + overlay UI
- `GameState.swift`：可观察状态（关卡、尝试次数、胜负信息）
- `GameScene.swift`：SpriteKit 场景（物理、输入、发射、碰撞伤害、胜负）
- `Level.swift`：关卡数据
- `Info.plist`：最小 macOS plist

---

## 命令行构建（可选）
在工程目录执行：

```bash
xcodebuild -project FlingBirds.xcodeproj -scheme FlingBirds -configuration Debug build
```

---

## 可扩展方向（非必须）
- 加入多只鸟与不同技能
- 加入镜头跟随与更大关卡
- 使用自制（或 CC0）音效与粒子效果

