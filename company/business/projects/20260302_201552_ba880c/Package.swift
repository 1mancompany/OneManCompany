// swift-tools-version:5.5
import PackageDescription

let package = Package(
    name: "AngryBirdsClone",
    platforms: [
        .macOS(.v11)
    ],
    targets: [
        .executableTarget(
            name: "AngryBirdsClone",
            dependencies: [],
            path: "Sources"
        )
    ]
)