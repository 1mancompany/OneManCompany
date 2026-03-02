# COO Implementation Notes (2026-03-02)

背景：原计划派发给工程同事 00008 进行编码，但对方无 agent loop 无法接单。
因此由 COO 直接承担实现与交付兜底。

## 本次实际修改
### 1) 修复：胜利条件可能永远不触发
原实现中 `checkVictory()` 只有在 `didBegin(contact:)` 里调用。
若两只 Pig 只是滚落地图（未产生足够碰撞触发伤害），`pigs` 不会被清空，导致无法显示 Victory。

**改动**：
- 在 `GameScene.update(_:)` 中新增：
  - `cleanupOutOfBounds()`：清理 y < -500 的 pig/block（视为掉出地图/被击倒）
  - 每帧调用 `checkVictory()`
- `checkVictory()` 改为：当 `pigs.isEmpty` 时显示 `Victory! (Press R)`

### 2) 小优化：HUD/胜利文案层级
- 设置 HUD `zPosition`，避免被其他节点遮挡。
- `SKLabelNode(fontNamed: nil)` 以使用系统默认字体，避免指定字体在某些系统下不可用导致回退表现不一致。

## 受影响文件
- `Sources/AngryBirdsLite/GameScene.swift`
- `docs/MILESTONES.md`（文字同步：胜利条件补充“或掉出地图”）

## 自测建议
1. `bash scripts/run_debug.sh`
2. 发射小鸟：
   - 验证可击倒 Pig（碰撞扣血移除）
   - 或将 Pig 撞出屏幕下方（y < -500）后也能胜利
3. 按 `R` 重开。

## 未完成项（需在本机补齐）
- `docs/media/` 下的截图/录屏仍需在本机运行后产出：
  - `screenshot-1.png`（关卡初始）
  - `screenshot-2.png`（命中/倒塌）
  - `demo.mov` 或 `demo.gif`
