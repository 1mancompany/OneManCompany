import SpriteKit
import SwiftUI

final class GameScene: SKScene, SKPhysicsContactDelegate {
    // MARK: - Types

    private enum Category {
        static let bird: UInt32  = 1 << 0
        static let pig: UInt32   = 1 << 1
        static let block: UInt32 = 1 << 2
        static let world: UInt32 = 1 << 3
    }

    private final class HealthComponent {
        var hp: CGFloat
        init(_ hp: CGFloat) { self.hp = hp }
    }

    // MARK: - State

    private let anchorPointWorld = CGPoint(x: 180, y: 210)
    private let maxStretch: CGFloat = 110
    private let power: CGFloat = 0.9

    private unowned let state: GameState

    private var bird: SKShapeNode?
    private var aimLine: SKShapeNode?

    private var dragging = false
    private var hasLaunched = false

    // track entities
    private var pigs: [SKNode] = []

    // MARK: - Init

    init(size: CGSize, state: GameState) {
        self.state = state
        super.init(size: size)
    }

    required init?(coder aDecoder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    // MARK: - Lifecycle

    override func didMove(to view: SKView) {
        backgroundColor = NSColor(calibratedRed: 0.12, green: 0.13, blue: 0.18, alpha: 1)

        physicsWorld.gravity = CGVector(dx: 0, dy: -9.8)
        physicsWorld.contactDelegate = self

        buildWorldBounds()
        addSlingshotMarker()
        loadCurrentLevel()
    }

    func resize(to newSize: CGSize) {
        self.size = newSize
        buildWorldBounds()
    }

    // MARK: - World

    private func buildWorldBounds() {
        // Remove existing world bounds (if any)
        childNode(withName: "worldBounds")?.removeFromParent()

        let bounds = SKNode()
        bounds.name = "worldBounds"

        // Ground line at y = 90
        let groundY: CGFloat = 90
        let ground = SKNode()
        ground.name = "ground"
        ground.position = .zero
        ground.physicsBody = SKPhysicsBody(edgeFrom: CGPoint(x: -2000, y: groundY), to: CGPoint(x: 5000, y: groundY))
        ground.physicsBody?.categoryBitMask = Category.world
        ground.physicsBody?.contactTestBitMask = Category.bird | Category.pig | Category.block
        ground.physicsBody?.collisionBitMask = Category.bird | Category.pig | Category.block
        bounds.addChild(ground)

        // A back wall to prevent going too far left
        let leftWall = SKNode()
        leftWall.physicsBody = SKPhysicsBody(edgeFrom: CGPoint(x: 40, y: groundY), to: CGPoint(x: 40, y: 2000))
        leftWall.physicsBody?.categoryBitMask = Category.world
        leftWall.physicsBody?.collisionBitMask = Category.bird | Category.pig | Category.block
        bounds.addChild(leftWall)

        addChild(bounds)

        // Visual ground
        childNode(withName: "groundVisual")?.removeFromParent()
        let groundVisual = SKShapeNode(rectOf: CGSize(width: 6000, height: 10))
        groundVisual.name = "groundVisual"
        groundVisual.fillColor = NSColor(calibratedRed: 0.20, green: 0.22, blue: 0.28, alpha: 1)
        groundVisual.strokeColor = .clear
        groundVisual.position = CGPoint(x: 1500, y: groundY - 6)
        groundVisual.zPosition = -1
        addChild(groundVisual)
    }

    private func addSlingshotMarker() {
        childNode(withName: "sling")?.removeFromParent()

        let base = SKShapeNode(circleOfRadius: 10)
        base.name = "sling"
        base.fillColor = NSColor(calibratedRed: 0.85, green: 0.65, blue: 0.35, alpha: 1)
        base.strokeColor = NSColor(calibratedWhite: 1, alpha: 0.15)
        base.position = anchorPointWorld
        base.zPosition = 5
        addChild(base)
    }

    // MARK: - Level

    func loadCurrentLevel() {
        restartLevel()
    }

    func restartLevel() {
        removeAllChildren()
        pigs.removeAll()

        // rebuild base
        backgroundColor = NSColor(calibratedRed: 0.12, green: 0.13, blue: 0.18, alpha: 1)
        buildWorldBounds()
        addSlingshotMarker()

        state.resetForLevel()
        state.bannerText = "Level: \(Level.all[state.levelIndex].name)"

        spawnEntities(for: Level.all[state.levelIndex])
        spawnBirdReady()

        dragging = false
        hasLaunched = false

        // clear banner after a short moment
        run(.sequence([
            .wait(forDuration: 1.0),
            .run { [weak self] in
                if self?.state.bannerText?.hasPrefix("Level:") == true {
                    self?.state.bannerText = nil
                }
            }
        ]))
    }

    private func spawnEntities(for level: Level) {
        // blocks
        for spec in level.blocks {
            let node = SKShapeNode(rectOf: spec.size, cornerRadius: 3)
            node.position = spec.position
            node.fillColor = NSColor(calibratedRed: 0.70, green: 0.72, blue: 0.78, alpha: 1)
            node.strokeColor = NSColor(calibratedWhite: 0, alpha: 0.25)
            node.lineWidth = 1
            node.zPosition = 2

            node.physicsBody = SKPhysicsBody(rectangleOf: spec.size)
            node.physicsBody?.affectedByGravity = true
            node.physicsBody?.allowsRotation = true
            node.physicsBody?.mass = 2.8
            node.physicsBody?.restitution = 0.15
            node.physicsBody?.friction = 0.7
            node.physicsBody?.linearDamping = 0.25

            node.physicsBody?.categoryBitMask = Category.block
            node.physicsBody?.contactTestBitMask = Category.bird | Category.pig | Category.block
            node.physicsBody?.collisionBitMask = Category.world | Category.bird | Category.pig | Category.block

            node.userData = ["health": HealthComponent(spec.health)]

            addChild(node)
        }

        // pigs
        for spec in level.pigs {
            let radius = max(10, spec.size.width / 2)
            let node = SKShapeNode(circleOfRadius: radius)
            node.position = spec.position
            node.fillColor = NSColor(calibratedRed: 0.25, green: 0.85, blue: 0.45, alpha: 1)
            node.strokeColor = NSColor(calibratedWhite: 0, alpha: 0.25)
            node.lineWidth = 1
            node.zPosition = 3

            node.physicsBody = SKPhysicsBody(circleOfRadius: radius)
            node.physicsBody?.affectedByGravity = true
            node.physicsBody?.allowsRotation = true
            node.physicsBody?.mass = 1.2
            node.physicsBody?.restitution = 0.20
            node.physicsBody?.friction = 0.6
            node.physicsBody?.linearDamping = 0.2

            node.physicsBody?.categoryBitMask = Category.pig
            node.physicsBody?.contactTestBitMask = Category.bird | Category.block | Category.world
            node.physicsBody?.collisionBitMask = Category.world | Category.bird | Category.block | Category.pig

            node.userData = ["health": HealthComponent(spec.health)]
            addChild(node)
            pigs.append(node)
        }
    }

    // MARK: - Bird

    private func spawnBirdReady() {
        aimLine?.removeFromParent()

        let node = SKShapeNode(circleOfRadius: 16)
        node.name = "bird"
        node.position = anchorPointWorld
        node.fillColor = NSColor(calibratedRed: 0.95, green: 0.35, blue: 0.30, alpha: 1)
        node.strokeColor = NSColor(calibratedWhite: 0, alpha: 0.25)
        node.lineWidth = 1
        node.zPosition = 10

        node.physicsBody = SKPhysicsBody(circleOfRadius: 16)
        node.physicsBody?.isDynamic = false // ready state
        node.physicsBody?.mass = 1.0
        node.physicsBody?.restitution = 0.25
        node.physicsBody?.friction = 0.55
        node.physicsBody?.linearDamping = 0.35

        node.physicsBody?.categoryBitMask = Category.bird
        node.physicsBody?.contactTestBitMask = Category.pig | Category.block | Category.world
        node.physicsBody?.collisionBitMask = Category.world | Category.pig | Category.block

        addChild(node)
        bird = node

        // aim line
        let line = SKShapeNode()
        line.zPosition = 9
        line.strokeColor = NSColor(calibratedWhite: 1, alpha: 0.35)
        line.lineWidth = 2
        addChild(line)
        aimLine = line
        updateAimLine(to: anchorPointWorld)
    }

    private func updateAimLine(to dragPoint: CGPoint) {
        guard let aimLine else { return }
        let p = CGMutablePath()
        p.move(to: anchorPointWorld)
        p.addLine(to: dragPoint)
        aimLine.path = p
    }

    private func clampDragPoint(_ point: CGPoint) -> CGPoint {
        let dx = point.x - anchorPointWorld.x
        let dy = point.y - anchorPointWorld.y
        let dist = max(0.0001, hypot(dx, dy))
        if dist <= maxStretch { return point }
        let scale = maxStretch / dist
        return CGPoint(x: anchorPointWorld.x + dx * scale, y: anchorPointWorld.y + dy * scale)
    }

    // MARK: - Input (macOS)

    override func mouseDown(with event: NSEvent) {
        guard let bird, !hasLaunched else { return }
        let p = event.location(in: self)
        if bird.contains(p) {
            dragging = true
        }
    }

    override func mouseDragged(with event: NSEvent) {
        guard dragging, let bird, !hasLaunched else { return }
        let raw = event.location(in: self)
        let clamped = clampDragPoint(raw)
        bird.position = clamped
        updateAimLine(to: clamped)
    }

    override func mouseUp(with event: NSEvent) {
        guard dragging, let bird, !hasLaunched else { return }
        dragging = false

        let releasePoint = bird.position
        let dx = anchorPointWorld.x - releasePoint.x
        let dy = anchorPointWorld.y - releasePoint.y

        // if barely pulled, ignore
        if hypot(dx, dy) < 10 {
            bird.position = anchorPointWorld
            updateAimLine(to: anchorPointWorld)
            return
        }

        hasLaunched = true
        aimLine?.isHidden = true

        bird.physicsBody?.isDynamic = true

        let impulse = CGVector(dx: dx * power, dy: dy * power)
        bird.physicsBody?.applyImpulse(impulse)

        state.attemptsLeft -= 1
        checkLoseConditionIfNeeded()
    }

    // MARK: - Contacts / Damage

    func didBegin(_ contact: SKPhysicsContact) {
        // compute an approximate impact severity based on relative velocity
        let a = contact.bodyA
        let b = contact.bodyB

        let relV = CGVector(dx: a.velocity.dx - b.velocity.dx, dy: a.velocity.dy - b.velocity.dy)
        let speed = hypot(relV.dx, relV.dy)

        // small touches do nothing
        if speed < 40 { return }

        let damage = min(80, speed * 0.18)

        applyDamageIfNeeded(to: a.node, amount: damage)
        applyDamageIfNeeded(to: b.node, amount: damage)

        cleanupIfOutOfBounds()
        checkWinConditionIfNeeded()
    }

    private func applyDamageIfNeeded(to node: SKNode?, amount: CGFloat) {
        guard let node,
              let health = node.userData?["health"] as? HealthComponent
        else { return }

        health.hp -= amount

        // small visual feedback
        if let shape = node as? SKShapeNode {
            let t = max(0, min(1, health.hp / 60))
            // fade toward darker as damaged
            shape.alpha = max(0.35, 0.55 + 0.45 * t)
        }

        if health.hp <= 0 {
            // remove with pop
            node.run(.sequence([
                .scale(to: 1.2, duration: 0.06),
                .fadeOut(withDuration: 0.08),
                .removeFromParent()
            ]))
        }
    }

    // MARK: - Win/Lose

    private func checkWinConditionIfNeeded() {
        pigs = pigs.filter { $0.parent != nil }
        if pigs.isEmpty {
            state.bannerText = "Victory! (press Next)"
        }
    }

    private func checkLoseConditionIfNeeded() {
        // only declare lose if attempts exhausted and not already won
        if state.attemptsLeft <= 0 {
            // Delay: allow last shot to play out, then evaluate
            run(.sequence([
                .wait(forDuration: 2.0),
                .run { [weak self] in
                    guard let self else { return }
                    self.pigs = self.pigs.filter { $0.parent != nil }
                    if !self.pigs.isEmpty {
                        self.state.bannerText = "Defeat (press Restart)"
                    }
                }
            ]))
        }
    }

    // MARK: - Cleanup / Respawn

    override func update(_ currentTime: TimeInterval) {
        cleanupIfOutOfBounds()

        // If bird launched and slowed down, spawn a new bird if attempts remain and not won
        if hasLaunched, let bird {
            let speed = hypot(bird.physicsBody?.velocity.dx ?? 0, bird.physicsBody?.velocity.dy ?? 0)
            let isSleeping = speed < 8
            let farAway = bird.position.x > 2000 || bird.position.y < -200

            if (isSleeping || farAway) {
                // remove old bird
                bird.removeFromParent()
                self.bird = nil

                pigs = pigs.filter { $0.parent != nil }
                let won = pigs.isEmpty

                if !won, state.attemptsLeft > 0 {
                    hasLaunched = false
                    spawnBirdReady()
                }
            }
        }
    }

    private func cleanupIfOutOfBounds() {
        // remove any nodes that fell too far
        enumerateChildNodes(withName: "//*") { node, _ in
            if node.position.y < -600 {
                node.removeFromParent()
            }
        }
    }
}
