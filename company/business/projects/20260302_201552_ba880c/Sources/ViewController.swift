import Cocoa
import SpriteKit

class ViewController: NSViewController {

    override func loadView() {
        self.view = SKView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
    }

    override func viewDidLoad() {
        super.viewDidLoad()

        if let view = self.view as? SKView {
            let scene = GameScene(size: view.bounds.size)
            scene.scaleMode = .aspectFill
            
            view.presentScene(scene)
            
            view.ignoresSiblingOrder = true
            view.showsFPS = true
            view.showsNodeCount = true
            // view.showsPhysics = true // 取消注释以显示物理轮廓调试
        }
    }
}