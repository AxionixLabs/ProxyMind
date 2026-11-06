import AppKit
import ScreenCaptureKit
import UniformTypeIdentifiers

struct CapturedImage {
    let width: Int
    let height: Int
    let mime: String
    let data: Data
    var json: JSON { .object(["width": .integer(width), "height": .integer(height),
                             "mimeType": .string(mime), "data": .string(data.base64EncodedString())]) }
}

@MainActor
enum Capture {
    static func window(_ id: CGWindowID, bounds: CGRect) async throws -> CapturedImage {
        guard CGPreflightScreenCaptureAccess() else { throw DesktopFailure("screen_capture_denied", "Grant Screen Recording to Desktop Sidecar, then reset the session.") }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
            guard let window = content.windows.first(where: { $0.windowID == id }), window.frame == bounds else {
                throw DesktopFailure("window_changed", "Capture window is unavailable or its geometry changed.")
            }
            // 单窗口过滤不会捕获本进程的蓝光或指针窗口。
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let scale = CGFloat(filter.pointPixelScale)
            guard scale.isFinite, scale > 0 else { throw DesktopFailure("capture_unavailable", "Capture scale is unavailable.") }
            let config = SCStreamConfiguration()
            let naturalWidth = max(1, Int(ceil(bounds.width * scale))), naturalHeight = max(1, Int(ceil(bounds.height * scale)))
            let factor = min(1, sqrt(16_000_000 / (Double(naturalWidth) * Double(naturalHeight))))
            config.width = max(1, Int(Double(naturalWidth) * factor)); config.height = max(1, Int(Double(naturalHeight) * factor))
            config.showsCursor = false; config.ignoreShadowsSingleWindow = true; config.includeChildWindows = false
            config.colorSpaceName = CGColorSpace.sRGB
            let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
            return try encode(image)
        } catch let error as DesktopFailure { throw error }
        catch { throw DesktopFailure("capture_unavailable", "ScreenCaptureKit failed to capture the current window.") }
    }
    static func encode(_ image: CGImage) throws -> CapturedImage {
        guard image.width * image.height <= 16_000_000 else { throw DesktopFailure("image_limit", "Capture exceeds pixel limit.") }
        let representation = NSBitmapImageRep(cgImage: image)
        if let png = representation.representation(using: .png, properties: [:]), png.count <= 1024 * 1024 {
            return CapturedImage(width: image.width, height: image.height, mime: "image/png", data: png)
        }
        for quality in [0.85, 0.65, 0.4] {
            if let jpeg = representation.representation(using: .jpeg, properties: [.compressionFactor: quality]), jpeg.count <= 8 * 1024 * 1024 {
                return CapturedImage(width: image.width, height: image.height, mime: "image/jpeg", data: jpeg)
            }
        }
        throw DesktopFailure("image_limit", "Encoded capture exceeds byte limit.")
    }
}
