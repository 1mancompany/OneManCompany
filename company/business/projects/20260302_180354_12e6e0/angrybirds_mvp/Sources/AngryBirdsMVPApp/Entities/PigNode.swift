import SpriteKit

final class PigNode: SKShapeNode {
    init(radius: CGFloat) {
        super.init()
        self.path = CGPath(ellipseIn: CGRect(x: -radius, y: -radius, width: radius*2, height: radius*2), transform: nil)
        self.fillColor = .green
        self.strokeColor = .clear
        self.name = "pig"

        let body = SKPhysicsBody(circleOfRadius: radius)
        body.mass = 1.0
        body.friction = 0.6
        body.restitution = 0.2
        body.linearDamping = 0.2
        body.angularDamping = 0.2

        body.categoryBitMask = PhysicsCategory.pig
        body.collisionBitMask = PhysicsCategory.world | PhysicsCategory.block | PhysicsCategory.bird
        body.contactTestBitMask = PhysicsCategory.bird

        self.physicsBody = body
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
