import SwiftUI
import SpriteKit

struct GameRootView: View {
    @StateObject private var gameState = GameState()
    @State private var scene: GameScene = GameScene(size: CGSize(width: 1000, height: 650), gameState: GameState())

    init() {
        // 这里不能直接引用 @StateObject，因此在 onAppear 中重建 scene。
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            GeometryReader { geo in
                SpriteView(scene: scene)
                    .ignoresSafeArea()
                    .onAppear {
                        // 用同一个 gameState 重建 scene，保证 HUD 与场景状态一致。
                        let newScene = GameScene(size: geo.size, gameState: gameState)
                        newScene.scaleMode = .resizeFill
                        self.scene = newScene
                    }
                    .onChange(of: geo.size) { _, newSize in
                        scene.size = newSize
                    }
            }

            hud
        }
    }

    private var hud: some View {
        VStack(alignment: .trailing, spacing: 10) {
            HStack(spacing: 12) {
                Text("Birds: \(gameState.remainingBirds)")
                    .font(.system(size: 14, weight: .semibold))

                Button("Restart") {
                    let newScene = GameScene(size: scene.size, gameState: gameState)
                    newScene.scaleMode = .resizeFill
                    self.scene = newScene
                    gameState.resetForNewGame()
                }
            }

            Text(gameState.statusText)
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(gameState.statusColor)

            Spacer()
        }
        .padding(16)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .padding(12)
    }
}
