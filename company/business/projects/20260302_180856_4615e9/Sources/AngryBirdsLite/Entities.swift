import Foundation
import SpriteKit

// Note: We keep these types very small and rely on `node.userData` for HP bookkeeping.

final class Bird {
    let node: SKShapeNode

    init(position: CGPoint) {
        node = SKShapeNode(circleOfRadius: Tuning.birdRadius)
        node.position = position
        node.fillColor = .systemRed
        node.strokeColor = .clear
        node.name = "bird"

        let body = SKPhysicsBody(circleOfRadius: Tuning.birdRadius)
        body.affectedByGravity = true
        body.isDynamic = false // becomes dynamic on launch
        body.friction = 0.5
        body.restitution = 0.2
        body.linearDamping = 0.2
        body.angularDamping = 0.3
        body.categoryBitMask = PhysicsCategory.bird
        body.collisionBitMask = PhysicsCategory.ground | PhysicsCategory.block | PhysicsCategory.pig
        body.contactTestBitMask = PhysicsCategory.block | PhysicsCategory.pig
        node.physicsBody = body
    }
}

final class Pig {
    let node: SKShapeNode

    init(position: CGPoint) {
        node = SKShapeNode(circleOfRadius: Tuning.pigRadius)
        node.position = position
        node.fillColor = .systemGreen
        node.strokeColor = .clear
        node.name = "pig"

        let body = SKPhysicsBody(circleOfRadius: Tuning.pigRadius)
        body.affectedByGravity = true
        body.isDynamic = true
        body.friction = 0.7
        body.restitution = 0.1
        body.linearDamping = 0.3
        body.angularDamping = 0.4
        body.categoryBitMask = PhysicsCategory.pig
        body.collisionBitMask = PhysicsCategory.ground | PhysicsCategory.block | PhysicsCategory.bird | PhysicsCategory.pig
        body.contactTestBitMask = PhysicsCategory.bird | PhysicsCategory.block
        node.physicsBody = body

        // SKNode.userData is NSMutableDictionary; avoid assigning Swift Dictionary literal directly.
        node.userData = NSMutableDictionary()
        node.userData?["hp"] = CGFloat(100)
        node.userData?["maxHP"] = CGFloat(100)
    }
}

enum BlockMaterial {
    case wood
    case stone

    var color: SKColor {
        switch self {
        case .wood:
            return SKColor(calibratedRed: 0.72, green: 0.45, blue: 0.20, alpha: 1)
        case .stone:
            return SKColor(calibratedWhite: 0.65, alpha: 1)
        }
    }

    var hp: CGFloat {
        switch self {
        case .wood:  return 80
        case .stone: return 160
        }
    }
}

final class Block {
    let node: SKShapeNode
    let material: BlockMaterial

    init(position: CGPoint, size: CGSize, material: BlockMaterial) {
        self.material = material

        node = SKShapeNode(rectOf: size, cornerRadius: 4)
        node.position = position
        node.fillColor = material.color
        node.strokeColor = .clear
        node.name = "block"

        let body = SKPhysicsBody(rectangleOf: size)
        body.affectedByGravity = true
        body.isDynamic = true
        body.friction = 0.9
        body.restitution = 0.05
        body.linearDamping = 0.2
        body.angularDamping = 0.4
        body.categoryBitMask = PhysicsCategory.block
        body.collisionBitMask = PhysicsCategory.ground | PhysicsCategory.block | PhysicsCategory.bird | PhysicsCategory.pig
        body.contactTestBitMask = PhysicsCategory.bird | PhysicsCategory.pig | PhysicsCategory.block
        node.physicsBody = body

        node.userData = NSMutableDictionary()
        node.userData?["hp"] = material.hp
        node.userData?["maxHP"] = material.hp
    }
}
