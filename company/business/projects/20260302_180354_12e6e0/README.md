# macOS Angry Birds MVP (Swift + SpriteKit)

本项目交付一个可在 **macOS** 上运行的《愤怒的小鸟》玩法 MVP，优先采用 **Swift + SpriteKit**（原生），目标是：弹弓拖拽发射 + 物理碰撞 + 简单胜负判定 + 单关卡重开。

## 1. 技术栈
- Swift 5.9+
- SwiftUI（UI + HUD）
- SpriteKit（场景渲染 + 2D 物理）
- Swift Package（Xcode 可直接打开 `Package.swift` 运行）

## 2. 如何运行
1. 用 Xcode 打开本目录下的 `angrybirds_mvp/Package.swift`
2. 选择 scheme：`AngryBirdsMVPApp`
3. 运行到 **My Mac**

> 说明：该 MVP 使用矢量图形（SKShapeNode）绘制鸟/猪/方块，不依赖图片资源。

## 3. 玩法说明
- 鼠标按住小鸟并拖拽（向后拉）
- 松开鼠标发射
- 命中猪 => 胜利
- 小鸟用尽仍未命中 => 失败
- 点击右上角 **Restart** 可重开

## 4. 项目结构
- `Sources/AngryBirdsMVPApp/Game/`：配置、状态机、关卡控制
- `Sources/AngryBirdsMVPApp/Scenes/`：SpriteKit 场景（GameScene）
- `Sources/AngryBirdsMVPApp/Entities/`：鸟/猪/方块等实体节点
- `Sources/AngryBirdsMVPApp/UI/`：SwiftUI 视图（HUD、按钮）
- `Sources/AngryBirdsMVPApp/Utils/`：数学/工具
- `docs/`：技术路线、里程碑、验收自测清单

## 5. 验收自测
见 `docs/acceptance_checklist.md`。
