// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "SoundGrab",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "SoundGrab",
            path: "Sources"
        )
    ]
)
