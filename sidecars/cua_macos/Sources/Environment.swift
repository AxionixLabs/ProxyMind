import AppKit
import ApplicationServices
import Carbon

struct Permissions: Equatable {
    let accessibility: Bool
    let capture: Bool
    let post: Bool
    let listen: Bool
    var input: Bool { accessibility && post && listen }
    static func current() -> Permissions {
        Permissions(accessibility: AXIsProcessTrustedWithOptions(
            [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: false] as CFDictionary),
                    capture: CGPreflightScreenCaptureAccess(), post: CGPreflightPostEventAccess(),
                    listen: CGPreflightListenEventAccess())
    }
}

struct DisplayIdentity: Equatable {
    let id: UInt32
    let bounds: CGRect
    let scale: CGFloat
}

@MainActor
enum DesktopEnvironment {
    static var primaryTop: CGFloat {
        NSScreen.screens.first(where: { displayID($0) == CGMainDisplayID() })?.frame.maxY ?? 0
    }
    static func displayID(_ screen: NSScreen) -> UInt32 {
        (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
    }
    static func displays() -> [DisplayIdentity] {
        let top = primaryTop
        return NSScreen.screens.map {
            DisplayIdentity(id: displayID($0), bounds: Coordinates.appKitRect($0.frame, primaryTop: top),
                            scale: $0.backingScaleFactor)
        }.sorted { $0.id < $1.id }
    }
    static func scale(for bounds: CGRect) -> CGFloat {
        displays().max { left, right in
            let a = left.bounds.intersection(bounds), b = right.bounds.intersection(bounds)
            return (a.isNull ? 0 : a.width * a.height) < (b.isNull ? 0 : b.width * b.height)
        }?.scale ?? 1
    }
    static func requireInteractive() throws {
        guard let session = CGSessionCopyCurrentDictionary() as NSDictionary?,
              session.object(forKey: kCGSessionOnConsoleKey as String) as? Bool == true,
              session.object(forKey: kCGSessionLoginDoneKey as String) as? Bool == true,
              session.object(forKey: "CGSSessionScreenIsLocked") as? Bool != true,
              !NSScreen.screens.isEmpty else {
            throw DesktopFailure("desktop_unavailable", "An unlocked local console is required.")
        }
        guard !IsSecureEventInputEnabled() else {
            throw DesktopFailure("desktop_unavailable", "Secure Input is active.")
        }
        if let bundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier,
           ["com.apple.loginwindow", "com.apple.SecurityAgent", "com.apple.systempreferences"].contains(bundle) {
            throw DesktopFailure("desktop_unavailable", "The current system security surface cannot receive automation.")
        }
    }
}

enum AX {
    static func value(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        AXUIElementSetMessagingTimeout(element, 0.2)
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
    }
    static func string(_ element: AXUIElement, _ name: String, limit: Int = 1024) -> String {
        guard let value = value(element, name) as? String else { return "" }
        var units = Array(value.utf16.prefix(limit))
        if let last = units.last, (0xD800...0xDBFF).contains(last) { units.removeLast() }
        return String(decoding: units, as: UTF16.self)
    }
    static func bool(_ element: AXUIElement, _ name: String, default fallback: Bool = false) -> Bool {
        value(element, name) as? Bool ?? fallback
    }
    static func element(_ parent: AXUIElement, _ name: String) -> AXUIElement? {
        guard let value = value(parent, name), CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }
    static func elements(_ parent: AXUIElement, _ name: String, limit: Int) throws -> (items: [AXUIElement], truncated: Bool) {
        AXUIElementSetMessagingTimeout(parent, 0.2)
        var count: CFIndex = 0
        let status = AXUIElementGetAttributeValueCount(parent, name as CFString, &count)
        if status == .attributeUnsupported || status == .noValue { return ([], false) }
        guard status == .success else { throw DesktopFailure("accessibility_unavailable", "Cannot read Accessibility children.") }
        guard count > 0, limit > 0 else { return ([], count > 0) }
        var array: CFArray?
        guard AXUIElementCopyAttributeValues(parent, name as CFString, 0, min(count, limit), &array) == .success,
              let values = array as? [AXUIElement] else {
            throw DesktopFailure("accessibility_unavailable", "Cannot read Accessibility elements.")
        }
        return (values, count > limit)
    }
    static func bounds(_ element: AXUIElement) -> CGRect? {
        guard let position = value(element, kAXPositionAttribute), CFGetTypeID(position) == AXValueGetTypeID(),
              let size = value(element, kAXSizeAttribute), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
        let pointValue = position as! AXValue, sizeValue = size as! AXValue
        var point = CGPoint.zero, dimensions = CGSize.zero
        guard AXValueGetType(pointValue) == .cgPoint, AXValueGetType(sizeValue) == .cgSize,
              AXValueGetValue(pointValue, .cgPoint, &point), AXValueGetValue(sizeValue, .cgSize, &dimensions),
              point.x.isFinite, point.y.isFinite, dimensions.width.isFinite, dimensions.height.isFinite,
              dimensions.width >= 0, dimensions.height >= 0 else { return nil }
        return CGRect(origin: point, size: dimensions)
    }
}

struct WindowInfo {
    let id: CGWindowID
    let pid: pid_t
    let bounds: CGRect
    let title: String
    let visible: Bool
    let layer: Int
    static func all(onScreen: Bool = false) -> [WindowInfo] {
        let options: CGWindowListOption = onScreen ? [.optionOnScreenOnly, .excludeDesktopElements] : [.optionAll, .excludeDesktopElements]
        guard let values = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [NSDictionary] else { return [] }
        return values.compactMap { value in
            guard let id = value.object(forKey: kCGWindowNumber) as? NSNumber,
                  let pid = value.object(forKey: kCGWindowOwnerPID) as? NSNumber,
                  let rectangle = value.object(forKey: kCGWindowBounds) as? NSDictionary,
                  let bounds = CGRect(dictionaryRepresentation: rectangle),
                  let layer = value.object(forKey: kCGWindowLayer) as? NSNumber else { return nil }
            return WindowInfo(id: id.uint32Value, pid: pid.int32Value, bounds: bounds,
                              title: value.object(forKey: kCGWindowName) as? String ?? "",
                              visible: value.object(forKey: kCGWindowIsOnscreen) as? Bool ?? false,
                              layer: layer.intValue)
        }
    }
}
