# Architecture

## 目标
在最小工程量下实现：
- 弹弓拖拽发射
- SpriteKit 物理碰撞
- 可倒塌障碍（不同材质/血量）
- Pig 击倒胜利

## 模块
- `App.swift`
  - macOS 启动：NSApplication + NSWindow + SKView
- `GameScene.swift`
  - 关卡搭建、输入处理、物理回调、胜利判定
- `Entities.swift`
  - Bird/Pig/Block 的节点构建与基础参数（颜色、物理属性、初始 HP）
- `Physics.swift`
  - Bitmask 与调参（拉伸距离、冲量倍率、地面高度等）

## 关键机制
### 弹弓
- anchor 固定点 `Tuning.slingAnchor`
- 鼠标拖拽时：把 bird 位置 clamp 在最大拉伸半径内
- 松手时：把 `anchor - bird.position` 转为冲量向量并 `applyImpulse`，同时将 bird physicsBody 设为 dynamic

### 可倒塌（简化破坏）
- 使用 `SKPhysicsContact.collisionImpulse` 估算冲击强度
- Block/Pig 使用 `userData["hp"]` 记录血量
- 碰撞时扣血，血量<=0 即移除节点

> 注：这里采取“扣血移除”的简化破坏方式，可进一步扩展为“碎裂成多个小块”。
