import SpriteKit

enum PhysicsCategory {
    static let none: UInt32   = 0
    static let bird: UInt32   = 1 << 0
    static let pig: UInt32    = 1 << 1
    static let block: UInt32  = 1 << 2
    static let ground: UInt32 = 1 << 3
}

/// Common tuning knobs.
enum Tuning {
    static let slingAnchor = CGPoint(x: 220, y: 220)
    static let maxPullDistance: CGFloat = 120
    static let impulseMultiplier: CGFloat = 6.0

    static let birdRadius: CGFloat = 22
    static let pigRadius: CGFloat = 20

    static let groundY: CGFloat = 120
}

func clampVector(from origin: CGPoint, to target: CGPoint, maxDistance: CGFloat) -> CGPoint {
    let dx = target.x - origin.x
    let dy = target.y - origin.y
    let dist = sqrt(dx*dx + dy*dy)
    guard dist > maxDistance, dist > 0.0001 else { return target }
    let s = maxDistance / dist
    return CGPoint(x: origin.x + dx * s, y: origin.y + dy * s)
}
