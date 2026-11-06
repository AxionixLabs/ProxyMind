import AppKit

enum Coordinates {
    /// AX、CGEvent 和 SCK 使用主屏左上角为原点的逻辑点；AppKit 窗口使用左下角。
    static func appKitRect(_ rect: CGRect, primaryTop: CGFloat) -> CGRect {
        CGRect(x: rect.minX, y: primaryTop - rect.maxY, width: rect.width, height: rect.height)
    }
    static func imagePoint(x: Int, y: Int, width: Int, height: Int, bounds: CGRect) throws -> CGPoint {
        guard width > 0, height > 0, x >= 0, y >= 0, x < width, y < height else {
            throw DesktopFailure("invalid_request", "Point lies outside the observed image.")
        }
        return CGPoint(x: bounds.minX + CGFloat(x) * bounds.width / CGFloat(width),
                       y: bounds.minY + CGFloat(y) * bounds.height / CGFloat(height))
    }
    static func json(_ rect: CGRect) -> JSON {
        .object(["x": .number(rect.minX), "y": .number(rect.minY),
                 "width": .number(rect.width), "height": .number(rect.height)])
    }
}
