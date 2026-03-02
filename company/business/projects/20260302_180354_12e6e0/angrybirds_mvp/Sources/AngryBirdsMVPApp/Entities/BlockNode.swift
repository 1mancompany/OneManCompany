import SpriteKit

final class BlockNode: SKShapeNode {
    init(size: CGSize) {
        super.init()
        self.path = CGPath(rect: CGRect(x: -size.width/2, y: -size.height/2, width: size.width, height: size.height), transform: nil)
        self.fillColor = .brown
        self.strokeColor = .clear
        self.name = "block"

        let body = SKPhysicsBody(rectangleOf: size)
        body.mass = 2.0
        body.friction = 0.8
        body.restitution = 0.05
        body.linearDamping = 0.25
        body.angularDamping = 0.25

        body.categoryBitMask = PhysicsCategory.block
        body.collisionBitMask = PhysicsCategory.world | PhysicsCategory.block | PhysicsCategory.bird | PhysicsCategory.pig
        body.contactTestBitMask = 0

        self.physicsBody = body
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
