// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AngryBirdsMVP",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "AngryBirdsMVPApp", targets: ["AngryBirdsMVPApp"])
    ],
    targets: [
        .executableTarget(
            name: "AngryBirdsMVPApp",
            path: "Sources/AngryBirdsMVPApp",
            resources: [
                .process("Resources")
            ]
        )
    ]
)
