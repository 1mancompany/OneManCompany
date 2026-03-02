import Foundation
import CoreGraphics

struct BlockData {
    let position: CGPoint
    let size: CGSize
}

struct Level {
    let id: Int
    let pigs: [CGPoint]
    let blocks: [BlockData]
}

class LevelManager {
    static func getLevel(_ index: Int) -> Level {
        if index == 1 {
            // 第一关：简单结构
            return Level(
                id: 1,
                pigs: [
                    CGPoint(x: 750, y: 100)
                ],
                blocks: [
                    // 两根垂直柱子
                    BlockData(position: CGPoint(x: 700, y: 100), size: CGSize(width: 20, height: 100)),
                    BlockData(position: CGPoint(x: 800, y: 100), size: CGSize(width: 20, height: 100)),
                    // 一个水平屋顶
                    BlockData(position: CGPoint(x: 750, y: 160), size: CGSize(width: 140, height: 20))
                ]
            )
        } else {
            // 第二关：双层结构
            return Level(
                id: 2,
                pigs: [
                    CGPoint(x: 700, y: 100),
                    CGPoint(x: 800, y: 100),
                    CGPoint(x: 750, y: 220)
                ],
                blocks: [
                    // 一楼柱子
                    BlockData(position: CGPoint(x: 650, y: 100), size: CGSize(width: 20, height: 100)),
                    BlockData(position: CGPoint(x: 750, y: 100), size: CGSize(width: 20, height: 100)),
                    BlockData(position: CGPoint(x: 850, y: 100), size: CGSize(width: 20, height: 100)),
                    // 一楼屋顶
                    BlockData(position: CGPoint(x: 700, y: 160), size: CGSize(width: 120, height: 20)),
                    BlockData(position: CGPoint(x: 800, y: 160), size: CGSize(width: 120, height: 20)),
                    // 二楼柱子
                    BlockData(position: CGPoint(x: 700, y: 220), size: CGSize(width: 20, height: 100)),
                    BlockData(position: CGPoint(x: 800, y: 220), size: CGSize(width: 20, height: 100)),
                    // 二楼屋顶
                    BlockData(position: CGPoint(x: 750, y: 280), size: CGSize(width: 140, height: 20))
                ]
            )
        }
    }
}