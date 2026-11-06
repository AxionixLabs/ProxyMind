// swift-tools-version: 5.9
import PackageDescription

let package = Package(name: "DesktopFixture", platforms: [.macOS("14.2")],
    products: [.executable(name: "desktop-fixture", targets: ["DesktopFixture"])],
    targets: [.executableTarget(name: "DesktopFixture", path: "Sources")])
