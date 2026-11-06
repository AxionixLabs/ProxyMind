// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "DesktopSidecar",
    platforms: [.macOS("14.2")],
    products: [.executable(name: "desktop-sidecar", targets: ["DesktopSidecar"])],
    targets: [
        .executableTarget(name: "DesktopSidecar", path: "Sources", linkerSettings: [
            .linkedFramework("AppKit"), .linkedFramework("ApplicationServices"),
            .linkedFramework("ScreenCaptureKit"), .linkedFramework("Carbon"),
        ]),
        .testTarget(name: "DesktopSidecarTests", dependencies: ["DesktopSidecar"], path: "Tests"),
    ]
)
