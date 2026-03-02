import SwiftUI
import SpriteKit

struct ContentView: View {
    @StateObject private var state = GameState()
    @State private var scene: GameScene? = nil

    var body: some View {
        ZStack(alignment: .topTrailing) {
            GeometryReader { geo in
                SpriteView(scene: makeSceneIfNeeded(size: geo.size))
                    .ignoresSafeArea()
                    .onChange(of: geo.size) { _, newSize in
                        scene?.resize(to: newSize)
                    }
            }

            overlay
                .padding(12)
                .background(.black.opacity(0.35))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .padding(12)
        }
        .frame(minWidth: 980, idealWidth: 1100, minHeight: 620, idealHeight: 720)
    }

    private func makeSceneIfNeeded(size: CGSize) -> SKScene {
        if let scene { return scene }

        let new = GameScene(size: size, state: state)
        new.scaleMode = .resizeFill
        self.scene = new
        return new
    }

    private var overlay: some View {
        VStack(alignment: .trailing, spacing: 10) {
            HStack(spacing: 12) {
                Text("Level: \(state.levelIndex + 1)/\(Level.all.count)")
                Text("Attempts: \(state.attemptsLeft)")
            }
            .font(.system(size: 13, weight: .semibold, design: .monospaced))
            .foregroundStyle(.white)

            if let banner = state.bannerText {
                Text(banner)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.yellow)
            }

            HStack(spacing: 8) {
                Button("Prev") { state.prevLevel() }
                    .keyboardShortcut(.leftArrow, modifiers: [])
                Button("Next") { state.nextLevel() }
                    .keyboardShortcut(.rightArrow, modifiers: [])
                Button("Restart") { state.restartRequested.toggle() }
                    .keyboardShortcut("r", modifiers: [])
            }
            .buttonStyle(.borderedProminent)
            .tint(.blue)
        }
        .onChange(of: state.levelIndex) { _, _ in
            scene?.loadCurrentLevel()
        }
        .onChange(of: state.restartRequested) { _, _ in
            scene?.restartLevel()
        }
    }
}

#Preview {
    ContentView()
}
