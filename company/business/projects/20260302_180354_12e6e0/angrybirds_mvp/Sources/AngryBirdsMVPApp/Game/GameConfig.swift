import CoreGraphics

enum GameConfig {
    // MARK: - Gameplay
    static let initialBirdCount: Int = 3

    // MARK: - World
    static let gravity: CGVector = CGVector(dx: 0, dy: -420) // SpriteKit points/s^2
    static let floorY: CGFloat = 120

    // MARK: - Slingshot
    static let slingshotAnchor: CGPoint = CGPoint(x: 210, y: 230)
    static let maxStretch: CGFloat = 120
    static let launchPower: CGFloat = 8.5 // impulse multiplier

    // MARK: - Physics tuning
    static let maxBirdSpeed: CGFloat = 1800
    static let birdRadius: CGFloat = 18
    static let pigRadius: CGFloat = 18
    static let blockSize: CGSize = CGSize(width: 42, height: 42)

    // MARK: - Level layout
    static let levelOriginX: CGFloat = 720
    static let levelBaseY: CGFloat = floorY + 20
}
