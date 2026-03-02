import SwiftUI

@MainActor
final class GameState: ObservableObject {
    enum RoundState {
        case idle
        case aiming
        case flying
        case win
        case fail
    }

    @Published var remainingBirds: Int = GameConfig.initialBirdCount
    @Published var roundState: RoundState = .idle

    var statusText: String {
        switch roundState {
        case .idle: return "Drag the bird to aim"
        case .aiming: return "Release to launch"
        case .flying: return ""
        case .win: return "YOU WIN"
        case .fail: return "YOU FAIL"
        }
    }

    var statusColor: Color {
        switch roundState {
        case .win: return .green
        case .fail: return .red
        default: return .primary
        }
    }

    func resetForNewGame() {
        remainingBirds = GameConfig.initialBirdCount
        roundState = .idle
    }
}
