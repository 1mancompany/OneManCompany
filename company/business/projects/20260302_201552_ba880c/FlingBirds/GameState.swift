import Foundation

final class GameState: ObservableObject {
    @Published var levelIndex: Int = 0
    @Published var attemptsLeft: Int = 4
    @Published var bannerText: String? = nil

    // toggles to request an action from SwiftUI → SKScene
    @Published var restartRequested: Bool = false

    func resetForLevel() {
        attemptsLeft = 4
        bannerText = nil
    }

    func nextLevel() {
        levelIndex = min(levelIndex + 1, Level.all.count - 1)
        resetForLevel()
    }

    func prevLevel() {
        levelIndex = max(levelIndex - 1, 0)
        resetForLevel()
    }
}
