import SpriteKit

final class BirdNode: SKShapeNode {
    init(radius: CGFloat) {
        super.init()
        self.path = CGPath(ellipseIn: CGRect(x: -radius, y: -radius, width: radius*2, height: radius*2), transform: nil)
        self.fillColor = .red
        self.strokeColor = .clear
        self.name = "bird"

        let body = SKPhysicsBody(circleOfRadius: radius)
        body.mass = 1.0
        body.friction = 0.5
        body.restitution = 0.3
        body.linearDamping = 0.2
        body.angularDamping = 0.2
        body.usesPreciseCollisionDetection = true

        body.categoryBitMask = PhysicsCategory.bird
        body.collisionBitMask = PhysicsCategory.world | PhysicsCategory.block | PhysicsCategory.pig
        body.contactTestBitMask = PhysicsCategory.pig

        self.physicsBody = body
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
