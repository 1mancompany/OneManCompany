import SpriteKit

final class GameScene: SKScene, SKPhysicsContactDelegate {
    private weak var gameState: GameState?

    private var currentBird: BirdNode?
    private var isDraggingBird: Bool = false

    // UI helpers
    private let slingshotAnchorNode = SKShapeNode(circleOfRadius: 6)

    init(size: CGSize, gameState: GameState) {
        self.gameState = gameState
        super.init(size: size)
        self.scaleMode = .resizeFill
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func didMove(to view: SKView) {
        backgroundColor = .init(white: 0.93, alpha: 1.0)

        physicsWorld.gravity = GameConfig.gravity
        physicsWorld.contactDelegate = self

        setupWorldBounds()
        setupSlingshotAnchor()
        setupLevel()
        spawnBirdIfNeeded()

        gameState?.roundState = .idle
    }

    // MARK: - Setup

    private func setupWorldBounds() {
        let floorY = GameConfig.floorY

        let bounds = CGRect(x: 0, y: 0, width: size.width, height: size.height)
        let edge = SKPhysicsBody(edgeLoopFrom: bounds)
        edge.categoryBitMask = PhysicsCategory.world
        edge.collisionBitMask = PhysicsCategory.bird | PhysicsCategory.pig | PhysicsCategory.block
        edge.contactTestBitMask = 0

        let world = SKNode()
        world.physicsBody = edge
        addChild(world)

        // visual floor
        let floor = SKShapeNode(rect: CGRect(x: 0, y: 0, width: size.width, height: floorY))
        floor.fillColor = .init(white: 0.86, alpha: 1.0)
        floor.strokeColor = .clear
        floor.zPosition = -1
        addChild(floor)
    }

    private func setupSlingshotAnchor() {
        slingshotAnchorNode.fillColor = .darkGray
        slingshotAnchorNode.strokeColor = .clear
        slingshotAnchorNode.position = GameConfig.slingshotAnchor
        slingshotAnchorNode.zPosition = 10
        addChild(slingshotAnchorNode)
    }

    private func setupLevel() {
        // 简单结构：3 个方块 + 1 个猪
        let baseX = GameConfig.levelOriginX
        let baseY = GameConfig.levelBaseY

        let s = GameConfig.blockSize

        let b1 = BlockNode(size: s)
        b1.position = CGPoint(x: baseX, y: baseY)
        addChild(b1)

        let b2 = BlockNode(size: s)
        b2.position = CGPoint(x: baseX + 55, y: baseY)
        addChild(b2)

        let b3 = BlockNode(size: s)
        b3.position = CGPoint(x: baseX + 27.5, y: baseY + 55)
        addChild(b3)

        let pig = PigNode(radius: GameConfig.pigRadius)
        pig.position = CGPoint(x: baseX + 27.5, y: baseY + 105)
        addChild(pig)
    }

    // MARK: - Bird lifecycle

    private func spawnBirdIfNeeded() {
        guard currentBird == nil else { return }
        guard let gameState else { return }
        guard gameState.roundState != .win && gameState.roundState != .fail else { return }

        guard gameState.remainingBirds > 0 else {
            gameState.roundState = .fail
            return
        }

        let bird = BirdNode(radius: GameConfig.birdRadius)
        bird.position = GameConfig.slingshotAnchor
        bird.zPosition = 20

        // 未发射前不受物理影响
        bird.physicsBody?.isDynamic = false
        bird.physicsBody?.affectedByGravity = false

        addChild(bird)
        currentBird = bird
        gameState.roundState = .idle
    }

    private func launchCurrentBird() {
        guard let bird = currentBird,
              let body = bird.physicsBody,
              let gameState else { return }

        let anchor = GameConfig.slingshotAnchor
        let stretch = (anchor - bird.position).clamped(maxLength: GameConfig.maxStretch)

        body.isDynamic = true
        body.affectedByGravity = true
        body.velocity = .zero
        body.angularVelocity = 0

        body.applyImpulse(stretch * GameConfig.launchPower)

        gameState.remainingBirds -= 1
        gameState.roundState = .flying

        // 允许下一只鸟准备（等当前飞行结束后再 spawn）
    }

    private func endFlightAndPrepareNextBird() {
        currentBird = nil
        isDraggingBird = false
        spawnBirdIfNeeded()
    }

    // MARK: - Input (macOS mouse)

    override func mouseDown(with event: NSEvent) {
        guard let bird = currentBird, let gameState else { return }
        guard gameState.roundState == .idle || gameState.roundState == .aiming else { return }

        let p = event.location(in: self)
        if bird.contains(p) {
            isDraggingBird = true
            gameState.roundState = .aiming
        }
    }

    override func mouseDragged(with event: NSEvent) {
        guard isDraggingBird, let bird = currentBird else { return }

        let p = event.location(in: self)
        let anchor = GameConfig.slingshotAnchor
        let dragVec = (p - anchor).clamped(maxLength: GameConfig.maxStretch)
        bird.position = CGPoint(x: anchor.x + dragVec.dx, y: anchor.y + dragVec.dy)
    }

    override func mouseUp(with event: NSEvent) {
        guard isDraggingBird else { return }
        isDraggingBird = false
        launchCurrentBird()
    }

    // MARK: - Update loop

    override func update(_ currentTime: TimeInterval) {
        guard let gameState else { return }
        guard gameState.roundState == .flying else { return }
        guard let bird = currentBird, let body = bird.physicsBody else { return }

        // 速度上限兜底（避免穿透/抖动）
        let v = body.velocity
        let speed = sqrt(v.dx*v.dx + v.dy*v.dy)
        if speed > GameConfig.maxBirdSpeed {
            let scale = GameConfig.maxBirdSpeed / speed
            body.velocity = CGVector(dx: v.dx * scale, dy: v.dy * scale)
        }

        // 飞出屏幕或基本停止 => 准备下一只鸟
        let out = bird.position.x < -200 || bird.position.x > size.width + 200 || bird.position.y < -200
        let nearlyStopped = speed < 25 && abs(body.angularVelocity) < 1
        if out || nearlyStopped {
            // 稍等一下让碰撞稳定
            run(.wait(forDuration: 0.2)) { [weak self] in
                guard let self else { return }
                if gameState.roundState == .flying { self.endFlightAndPrepareNextBird() }
            }
        }

        // 鸟用尽且没有当前鸟（理论不会发生），兜底失败
        if gameState.remainingBirds == 0 && currentBird == nil && gameState.roundState != .win {
            gameState.roundState = .fail
        }
    }

    // MARK: - Contacts

    func didBegin(_ contact: SKPhysicsContact) {
        guard let gameState else { return }
        guard gameState.roundState != .win && gameState.roundState != .fail else { return }

        let a = contact.bodyA.categoryBitMask
        let b = contact.bodyB.categoryBitMask

        let birdPig = (a == PhysicsCategory.bird && b == PhysicsCategory.pig) || (a == PhysicsCategory.pig && b == PhysicsCategory.bird)
        guard birdPig else { return }

        // 命中即胜（MVP 简化）
        gameState.roundState = .win

        // 可选：移除猪节点形成反馈
        if contact.bodyA.categoryBitMask == PhysicsCategory.pig {
            contact.bodyA.node?.removeFromParent()
        }
        if contact.bodyB.categoryBitMask == PhysicsCategory.pig {
            contact.bodyB.node?.removeFromParent()
        }
    }
}
