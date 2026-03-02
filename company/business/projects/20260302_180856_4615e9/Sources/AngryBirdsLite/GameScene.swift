import SpriteKit

final class GameScene: SKScene, SKPhysicsContactDelegate {
    private var bird: Bird!
    private var pigs: [Pig] = []
    private var blocks: [Block] = []

    private let slingAnchorNode = SKShapeNode(circleOfRadius: 6)
    private var slingBand: SKShapeNode?

    private var isDraggingBird = false
    private var hasLaunched = false

    private let hudLabel = SKLabelNode(fontNamed: nil)
    private let victoryLabel = SKLabelNode(fontNamed: nil)

    override func didMove(to view: SKView) {
        backgroundColor = SKColor(calibratedRed: 0.78, green: 0.90, blue: 1.0, alpha: 1)

        physicsWorld.gravity = CGVector(dx: 0, dy: -9.8)
        physicsWorld.contactDelegate = self

        loadLevel1()
    }

    // MARK: - Setup

    private func setupGround() {
        let groundSize = CGSize(width: size.width * 3, height: 40)
        let ground = SKShapeNode(rectOf: groundSize, cornerRadius: 0)
        ground.position = CGPoint(x: size.width / 2, y: Tuning.groundY - 20)
        ground.fillColor = SKColor(calibratedRed: 0.35, green: 0.75, blue: 0.35, alpha: 1)
        ground.strokeColor = .clear

        let body = SKPhysicsBody(rectangleOf: groundSize)
        body.isDynamic = false
        body.friction = 1.0
        body.categoryBitMask = PhysicsCategory.ground
        body.collisionBitMask = PhysicsCategory.bird | PhysicsCategory.block | PhysicsCategory.pig
        body.contactTestBitMask = 0
        ground.physicsBody = body
        addChild(ground)

        // Left wall to keep things from flying too far off-screen
        let wall = SKNode()
        wall.position = CGPoint(x: -40, y: size.height / 2)
        let wallBody = SKPhysicsBody(rectangleOf: CGSize(width: 80, height: size.height * 3))
        wallBody.isDynamic = false
        wallBody.categoryBitMask = PhysicsCategory.ground
        wallBody.collisionBitMask = PhysicsCategory.bird | PhysicsCategory.block | PhysicsCategory.pig
        wall.physicsBody = wallBody
        addChild(wall)
    }

    private func setupHUD() {
        hudLabel.fontSize = 16
        hudLabel.fontColor = .black
        hudLabel.horizontalAlignmentMode = .left
        hudLabel.verticalAlignmentMode = .top
        hudLabel.position = CGPoint(x: 20, y: size.height - 20)
        hudLabel.zPosition = 1000
        hudLabel.text = "Drag the bird and release to launch. Press R to restart."
        addChild(hudLabel)

        victoryLabel.fontSize = 48
        victoryLabel.fontColor = .systemBlue
        victoryLabel.horizontalAlignmentMode = .center
        victoryLabel.verticalAlignmentMode = .center
        victoryLabel.position = CGPoint(x: size.width / 2, y: size.height / 2)
        victoryLabel.zPosition = 1001
        victoryLabel.text = ""
        addChild(victoryLabel)
    }

    private func setupSlingVisuals() {
        slingAnchorNode.fillColor = .black
        slingAnchorNode.strokeColor = .clear
        slingAnchorNode.position = Tuning.slingAnchor
        addChild(slingAnchorNode)

        let stand = SKShapeNode(rectOf: CGSize(width: 30, height: 80), cornerRadius: 6)
        stand.position = CGPoint(x: Tuning.slingAnchor.x, y: Tuning.slingAnchor.y - 35)
        stand.fillColor = SKColor(calibratedRed: 0.45, green: 0.28, blue: 0.12, alpha: 1)
        stand.strokeColor = .clear
        addChild(stand)
    }

    // MARK: - Level

    private func loadLevel1() {
        // Clear existing
        removeAllChildren()
        slingBand = nil
        pigs.removeAll()
        blocks.removeAll()
        hasLaunched = false
        isDraggingBird = false

        // Re-add basics
        setupGround()
        setupHUD()
        setupSlingVisuals()

        // Bird
        bird = Bird(position: Tuning.slingAnchor)
        addChild(bird.node)

        // Simple structure: mixed materials
        let baseX: CGFloat = 820
        let baseY: CGFloat = Tuning.groundY + 40

        func addBlock(x: CGFloat, y: CGFloat, w: CGFloat, h: CGFloat, m: BlockMaterial) {
            let b = Block(position: CGPoint(x: x, y: y), size: CGSize(width: w, height: h), material: m)
            blocks.append(b)
            addChild(b.node)
        }

        // Base platform
        addBlock(x: baseX, y: baseY, w: 220, h: 24, m: .stone)

        // Towers
        addBlock(x: baseX - 80, y: baseY + 60, w: 30, h: 100, m: .wood)
        addBlock(x: baseX + 80, y: baseY + 60, w: 30, h: 100, m: .wood)
        addBlock(x: baseX, y: baseY + 120, w: 200, h: 24, m: .stone)

        // Top small blocks
        addBlock(x: baseX - 40, y: baseY + 170, w: 28, h: 60, m: .wood)
        addBlock(x: baseX + 40, y: baseY + 170, w: 28, h: 60, m: .wood)
        addBlock(x: baseX, y: baseY + 210, w: 140, h: 22, m: .stone)

        // Pigs
        func addPig(x: CGFloat, y: CGFloat) {
            let p = Pig(position: CGPoint(x: x, y: y))
            pigs.append(p)
            addChild(p.node)
        }

        addPig(x: baseX, y: baseY + 50)
        addPig(x: baseX, y: baseY + 150)

        victoryLabel.text = ""
    }

    // MARK: - Input (macOS)

    override func mouseDown(with event: NSEvent) {
        guard let view = view else { return }
        if victoryLabel.text?.isEmpty == false { return }

        let location = event.location(in: view)
        let point = convertPoint(fromView: location)

        // Start dragging only if clicking near bird and not launched yet
        if !hasLaunched, bird.node.contains(point) {
            isDraggingBird = true
            drawSlingBand(to: bird.node.position)
        }
    }

    override func mouseDragged(with event: NSEvent) {
        guard let view = view, isDraggingBird, !hasLaunched else { return }

        let location = event.location(in: view)
        let point = convertPoint(fromView: location)

        let clamped = clampVector(from: Tuning.slingAnchor, to: point, maxDistance: Tuning.maxPullDistance)
        bird.node.position = clamped
        drawSlingBand(to: clamped)
    }

    override func mouseUp(with event: NSEvent) {
        guard isDraggingBird, !hasLaunched else { return }
        isDraggingBird = false
        slingBand?.removeFromParent()

        // Launch
        hasLaunched = true
        bird.node.physicsBody?.isDynamic = true

        let dx = Tuning.slingAnchor.x - bird.node.position.x
        let dy = Tuning.slingAnchor.y - bird.node.position.y
        let impulse = CGVector(dx: dx * Tuning.impulseMultiplier, dy: dy * Tuning.impulseMultiplier)
        bird.node.physicsBody?.applyImpulse(impulse)

        // Prevent re-grab
        bird.node.name = "bird_launched"
    }

    override func keyDown(with event: NSEvent) {
        // 'r' or 'R'
        if event.charactersIgnoringModifiers?.lowercased() == "r" {
            loadLevel1()
        }
    }

    private func drawSlingBand(to point: CGPoint) {
        slingBand?.removeFromParent()

        let path = CGMutablePath()
        path.move(to: Tuning.slingAnchor)
        path.addLine(to: point)

        let band = SKShapeNode(path: path)
        band.strokeColor = .black
        band.lineWidth = 3
        band.zPosition = 100
        addChild(band)
        slingBand = band
    }

    // MARK: - Frame update

    override func update(_ currentTime: TimeInterval) {
        cleanupOutOfBounds()
        checkVictory()
    }

    private func cleanupOutOfBounds() {
        // If objects fall far below the screen, remove them to avoid endless simulation.
        let killY: CGFloat = -500

        for p in pigs {
            if p.node.position.y < killY {
                p.node.removeFromParent()
            }
        }
        pigs.removeAll { $0.node.parent == nil }

        for b in blocks {
            if b.node.position.y < killY {
                b.node.removeFromParent()
            }
        }
        blocks.removeAll { $0.node.parent == nil }
    }

    // MARK: - Contacts & Damage

    func didBegin(_ contact: SKPhysicsContact) {
        // Use collision impulse as a proxy for damage.
        let impulse = max(0, contact.collisionImpulse)
        if impulse < 5 { return }

        // Determine which nodes are damageable
        let nodes = [contact.bodyA.node, contact.bodyB.node].compactMap { $0 }
        for n in nodes {
            if n.name == "pig" {
                applyDamage(to: n, base: impulse, scale: 0.9)
            } else if n.name == "block" {
                applyDamage(to: n, base: impulse, scale: 0.6)
            }
        }

        cleanupDestroyed()
        checkVictory()
    }

    private func applyDamage(to node: SKNode, base impulse: CGFloat, scale: CGFloat) {
        // Damage is scaled by impulse; tune to feel right.
        let dmg = impulse * scale
        guard let hp = node.userData?["hp"] as? CGFloat else { return }
        let maxHP = (node.userData?["maxHP"] as? CGFloat) ?? hp

        let newHP = max(0, hp - dmg)
        node.userData?["hp"] = newHP

        // Simple visual feedback: fade as HP decreases
        if let shape = node as? SKShapeNode {
            let alpha = max(0.15, newHP / max(1, maxHP))
            shape.fillColor = shape.fillColor.withAlphaComponent(alpha)
        }
    }

    private func cleanupDestroyed() {
        // Remove pigs/blocks with hp <= 0
        for p in pigs {
            if let hp = p.node.userData?["hp"] as? CGFloat, hp <= 0 {
                p.node.removeFromParent()
            }
        }
        pigs.removeAll { $0.node.parent == nil }

        for b in blocks {
            if let hp = b.node.userData?["hp"] as? CGFloat, hp <= 0 {
                b.node.removeFromParent()
            }
        }
        blocks.removeAll { $0.node.parent == nil }
    }

    private func checkVictory() {
        guard pigs.isEmpty else { return }
        if victoryLabel.text?.isEmpty == true {
            victoryLabel.text = "Victory! (Press R)"
        }
    }
}
