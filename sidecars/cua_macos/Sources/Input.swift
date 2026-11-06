import AppKit
import ApplicationServices

enum InputOperation {
    case click(JSON, CGMouseButton, Int), move(JSON, Int), drag(JSON, JSON, Int), scroll(JSON, Int, Int)
    case keys([CGKeyCode]), text(String)
    init(_ value: JSON) throws {
        let kind = try value["kind"].string(limit: 32)
        switch kind {
        case "click":
            _ = try value.fields("kind", "target", "button", "count")
            let name = try value["button"].string(limit: 8)
            guard let button = ["left": CGMouseButton.left, "right": .right, "middle": .center][name] else {
                throw DesktopFailure("invalid_request", "Invalid mouse button.")
            }
            self = .click(value["target"], button, try value["count"].integer(1...2))
        case "move":
            _ = try value.fields("kind", "target", "durationMs"); _ = try value["target"].fields("x", "y")
            self = .move(value["target"], try value["durationMs"].integer(0...1200))
        case "drag":
            _ = try value.fields("kind", "start", "end", "durationMs")
            _ = try value["start"].fields("x", "y"); _ = try value["end"].fields("x", "y")
            self = .drag(value["start"], value["end"], try value["durationMs"].integer(50...1200))
        case "scroll":
            _ = try value.fields("kind", "target", "deltaX", "deltaY"); _ = try value["target"].fields("x", "y")
            let x = try value["deltaX"].integer(-20...20), y = try value["deltaY"].integer(-20...20)
            guard x != 0 || y != 0 else { throw DesktopFailure("invalid_request", "Scroll delta cannot be zero.") }
            self = .scroll(value["target"], x, y)
        case "press_key":
            _ = try value.fields("kind", "keys")
            guard case .array(let keys) = value["keys"], (1...4).contains(keys.count) else {
                throw DesktopFailure("invalid_request", "Expected one to four keys.")
            }
            let codes = try keys.map { try KeyMap.code($0.string(limit: 16)) }
            guard Set(codes).count == codes.count else { throw DesktopFailure("invalid_request", "Duplicate key.") }
            self = .keys(codes)
        case "type_text":
            _ = try value.fields("kind", "text")
            let text = try value["text"].string(limit: 2048, empty: true)
            guard !text.isEmpty, !text.contains("\0") else { throw DesktopFailure("invalid_request", "Text must contain 1..2048 UTF-16 units without NUL.") }
            self = .text(text)
        default: throw DesktopFailure("unsupported_action", "Unknown input operation.")
        }
    }
}

enum KeyMap {
    static func code(_ name: String) throws -> CGKeyCode {
        let keys: [String: CGKeyCode] = [
            "A":0,"S":1,"D":2,"F":3,"H":4,"G":5,"Z":6,"X":7,"C":8,"V":9,"B":11,
            "Q":12,"W":13,"E":14,"R":15,"Y":16,"T":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,
            "9":25,"7":26,"8":28,"0":29,"O":31,"U":32,"I":34,"P":35,"L":37,"J":38,"K":40,"N":45,"M":46,
            "ENTER":36,"TAB":48,"SPACE":49,"BACKSPACE":51,"ESC":53,"WIN":55,"SHIFT":56,"ALT":58,"CTRL":59,
            "F1":122,"F2":120,"F3":99,"F4":118,"F5":96,"F6":97,"F7":98,"F8":100,"F9":101,"F10":109,
            "F11":103,"F12":111,"F13":105,"F14":107,"F15":113,"F16":106,"F17":64,"F18":79,"F19":80,"F20":90,
            "HOME":115,"PAGEUP":116,"DELETE":117,"END":119,"PAGEDOWN":121,"LEFT":123,"RIGHT":124,"DOWN":125,"UP":126,
        ]
        guard let code = keys[name] else { throw DesktopFailure("input_rejected", "The named key is unavailable on macOS.") }
        return code
    }
    static func flags(_ codes: [CGKeyCode]) -> CGEventFlags {
        var value: CGEventFlags = []
        if codes.contains(55) { value.insert(.maskCommand) }
        if codes.contains(56) { value.insert(.maskShift) }
        if codes.contains(58) { value.insert(.maskAlternate) }
        if codes.contains(59) { value.insert(.maskControl) }
        return value
    }
}

@MainActor
final class Input {
    private let windows: Windows
    private let lifetime: Lifetime
    private let overlay = Overlay()
    private var tap: CFMachPort?
    private var tapSource: CFRunLoopSource?
    private var interrupted = false
    private var stopped = false
    private let tag = Int64.random(in: 1...Int64.max)
    private var submitted = 0
    init(windows: Windows, lifetime: Lifetime) { self.windows = windows; self.lifetime = lifetime }
    func close() { endMonitoring(); overlay.close() }
    private func permission() throws {
        guard !stopped else { throw DesktopFailure("input_stopped", "User intervention stopped this session; reset only after explicit user permission.") }
        guard !lifetime.inputCancelled else { throw DesktopFailure("input_interrupted", "Desktop input was cancelled.") }
        guard Permissions.current().input else { throw DesktopFailure("input_guard_unavailable", "Accessibility, event posting and input monitoring permissions are required.") }
        try DesktopEnvironment.requireInteractive()
    }
    private func beginMonitoring() throws {
        interrupted = false
        let types: [CGEventType] = [.keyDown, .keyUp, .flagsChanged, .mouseMoved, .leftMouseDown, .leftMouseUp,
            .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged, .scrollWheel]
        let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }
        tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
            eventsOfInterest: mask, callback: { _, type, event, context in
                guard let context else { return Unmanaged.passUnretained(event) }
                let owner = Unmanaged<Input>.fromOpaque(context).takeUnretainedValue()
                MainActor.assumeIsolated {
                    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput ||
                        event.getIntegerValueField(.eventSourceUserData) != owner.tag {
                        owner.interrupted = true; owner.stopped = true
                    }
                }
                return Unmanaged.passUnretained(event)
            }, userInfo: Unmanaged.passUnretained(self).toOpaque())
        guard let tap else { throw DesktopFailure("input_guard_unavailable", "Cannot monitor external keyboard and mouse input.") }
        tapSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        guard let tapSource else { endMonitoring(); throw DesktopFailure("input_guard_unavailable", "Cannot attach input monitor.") }
        CFRunLoopAddSource(CFRunLoopGetMain(), tapSource, .commonModes); CGEvent.tapEnable(tap: tap, enable: true)
    }
    private func endMonitoring() {
        if let tap { CGEvent.tapEnable(tap: tap, enable: false); CFMachPortInvalidate(tap) }
        if let tapSource { CFRunLoopRemoveSource(CFRunLoopGetMain(), tapSource, .commonModes) }
        tap = nil; tapSource = nil
    }
    private func idle() throws {
        for code in 0..<128 where CGEventSource.keyState(.combinedSessionState, key: CGKeyCode(code)) {
            throw DesktopFailure("input_busy", "A keyboard key is already pressed (key code \(code)).")
        }
        for button in [CGMouseButton.left, .right, .center] where CGEventSource.buttonState(.combinedSessionState, button: button) {
            throw DesktopFailure("input_busy", "A mouse button is already pressed.")
        }
    }
    private func focused(_ window: WindowReference) throws {
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == window.pid,
              let focused = AX.element(AXUIElementCreateApplication(window.pid), kAXFocusedWindowAttribute),
              CFEqual(focused, window.element) else { throw DesktopFailure("focus_changed", "Target window is not focused.") }
    }
    private func hit(_ point: CGPoint, window: WindowReference) throws {
        let desktop = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(desktop, 0.2)
        var element: AXUIElement?, pid: pid_t = 0
        // 窗口层级含点击穿透的系统浮层；AX 命中检测按实际可接收输入的元素判断。
        guard AXUIElementCopyElementAtPosition(desktop, Float(point.x), Float(point.y), &element) == .success,
              let element, AXUIElementGetPid(element, &pid) == .success, pid == window.pid,
              CFEqual(element, window.element) || AX.element(element, kAXWindowAttribute).map({ CFEqual($0, window.element) }) == true else {
            throw DesktopFailure("target_obscured", "Target point does not hit the observed window.")
        }
    }
    private func point(_ value: JSON, observation: Observation) throws -> CGPoint {
        if case .object(let fields) = value, fields.keys.contains("element") {
            _ = try value.fields("element")
            let index = try value["element"].integer(0...255)
            guard index < observation.controls.count else { throw DesktopFailure("invalid_request", "Unknown control index.") }
            let control = observation.controls[index], current = ControlReference(control.element)
            guard control.matches(current), current.enabled, let bounds = current.bounds,
                  observation.snapshot.bounds.contains(CGPoint(x: bounds.midX, y: bounds.midY)) else {
                throw DesktopFailure("window_changed", "Observed control identity or availability changed.")
            }
            return CGPoint(x: bounds.midX, y: bounds.midY)
        }
        _ = try value.fields("x", "y")
        return try Coordinates.imagePoint(x: value["x"].integer(0...16_000_000), y: value["y"].integer(0...16_000_000),
            width: observation.imageWidth, height: observation.imageHeight, bounds: observation.snapshot.bounds)
    }
    private func post(_ event: CGEvent?) throws {
        guard let event else { throw DesktopFailure("input_rejected", "Cannot create a Quartz input event.") }
        event.setIntegerValueField(.eventSourceUserData, value: tag)
        event.post(tap: .cghidEventTap); submitted += 1
    }
    private func check(_ window: WindowReference, observation: Observation) throws {
        if interrupted { throw DesktopFailure("user_interrupted", "External mouse or keyboard input stopped the action.") }
        try permission(); try focused(window)
        let current = try windows.snapshot(windows.lookup(window.reference))
        if current.bounds != observation.snapshot.bounds { overlay.sync(bounds: current.bounds) }
        guard current == observation.snapshot else { throw DesktopFailure("window_changed", "Window or display state changed during input.") }
    }
    private func move(to target: CGPoint, duration: Int, dragging: Bool = false,
                      window: WindowReference, observation: Observation) async throws {
        let start = CGEvent(source: nil)?.location ?? target, began = ProcessInfo.processInfo.systemUptime
        repeat {
            try check(window, observation: observation)
            let fraction = duration == 0 ? 1 : min(1, (ProcessInfo.processInfo.systemUptime - began) * 1000 / Double(duration))
            let fractionSmooth = fraction * fraction * (3 - 2 * fraction)
            let position = CGPoint(x: start.x + (target.x - start.x) * fractionSmooth, y: start.y + (target.y - start.y) * fractionSmooth)
            if dragging { try hit(position, window: window) }
            try post(CGEvent(mouseEventSource: nil, mouseType: dragging ? .leftMouseDragged : .mouseMoved,
                             mouseCursorPosition: position, mouseButton: .left))
            if fraction >= 1 { break }
            try await Task.sleep(nanoseconds: 8_000_000)
        } while true
        try hit(target, window: window)
    }
    func activate(_ reference: String) async throws -> JSON {
        defer { windows.invalidate(reference) }
        try permission(); try idle()
        let window = try windows.lookup(reference), snapshot = try windows.snapshot(window)
        let guardProcess = try GuardClient(tag: tag); defer { overlay.hide(); guardProcess.close() }
        try beginMonitoring(); defer { endMonitoring() }
        try await overlay.show(bounds: snapshot.bounds)
        try permission()
        if interrupted { throw DesktopFailure("user_interrupted", "External input interrupted activation.") }
        guard let app = NSRunningApplication(processIdentifier: window.pid), app.activate(options: []),
              AXUIElementPerformAction(window.element, kAXRaiseAction as CFString) == .success else {
            throw DesktopFailure("input_rejected", "The application rejected activation.")
        }
        let deadline = ProcessInfo.processInfo.systemUptime + 1
        while ProcessInfo.processInfo.systemUptime < deadline {
            if interrupted { throw DesktopFailure("user_interrupted", "External input interrupted activation.") }
            try permission()
            if (try? focused(window)) != nil { try await overlay.finish(); return result(1) }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        throw DesktopFailure("focus_changed", "The target did not become the focused window.")
    }
    func perform(_ reference: String, observation referenceID: String, operation value: JSON) async throws -> JSON {
        defer { windows.invalidate(reference) }
        let observation = try windows.validate(reference, observation: referenceID)
        let operation = try InputOperation(value), window = try windows.lookup(reference)
        try permission(); try idle(); try focused(window)
        // 所有坐标和控件在创建可见覆盖层之前校验。
        switch operation {
        case .click(let target, _, _), .move(let target, _), .scroll(let target, _, _): try hit(point(target, observation: observation), window: window)
        case .drag(let start, let end, _): try hit(point(start, observation: observation), window: window); try hit(point(end, observation: observation), window: window)
        case .keys, .text: break
        }
        let guardProcess = try GuardClient(tag: tag)
        defer { overlay.hide(); guardProcess.close(); endMonitoring() }
        try beginMonitoring(); try check(window, observation: observation)
        try await overlay.show(bounds: observation.snapshot.bounds)
        try check(window, observation: observation)
        submitted = 0
        do {
            switch operation {
            case .move(let target, let duration):
                try await move(to: point(target, observation: observation), duration: duration, window: window, observation: observation)
            case .click(let target, let button, let count):
                let position = try point(target, observation: observation)
                try await move(to: position, duration: 120, window: window, observation: observation)
                for click in 1...count {
                    try check(window, observation: observation); try hit(position, window: window)
                    try guardProcess.command("hold mouse:\(button.rawValue)")
                    let event = CGEvent(mouseEventSource: nil, mouseType: button == .left ? .leftMouseDown : button == .right ? .rightMouseDown : .otherMouseDown,
                                        mouseCursorPosition: position, mouseButton: button)
                    event?.setIntegerValueField(.mouseEventClickState, value: Int64(click)); try post(event)
                    try guardProcess.command("release mouse:\(button.rawValue) \(click)"); submitted += 1
                    overlay.feedback(); try await Task.sleep(nanoseconds: 25_000_000)
                }
            case .drag(let start, let end, let duration):
                try await move(to: point(start, observation: observation), duration: 120, window: window, observation: observation)
                try guardProcess.command("hold mouse:0")
                try post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                                 mouseCursorPosition: point(start, observation: observation), mouseButton: .left))
                try await move(to: point(end, observation: observation), duration: duration, dragging: true, window: window, observation: observation)
                try guardProcess.command("release mouse:0"); submitted += 1
            case .scroll(let target, let x, let y):
                try await move(to: point(target, observation: observation), duration: 120, window: window, observation: observation)
                try check(window, observation: observation)
                try post(CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 2, wheel1: Int32(y), wheel2: Int32(-x), wheel3: 0))
            case .keys(let codes):
                for (index, code) in codes.enumerated() {
                    try check(window, observation: observation); try guardProcess.command("hold key:\(code)")
                    let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
                    event?.flags = KeyMap.flags(Array(codes.prefix(index + 1))); try post(event)
                }
                for code in codes.reversed() { try guardProcess.command("release key:\(code)"); submitted += 1 }
            case .text(let text):
                for scalar in text.unicodeScalars {
                    try check(window, observation: observation); try guardProcess.command("hold key:0")
                    let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
                    let units = Array(String(scalar).utf16)
                    units.withUnsafeBufferPointer { event?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: $0.baseAddress) }
                    try post(event); try guardProcess.command("release key:0"); submitted += 1
                    try await Task.sleep(nanoseconds: 2_000_000)
                }
            }
            if interrupted { throw DesktopFailure("user_interrupted", "External input interrupted this action.") }
            try await overlay.finish()
            return result(submitted)
        } catch let error as DesktopFailure {
            if submitted > 0 && !["user_interrupted", "input_stopped"].contains(error.code) {
                throw DesktopFailure("input_interrupted", "Input stopped after submitting \(submitted) events (\(error.code)); observe before deciding another action.")
            }
            throw error
        } catch { throw DesktopFailure("input_interrupted", "Input was cancelled; submitted events must not be replayed.") }
    }
    private func result(_ count: Int) -> JSON { .object(["eventsSubmitted": .integer(count), "requiresObservation": .bool(true)]) }
}
