import SwiftUI

@main
struct AngryBirdsMVPApp: App {
    var body: some Scene {
        WindowGroup {
            GameRootView()
                .frame(minWidth: 1000, minHeight: 650)
        }
        .windowStyle(.titleBar)
    }
}
