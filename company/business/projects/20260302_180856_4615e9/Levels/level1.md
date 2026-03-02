# Level 1

本版本为最小可玩交付，关卡在代码中硬编码（见 `GameScene.loadLevel1()`）。

布局要点：
- 右侧一个混合材质塔（Wood / Stone），可以在物理碰撞下倒塌
- 2 个 Pig：底层 1 个、上层 1 个

后续扩展建议：
- 用 JSON/YAML 描述 block/pig 的坐标/尺寸/材质
- SwiftPM Resources + Bundle.module 读取关卡文件
