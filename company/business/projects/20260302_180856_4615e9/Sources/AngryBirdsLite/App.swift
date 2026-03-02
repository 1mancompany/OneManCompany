import AppKit
import SpriteKit

/// Minimal macOS app bootstrap without Xcode project.
/// We create an NSWindow containing an SKView and present `GameScene`.
@main
final class App: NSObject, NSApplicationDelegate {
    private var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)

        let size = NSSize(width: 1200, height: 700)
        window = NSWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "AngryBirdsLite"
        window.center()

        let skView = SKView(frame: NSRect(origin: .zero, size: size))
        skView.ignoresSiblingOrder = true
        skView.showsFPS = true
        skView.showsNodeCount = true
        skView.showsPhysics = false

        let scene = GameScene(size: size)
        scene.scaleMode = .resizeFill
        skView.presentScene(scene)

        window.contentView = skView
        window.makeKeyAndOrderFront(nil)

        app.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}
