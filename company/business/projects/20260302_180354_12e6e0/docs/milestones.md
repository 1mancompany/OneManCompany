# 项目里程碑与集成节奏（macOS Angry Birds MVP）

## Milestone 0：工程骨架（0.5 天）
- Swift Package 可在 Xcode 打开并运行
- SwiftUI + SpriteView 显示空场景
- GameConfig / GameState 基础文件就位

## Milestone 1：关卡与物理世界（0.5 天）
- 地面/边界（edge physics body）
- 生成：弹弓锚点、小鸟、方块结构、猪
- bitmask 与 contact delegate 打通

## Milestone 2：弹弓拖拽发射（1 天）
- mouseDown/Dragged/Up 拖拽
- 最大拉伸限制、发射冲量、速度上限
- 发射后回合流转：生成下一只鸟 / 鸟用尽

## Milestone 3：胜负判定 + HUD（0.5 天）
- 鸟-猪接触 => 胜利提示
- 鸟用尽仍未胜利 => 失败提示
- HUD：剩余鸟数、Restart 按钮

## Milestone 4：调参/稳定性兜底（0.5 天）
- 物理参数集中管理（重力、弹性、摩擦、冲量）
- 穿透/抖动兜底：precise collision、速度 clamp
- README + 自测清单

## 集成与验收节奏
- 每完成一个 Milestone 即可跑通一次验收自测清单对应条目
- 最终以 `docs/acceptance_checklist.md` 逐条勾选并记录结果
