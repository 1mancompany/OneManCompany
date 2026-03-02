# Milestones / 里程碑

## M0 — 立项与技术路线（Done）
- [x] 确定技术路线：SpriteKit + Swift（macOS）
- [x] 确定验收标准与目录结构

## M1 — 核心可玩性（Done）
- [x] 弹弓：拖拽拉伸、松手发射（冲量）
- [x] SpriteKit 物理：重力、碰撞、弹性/摩擦/阻尼
- [x] 关卡搭建：1 个关卡（硬编码）

## M2 — 可倒塌障碍与胜利条件（Done）
- [x] 障碍物两种材质/耐久：Wood / Stone（HP 不同）
- [x] 碰撞伤害：基于 collisionImpulse 扣血
- [x] 胜利条件：所有 Pig **HP<=0 或掉出地图** -> Victory
- [x] 重开：R 键

## M3 — 交付与验收材料（Partial）
- [x] README：构建/运行说明
- [x] 构建脚本：`scripts/build_app.sh` 生成 `.app`
- [x] 资源许可：`docs/ASSET_LICENSES.md`
- [ ] 截图/录屏：请将 `screenshot-*.png`、`demo.mov/gif` 放入 `docs/media/`

## 建议的验收步骤
1. `bash scripts/run_debug.sh`（确认可玩）
2. `bash scripts/build_app.sh && open build/AngryBirdsLite.app`
3. 截图与录屏保存到 `docs/media/`
