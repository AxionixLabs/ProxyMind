import AppKit
import ApplicationServices

struct State: Encodable {
    let ready: Bool
    let count: Int
    let text: String
    let heldKeys: [Int]
    let hardwareKeys: [Int]
    let flags: UInt64
    let heldButtons: [UInt32]
    let drags: Int
    let scrolls: Int
    let blockerClicks: Int
    let events: [InputEvent]
}

struct InputEvent: Encodable {
    let type: UInt
    let flags: UInt
    let sourcePID: Int64
}

@MainActor
final class EventTrace {
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private(set) var events: [InputEvent] = []
    func start() {
        let types: [CGEventType] = [.flagsChanged, .keyDown, .keyUp, .leftMouseDown, .leftMouseUp, .leftMouseDragged, .scrollWheel]
        let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }
        tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .tailAppendEventTap, options: .listenOnly,
            eventsOfInterest: mask, callback: { _, type, event, context in
                if let context {
                    let trace = Unmanaged<EventTrace>.fromOpaque(context).takeUnretainedValue()
                    MainActor.assumeIsolated {
                        let flags = UInt(event.flags.rawValue), sourcePID = event.getIntegerValueField(.eventSourceUnixProcessID)
                        if trace.events.last?.type != UInt(type.rawValue) || trace.events.last?.flags != flags || trace.events.last?.sourcePID != sourcePID {
                            if trace.events.count == 64 { trace.events.removeFirst() }
                            trace.events.append(InputEvent(type: UInt(type.rawValue), flags: flags, sourcePID: sourcePID))
                        }
                    }
                }
                return Unmanaged.passUnretained(event)
            }, userInfo: Unmanaged.passUnretained(self).toOpaque())
        if let tap {
            source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
            if let source { CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes) }
        }
    }
    func close() {
        if let tap { CFMachPortInvalidate(tap) }
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        tap = nil; source = nil
    }
}

struct OverlayWindow: Encodable {
    let id: UInt32
    let title: String
    let visible: Bool
    let alpha: Double
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OverlayState: Encodable {
    let windows: [OverlayWindow]
    let cursorX: Double
    let cursorY: Double
    let frontmostPID: pid_t
}

final class InputView: NSView {
    private(set) var drags = 0
    private(set) var scrolls = 0
    override func mouseDown(with event: NSEvent) {}
    override func mouseUp(with event: NSEvent) {}
    override func mouseDragged(with event: NSEvent) { drags += 1 }
    override func scrollWheel(with event: NSEvent) { scrolls += 1 }
}

final class BlockingPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class Fixture: NSObject, NSApplicationDelegate {
    private let window = NSWindow(contentRect: NSRect(x: 160, y: 180, width: 640, height: 380),
                                  styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
    private let entry = NSTextField(frame: NSRect(x: 50, y: 180, width: 500, height: 32))
    private let label = NSTextField(labelWithString: "Count: 0")
    private let surface = InputView(frame: NSRect(x: 0, y: 0, width: 640, height: 380))
    private var blocker: BlockingPanel?
    private var blockerClicks = 0
    private let trace = EventTrace()
    private var count = 0
    func applicationDidFinishLaunching(_ notification: Notification) {
        trace.start()
        window.title = "CUA macOS Fixture"
        window.isReleasedWhenClosed = false
        window.contentView = surface
        window.contentView?.wantsLayer = true
        window.contentView?.layer?.backgroundColor = NSColor(srgbRed: 22/255, green: 38/255, blue: 65/255, alpha: 1).cgColor
        let button = NSButton(title: "Increment counter", target: self, action: #selector(increment))
        button.frame = NSRect(x: 50, y: 270, width: 200, height: 40)
        button.setAccessibilityIdentifier("increment")
        label.frame = NSRect(x: 280, y: 280, width: 200, height: 24)
        label.textColor = .white
        entry.setAccessibilityIdentifier("entry"); entry.setAccessibilityLabel("Fixture text")
        for view in [button, label, entry] { window.contentView?.addSubview(view) }
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        state()
        DispatchQueue.global().async {
            while let line = readLine() {
                DispatchQueue.main.async { self.command(line) }
            }
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
    }
    @objc private func increment() { count += 1; label.stringValue = "Count: \(count)" }
    @objc private func blockedClick() { blockerClicks += 1 }
    func applicationWillTerminate(_ notification: Notification) { trace.close() }
    private func state() {
        let state = State(ready: window.isVisible, count: count, text: entry.stringValue,
            heldKeys: (0..<128).filter { CGEventSource.keyState(.combinedSessionState, key: CGKeyCode($0)) },
            hardwareKeys: (0..<128).filter { CGEventSource.keyState(.hidSystemState, key: CGKeyCode($0)) },
            flags: CGEventSource.flagsState(.combinedSessionState).rawValue,
            heldButtons: [CGMouseButton.left, .right, .center].filter { CGEventSource.buttonState(.combinedSessionState, button: $0) }.map(\.rawValue),
            drags: surface.drags, scrolls: surface.scrolls, blockerClicks: blockerClicks, events: trace.events)
        guard var data = try? JSONEncoder().encode(state) else { return }
        data.append(10); FileHandle.standardOutput.write(data)
    }
    private func overlays(pid: pid_t) {
        let native = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [NSDictionary] ?? []
        let windows: [OverlayWindow] = native.compactMap { info in
            guard let owner = info.object(forKey: kCGWindowOwnerPID) as? Int, owner == pid,
                  let id = info.object(forKey: kCGWindowNumber) as? UInt32,
                  let dictionary = info.object(forKey: kCGWindowBounds) as? NSDictionary,
                  let bounds = CGRect(dictionaryRepresentation: dictionary) else { return nil }
            return OverlayWindow(id: id, title: info.object(forKey: kCGWindowName) as? String ?? "",
                visible: info.object(forKey: kCGWindowIsOnscreen) as? Bool ?? false,
                alpha: info.object(forKey: kCGWindowAlpha) as? Double ?? 0,
                x: bounds.minX, y: bounds.minY, width: bounds.width, height: bounds.height)
        }
        let cursor = CGEvent(source: nil)?.location ?? .zero
        let state = OverlayState(windows: windows, cursorX: cursor.x, cursorY: cursor.y,
                                 frontmostPID: NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0)
        guard var data = try? JSONEncoder().encode(state) else { return }
        data.append(10); FileHandle.standardOutput.write(data)
    }
    private func cover(ignoresMouse: Bool) {
        blocker?.close()
        let frame = window.convertToScreen(NSRect(x: 40, y: 260, width: 220, height: 60))
        let panel = BlockingPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.title = "CUA fixture blocker"; panel.isReleasedWhenClosed = false
        panel.level = .floating; panel.hidesOnDeactivate = false; panel.ignoresMouseEvents = ignoresMouse
        panel.contentView = NSButton(title: "Block clicks", target: self, action: #selector(blockedClick))
        panel.orderFrontRegardless()
        blocker = panel
    }
    private func command(_ command: String) {
        let parts = command.split(separator: " ")
        if parts.count == 2, parts[0] == "overlays", let pid = pid_t(parts[1]) { overlays(pid: pid); return }
        switch command {
        case "small":
            window.setContentSize(NSSize(width: 640, height: 380)); window.center()
        case "large":
            if let screen = window.screen { window.setFrame(screen.visibleFrame.insetBy(dx: 20, dy: 20), display: true) }
        case "block": cover(ignoresMouse: false)
        case "through": cover(ignoresMouse: true)
        case "unblock": blocker?.close(); blocker = nil
        case "interrupt":
            if let point = CGEvent(source: nil)?.location {
                CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
            }
        case "move": window.setFrameOrigin(NSPoint(x: window.frame.minX + 40, y: window.frame.minY + 30))
        case "minimize": window.miniaturize(nil)
        case "restore": window.deminiaturize(nil)
        case "close": window.close()
        case "exit": NSApp.terminate(nil)
        default: break
        }
        state()
    }
}

@main
enum FixtureApplication {
    @MainActor static func main() {
        signal(SIGPIPE, SIG_IGN)
        let app = NSApplication.shared, delegate = Fixture()
        app.delegate = delegate; app.setActivationPolicy(.regular); app.run()
    }
}
