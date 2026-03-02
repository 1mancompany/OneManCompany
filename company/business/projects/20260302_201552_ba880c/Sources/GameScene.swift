import SpriteKit
import GameplayKit

struct PhysicsCategory {
    static let none      : UInt32 = 0
    static let all       : UInt32 = UInt32.max
    static let bird      : UInt32 = 0b1
    static let block     : UInt32 = 0b10
    static let pig       : UInt32 = 0b100
    static let ground    : UInt32 = 0b1000
    static let bounds    : UInt32 = 0b10000
}

class GameScene: SKScene, SKPhysicsContactDelegate {
    
    var bird: SKShapeNode!
    var slingshotOrigin: CGPoint!
    var isDragging = false
    var currentLevel = 1
    
    var pigs: [SKNode] = []
    var blocks: [SKNode] = []
    
    var levelLabel: SKLabelNode!
    
    override func didMove(to view: SKView) {
        physicsWorld.contactDelegate = self
        
        // 设置边界防止物体掉出屏幕无休止下落
        physicsBody = SKPhysicsBody(edgeLoopFrom: frame)
        physicsBody?.categoryBitMask = PhysicsCategory.bounds
        physicsBody?.collisionBitMask = PhysicsCategory.all
        
        setupUI()
        setupGround()
        setupSlingshot()
        loadLevel(currentLevel)
    }
    
    func setupUI() {
        levelLabel = SKLabelNode(fontNamed: "Arial-BoldMT")
        levelLabel.fontSize = 24
        levelLabel.fontColor = .white
        levelLabel.position = CGPoint(x: frame.midX, y: frame.maxY - 50)
        addChild(levelLabel)
    }
    
    func setupGround() {
        let groundHeight: CGFloat = 50
        let ground = SKShapeNode(rectOf: CGSize(width: frame.width, height: groundHeight))
        ground.position = CGPoint(x: frame.midX, y: groundHeight / 2)
        ground.fillColor = NSColor(red: 0.2, green: 0.8, blue: 0.2, alpha: 1.0)
        ground.strokeColor = .clear
        
        ground.physicsBody = SKPhysicsBody(rectangleOf: ground.frame.size)
        ground.physicsBody?.isDynamic = false
        ground.physicsBody?.categoryBitMask = PhysicsCategory.ground
        ground.physicsBody?.collisionBitMask = PhysicsCategory.all
        addChild(ground)
    }
    
    func setupSlingshot() {
        slingshotOrigin = CGPoint(x: 200, y: 150)
        
        let slingshotBase = SKShapeNode(rectOf: CGSize(width: 15, height: 100))
        slingshotBase.position = CGPoint(x: 200, y: 100)
        slingshotBase.fillColor = .brown
        slingshotBase.strokeColor = .clear
        addChild(slingshotBase)
        
        spawnBird()
    }
    
    func spawnBird() {
        if bird != nil {
            bird.removeFromParent()
        }
        
        bird = SKShapeNode(circleOfRadius: 15)
        bird.fillColor = .red
        bird.strokeColor = .white
        bird.position = slingshotOrigin
        bird.name = "bird"
        
        bird.physicsBody = SKPhysicsBody(circleOfRadius: 15)
        bird.physicsBody?.isDynamic = false // 在弹弓上时是静态的
        bird.physicsBody?.categoryBitMask = PhysicsCategory.bird
        bird.physicsBody?.contactTestBitMask = PhysicsCategory.pig | PhysicsCategory.block
        bird.physicsBody?.collisionBitMask = PhysicsCategory.all
        bird.physicsBody?.restitution = 0.4
        bird.physicsBody?.friction = 0.5
        bird.physicsBody?.mass = 0.2 // 明确设置质量
        
        addChild(bird)
    }
    
    func loadLevel(_ levelIndex: Int) {
        levelLabel.text = "Level \(levelIndex)"
        
        // 清理旧关卡
        for pig in pigs { pig.removeFromParent() }
        for block in blocks { block.removeFromParent() }
        pigs.removeAll()
        blocks.removeAll()
        
        // 加载新关卡
        let level = LevelManager.getLevel(levelIndex)
        
        for pigPos in level.pigs {
            let pig = SKShapeNode(circleOfRadius: 15)
            pig.fillColor = .green
            pig.strokeColor = .black
            pig.position = pigPos
            
            pig.physicsBody = SKPhysicsBody(circleOfRadius: 15)
            pig.physicsBody?.isDynamic = true
            pig.physicsBody?.categoryBitMask = PhysicsCategory.pig
            pig.physicsBody?.contactTestBitMask = PhysicsCategory.bird | PhysicsCategory.block | PhysicsCategory.ground
            pig.physicsBody?.collisionBitMask = PhysicsCategory.all
            pig.physicsBody?.restitution = 0.3
            pig.physicsBody?.mass = 0.1
            pig.name = "pig"
            
            addChild(pig)
            pigs.append(pig)
        }
        
        for blockData in level.blocks {
            let block = SKShapeNode(rectOf: blockData.size)
            block.fillColor = .cyan
            block.strokeColor = .blue
            block.position = blockData.position
            
            block.physicsBody = SKPhysicsBody(rectangleOf: blockData.size)
            block.physicsBody?.isDynamic = true
            block.physicsBody?.categoryBitMask = PhysicsCategory.block
            block.physicsBody?.contactTestBitMask = PhysicsCategory.pig | PhysicsCategory.bird
            block.physicsBody?.collisionBitMask = PhysicsCategory.all
            // 根据尺寸动态设置质量
            block.physicsBody?.mass = (blockData.size.width * blockData.size.height) / 1000.0
            
            addChild(block)
            blocks.append(block)
        }
    }
    
    override func mouseDown(with event: NSEvent) {
        let location = event.location(in: self)
        
        // 将点击区域放大以便于抓取
        let hitArea = CGRect(x: bird.position.x - 30, y: bird.position.y - 30, width: 60, height: 60)
        if hitArea.contains(location) && !bird.physicsBody!.isDynamic {
            isDragging = true
        }
    }
    
    override func mouseDragged(with event: NSEvent) {
        if isDragging {
            let location = event.location(in: self)
            
            let dx = location.x - slingshotOrigin.x
            let dy = location.y - slingshotOrigin.y
            let distance = sqrt(dx*dx + dy*dy)
            let maxDistance: CGFloat = 100.0
            
            if distance > maxDistance {
                let angle = atan2(dy, dx)
                bird.position = CGPoint(x: slingshotOrigin.x + cos(angle) * maxDistance,
                                        y: slingshotOrigin.y + sin(angle) * maxDistance)
            } else {
                bird.position = location
            }
        }
    }
    
    override func mouseUp(with event: NSEvent) {
        if isDragging {
            isDragging = false
            
            let dx = slingshotOrigin.x - bird.position.x
            let dy = slingshotOrigin.y - bird.position.y
            
            bird.physicsBody?.isDynamic = true
            
            // 根据拖拽距离施加冲量
            let multiplier: CGFloat = 3.0
            bird.physicsBody?.applyImpulse(CGVector(dx: dx * multiplier, dy: dy * multiplier))
            
            // 准备下一只鸟
            scheduleNextBird()
        }
    }
    
    func scheduleNextBird() {
        run(SKAction.sequence([
            SKAction.wait(forDuration: 5.0),
            SKAction.run { [weak self] in
                guard let self = self else { return }
                // 只有在关卡未完成时才生成新鸟
                if !self.pigs.isEmpty {
                    self.spawnBird()
                }
            }
        ]))
    }
    
    func didBegin(_ contact: SKPhysicsContact) {
        let bodyA = contact.bodyA
        let bodyB = contact.bodyB
        
        handleCollision(bodyA: bodyA, bodyB: bodyB)
    }
    
    func handleCollision(bodyA: SKPhysicsBody, bodyB: SKPhysicsBody) {
        // 如果猪受到足够大的冲击力则被消灭
        if bodyA.categoryBitMask == PhysicsCategory.pig || bodyB.categoryBitMask == PhysicsCategory.pig {
            
            let nodeA = bodyA.node
            let nodeB = bodyB.node
            
            let pigNode = bodyA.categoryBitMask == PhysicsCategory.pig ? nodeA : nodeB
            let otherBody = bodyA.categoryBitMask == PhysicsCategory.pig ? bodyB : bodyA
            
            let velocityX = otherBody.velocity.dx
            let velocityY = otherBody.velocity.dy
            let speed = sqrt(velocityX * velocityX + velocityY * velocityY)
            
            // 被鸟直接击中，或者被高速运动的方块砸中
            if otherBody.categoryBitMask == PhysicsCategory.bird || speed > 100 {
                destroyPig(pigNode)
            }
        }
    }
    
    func destroyPig(_ node: SKNode?) {
        guard let pig = node else { return }
        
        if let index = pigs.firstIndex(of: pig) {
            pigs.remove(at: index)
            
            // 简单的爆炸/消失特效
            let pop = SKShapeNode(circleOfRadius: 20)
            pop.fillColor = .yellow
            pop.position = pig.position
            addChild(pop)
            
            pop.run(SKAction.sequence([
                SKAction.scale(to: 1.5, duration: 0.1),
                SKAction.fadeOut(withDuration: 0.1),
                SKAction.removeFromParent()
            ]))
            
            pig.removeFromParent()
            
            checkWinCondition()
        }
    }
    
    override func update(_ currentTime: TimeInterval) {
        // 清理掉出屏幕的猪
        for pig in pigs {
            if pig.position.y < 0 || pig.position.x > frame.width + 100 {
                destroyPig(pig)
            }
        }
    }
    
    func checkWinCondition() {
        if pigs.isEmpty {
            let winLabel = SKLabelNode(text: "Level Cleared!")
            winLabel.fontName = "Arial-BoldMT"
            winLabel.fontSize = 40
            winLabel.fontColor = .yellow
            winLabel.position = CGPoint(x: frame.midX, y: frame.midY)
            addChild(winLabel)
            
            run(SKAction.sequence([
                SKAction.wait(forDuration: 2.0),
                SKAction.run { [weak self] in
                    guard let self = self else { return }
                    winLabel.removeFromParent()
                    self.currentLevel += 1
                    if self.currentLevel > 2 {
                        self.currentLevel = 1 // 演示版循环回到第一关
                    }
                    self.loadLevel(self.currentLevel)
                    self.spawnBird()
                }
            ]))
        }
    }
}