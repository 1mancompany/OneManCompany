// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AngryBirdsLite",
    platforms: [
        .macOS(.v12)
    ],
    products: [
        .executable(name: "AngryBirdsLite", targets: ["AngryBirdsLite"]) 
    ],
    targets: [
        .executableTarget(
            name: "AngryBirdsLite",
            linkerSettings: [
                // Ensure SpriteKit/AppKit frameworks are linked
                .linkedFramework("AppKit"),
                .linkedFramework("SpriteKit")
            ]
        )
    ]
)
