import CoreGraphics

struct EntitySpec {
    enum Kind {
        case pig
        case block
    }

    let kind: Kind
    let position: CGPoint
    let size: CGSize // pig uses size.width as diameter
    let health: CGFloat
}

struct Level {
    let name: String
    let pigs: [EntitySpec]
    let blocks: [EntitySpec]

    static let all: [Level] = [
        Level(
            name: "Tutorial",
            pigs: [
                .init(kind: .pig, position: CGPoint(x: 760, y: 170), size: CGSize(width: 34, height: 34), health: 30)
            ],
            blocks: [
                .init(kind: .block, position: CGPoint(x: 760, y: 130), size: CGSize(width: 90, height: 18), health: 35)
            ]
        ),
        Level(
            name: "Stack",
            pigs: [
                .init(kind: .pig, position: CGPoint(x: 800, y: 205), size: CGSize(width: 34, height: 34), health: 35),
                .init(kind: .pig, position: CGPoint(x: 845, y: 205), size: CGSize(width: 34, height: 34), health: 35)
            ],
            blocks: [
                .init(kind: .block, position: CGPoint(x: 820, y: 140), size: CGSize(width: 140, height: 18), health: 45),
                .init(kind: .block, position: CGPoint(x: 780, y: 175), size: CGSize(width: 18, height: 90), health: 40),
                .init(kind: .block, position: CGPoint(x: 860, y: 175), size: CGSize(width: 18, height: 90), health: 40),
                .init(kind: .block, position: CGPoint(x: 820, y: 230), size: CGSize(width: 160, height: 18), health: 50)
            ]
        ),
        Level(
            name: "Fort",
            pigs: [
                .init(kind: .pig, position: CGPoint(x: 860, y: 220), size: CGSize(width: 34, height: 34), health: 35),
                .init(kind: .pig, position: CGPoint(x: 900, y: 180), size: CGSize(width: 34, height: 34), health: 35),
                .init(kind: .pig, position: CGPoint(x: 820, y: 180), size: CGSize(width: 34, height: 34), health: 35)
            ],
            blocks: [
                .init(kind: .block, position: CGPoint(x: 860, y: 130), size: CGSize(width: 200, height: 18), health: 55),
                .init(kind: .block, position: CGPoint(x: 770, y: 170), size: CGSize(width: 18, height: 110), health: 45),
                .init(kind: .block, position: CGPoint(x: 950, y: 170), size: CGSize(width: 18, height: 110), health: 45),
                .init(kind: .block, position: CGPoint(x: 860, y: 230), size: CGSize(width: 220, height: 18), health: 55),
                .init(kind: .block, position: CGPoint(x: 860, y: 290), size: CGSize(width: 120, height: 18), health: 40)
            ]
        )
    ]
}
