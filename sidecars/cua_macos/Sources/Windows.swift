import AppKit
import ApplicationServices

struct WindowSnapshot: Equatable {
    let bounds: CGRect
    let scale: CGFloat
    let minimized: Bool
    let visible: Bool
    let revision: UInt64
    let permissions: Permissions
    let displays: [DisplayIdentity]
}

final class WindowReference {
    let reference = UUID().uuidString
    let element: AXUIElement
    let id: CGWindowID
    let pid: pid_t
    let launched: Date?
    var revision: UInt64 = 0
    init(element: AXUIElement, info: WindowInfo, launched: Date?) {
        self.element = element; id = info.id; pid = info.pid; self.launched = launched
    }
}

struct ControlReference {
    let element: AXUIElement
    let name: String
    let role: String
    let identifier: String
    let bounds: CGRect?
    let enabled: Bool
    let password: Bool
    init(_ element: AXUIElement) {
        self.element = element
        role = AX.string(element, kAXRoleAttribute, limit: 128)
        password = AX.string(element, kAXSubroleAttribute) == kAXSecureTextFieldSubrole
        let title = AX.string(element, kAXTitleAttribute), description = AX.string(element, kAXDescriptionAttribute)
        name = !title.isEmpty ? title : (!description.isEmpty ? description : (password ? "" : AX.string(element, kAXValueAttribute)))
        identifier = AX.string(element, kAXIdentifierAttribute)
        bounds = AX.bounds(element); enabled = AX.bool(element, kAXEnabledAttribute, default: true)
    }
    func matches(_ other: ControlReference) -> Bool {
        CFEqual(element, other.element) && name == other.name && role == other.role && identifier == other.identifier &&
        bounds == other.bounds && enabled == other.enabled && password == other.password
    }
}

struct Observation {
    let reference = UUID().uuidString
    let window: String
    let snapshot: WindowSnapshot
    let controls: [ControlReference]
    let imageWidth: Int
    let imageHeight: Int
    let created = ProcessInfo.processInfo.systemUptime
}

@MainActor
final class Windows {
    private(set) var references: [String: WindowReference] = [:]
    private var observations: [String: Observation] = [:]
    private var observers: [pid_t: AXObserver] = [:]
    private var displayObserver: NSObjectProtocol?
    private var workspaceObservers: [NSObjectProtocol] = []
    init() {
        displayObserver = NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
            object: nil, queue: .main) { [weak self] _ in MainActor.assumeIsolated { self?.reviseAll() } }
        for name in [NSWorkspace.activeSpaceDidChangeNotification, NSWorkspace.didActivateApplicationNotification,
                     NSWorkspace.didHideApplicationNotification, NSWorkspace.didUnhideApplicationNotification,
                     NSWorkspace.didTerminateApplicationNotification] {
            workspaceObservers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: name, object: nil, queue: .main) {
                [weak self] _ in MainActor.assumeIsolated { self?.reviseAll() }
            })
        }
    }
    func close() {
        for observer in observers.values { CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .defaultMode) }
        observers.removeAll(); references.removeAll(); observations.removeAll()
        if let displayObserver { NotificationCenter.default.removeObserver(displayObserver) }
        displayObserver = nil
        for observer in workspaceObservers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        workspaceObservers.removeAll()
    }
    private func reviseAll() { for window in references.values { window.revision &+= 1 } }
    func changed(_ element: AXUIElement) {
        var pid: pid_t = 0
        guard AXUIElementGetPid(element, &pid) == .success else { reviseAll(); return }
        for window in references.values where window.pid == pid { window.revision &+= 1 }
    }
    private func track(_ window: WindowReference) throws {
        let pid = window.pid
        if observers[pid] == nil {
            var value: AXObserver?
            let status = AXObserverCreate(pid, { _, element, _, context in
                guard let context else { return }
                let owner = Unmanaged<Windows>.fromOpaque(context).takeUnretainedValue()
                MainActor.assumeIsolated { owner.changed(element) }
            }, &value)
            guard status == .success, let value else { throw DesktopFailure("window_unavailable", "Window event observation is unavailable.") }
            observers[pid] = value
            CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(value), .defaultMode)
        }
        guard let observer = observers[pid] else { return }
        let context = Unmanaged.passUnretained(self).toOpaque()
        for name in [kAXUIElementDestroyedNotification, kAXWindowMovedNotification, kAXWindowResizedNotification,
                     kAXWindowMiniaturizedNotification, kAXWindowDeminiaturizedNotification,
                     kAXTitleChangedNotification, kAXValueChangedNotification] {
            let status = AXObserverAddNotification(observer, window.element, name as CFString, context)
            if ![AXError.success, .notificationAlreadyRegistered, .notificationUnsupported].contains(status) {
                throw DesktopFailure("window_unavailable", "Cannot register window lifecycle notifications.")
            }
        }
    }
    private func untrack(_ window: WindowReference) {
        guard let observer = observers[window.pid] else { return }
        for name in [kAXUIElementDestroyedNotification, kAXWindowMovedNotification, kAXWindowResizedNotification,
                     kAXWindowMiniaturizedNotification, kAXWindowDeminiaturizedNotification,
                     kAXTitleChangedNotification, kAXValueChangedNotification] {
            AXObserverRemoveNotification(observer, window.element, name as CFString)
        }
    }
    func list() throws -> JSON {
        guard Permissions.current().accessibility else { throw DesktopFailure("accessibility_denied", "Grant Accessibility to Desktop Sidecar, then reset the desktop session.") }
        expire()
        let native = WindowInfo.all()
        var kept: [String: WindowReference] = [:], result: [JSON] = [], truncated = false
        for app in NSWorkspace.shared.runningApplications where app.processIdentifier != getpid() && app.activationPolicy == .regular {
            let root = AXUIElementCreateApplication(app.processIdentifier)
            AXUIElementSetMessagingTimeout(root, 0.2)
            guard let windows = try? AX.elements(root, kAXWindowsAttribute, limit: 129) else { truncated = true; continue }
            truncated = truncated || windows.truncated
            for element in windows.items {
                guard let bounds = AX.bounds(element), bounds.width > 0, bounds.height > 0 else { continue }
                let candidates = native.filter { $0.pid == app.processIdentifier && $0.layer == 0 && $0.bounds == bounds }
                guard candidates.count == 1, let info = candidates.first else { truncated = true; continue }
                guard result.count < 128 else { truncated = true; break }
                let previous = references.values.first { $0.id == info.id && $0.pid == info.pid && $0.launched == app.launchDate && CFEqual($0.element, element) }
                let window = previous ?? WindowReference(element: element, info: info, launched: app.launchDate)
                if previous == nil { try track(window) }
                kept[window.reference] = window
                result.append(.object(["window": .string(window.reference), "title": .string(AX.string(element, kAXTitleAttribute)),
                    "pid": .integer(Int(window.pid)), "bounds": Coordinates.json(bounds),
                    "displayScale": .number(DesktopEnvironment.scale(for: bounds)),
                    "minimized": .bool(AX.bool(element, kAXMinimizedAttribute))]))
            }
        }
        for (reference, window) in references where kept[reference] == nil { untrack(window); observations.removeValue(forKey: reference) }
        references = kept
        let active = Set(kept.values.map(\.pid))
        for pid in Array(observers.keys) where !active.contains(pid) {
            if let observer = observers.removeValue(forKey: pid) { CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .defaultMode) }
        }
        return .object(["windows": .array(result), "truncated": .bool(truncated)])
    }
    func lookup(_ reference: String) throws -> WindowReference {
        guard Permissions.current().accessibility else {
            observations.removeAll()
            throw DesktopFailure("accessibility_denied", "Accessibility is unavailable; grant permission and reset the session.")
        }
        guard let window = references[reference], let app = NSRunningApplication(processIdentifier: window.pid),
              !app.isTerminated, app.launchDate == window.launched,
              let live = try? AX.elements(AXUIElementCreateApplication(window.pid), kAXWindowsAttribute, limit: 256),
              live.items.contains(where: { CFEqual($0, window.element) }),
              WindowInfo.all().contains(where: { $0.id == window.id && $0.pid == window.pid }) else {
            observations.removeValue(forKey: reference)
            throw DesktopFailure("stale_window", "Window identity expired; enumerate again.")
        }
        return window
    }
    func snapshot(_ window: WindowReference, requireVisible: Bool = true) throws -> WindowSnapshot {
        let permissions = Permissions.current()
        guard permissions.accessibility else { throw DesktopFailure("accessibility_denied", "Accessibility permission was revoked; reset after granting it.") }
        guard let bounds = AX.bounds(window.element), bounds.width > 0, bounds.height > 0,
              let native = WindowInfo.all().first(where: { $0.id == window.id && $0.pid == window.pid }),
              native.bounds == bounds else { throw DesktopFailure("window_changed", "Window geometry changed.") }
        let minimized = AX.bool(window.element, kAXMinimizedAttribute)
        let visible = native.visible && NSRunningApplication(processIdentifier: window.pid)?.isHidden == false
        if requireVisible && (minimized || !visible) { throw DesktopFailure("window_unavailable", "Window is hidden, minimized or on another Space.") }
        return WindowSnapshot(bounds: bounds, scale: DesktopEnvironment.scale(for: bounds), minimized: minimized,
                              visible: visible, revision: window.revision, permissions: permissions, displays: DesktopEnvironment.displays())
    }
    func observe(_ reference: String, screenshot: Bool) async throws -> JSON {
        observations.removeValue(forKey: reference); expire()
        let window = try lookup(reference), before = try snapshot(window)
        let tree = controls(window)
        let image = screenshot ? try await Capture.window(window.id, bounds: before.bounds) : nil
        guard before == (try snapshot(lookup(reference))) else { throw DesktopFailure("window_changed", "Window changed while observing.") }
        let observation = Observation(window: reference, snapshot: before, controls: tree.controls,
                                      imageWidth: image?.width ?? 0, imageHeight: image?.height ?? 0)
        observations[reference] = observation
        return .object(["window": .string(reference), "observation": .string(observation.reference),
            "capturedAt": .string(ISO8601DateFormatter().string(from: Date())), "bounds": Coordinates.json(before.bounds),
            "displayScale": .number(before.scale), "coordinateSpace": .string("logical_screen_points"),
            "tree": .object(["nodes": .array(tree.nodes), "truncated": .bool(tree.truncated), "error": tree.error.map(JSON.string) ?? .null]),
            "image": image?.json ?? .null])
    }
    private func controls(_ window: WindowReference) -> (controls: [ControlReference], nodes: [JSON], truncated: Bool, error: String?) {
        var controls: [ControlReference] = [], nodes: [JSON] = [], truncated = false, failure: String?
        let bounds = AX.bounds(window.element) ?? .zero
        let deadline = ProcessInfo.processInfo.systemUptime + 3
        func visit(_ element: AXUIElement, parent: Int, depth: Int) {
            guard controls.count < 256 else { truncated = true; return }
            guard ProcessInfo.processInfo.systemUptime < deadline else { truncated = true; failure = "accessibility_timeout"; return }
            let control = ControlReference(element), index = controls.count
            controls.append(control)
            let relative = control.bounds.map { $0.offsetBy(dx: -bounds.minX, dy: -bounds.minY) }
            nodes.append(.object(["index": .integer(index), "parent": .integer(parent), "name": .string(control.name),
                "role": .string(control.role), "automationId": .string(control.identifier), "enabled": .bool(control.enabled),
                "offscreen": .bool(control.bounds.map { !$0.intersects(bounds) } ?? false), "password": .bool(control.password),
                "bounds": relative.map(Coordinates.json) ?? .null]))
            do {
                let children = try AX.elements(element, kAXChildrenAttribute, limit: depth < 8 ? 256 - controls.count : 1)
                if depth == 8 { truncated = truncated || !children.items.isEmpty || children.truncated; return }
                truncated = truncated || children.truncated
                for child in children.items { visit(child, parent: index, depth: depth + 1) }
            } catch { truncated = true; failure = "accessibility_unavailable" }
        }
        visit(window.element, parent: -1, depth: 0)
        return (controls, nodes, truncated, failure)
    }
    func validate(_ reference: String, observation: String) throws -> Observation {
        expire()
        guard let recorded = observations[reference], recorded.reference == observation else {
            throw DesktopFailure("stale_observation", "Observation expired, was consumed or belongs to another session.")
        }
        do {
            guard recorded.snapshot == (try snapshot(lookup(reference))) else {
                throw DesktopFailure("window_changed", "Window, display or permission state changed; observe again.")
            }
        } catch { observations.removeValue(forKey: reference); throw error }
        return recorded
    }
    func invalidate(_ reference: String) { observations.removeValue(forKey: reference) }
    private func expire() {
        let now = ProcessInfo.processInfo.systemUptime
        observations = observations.filter { now - $0.value.created < 120 && references[$0.key] != nil }
    }
}
