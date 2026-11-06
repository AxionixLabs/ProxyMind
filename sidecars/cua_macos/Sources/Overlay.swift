import AppKit
import QuartzCore

final class PassivePanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class EdgeGlowView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 20, dy: 20), xRadius: 12, yRadius: 12)
        var previousOpacity: CGFloat = 0
        // 沿同一闭合轮廓叠加柔化带；四角不会出现横、纵渐变交叉或独立边框线。
        for width in stride(from: 40, through: 1, by: -1) {
            let distance = CGFloat(width) / 2
            let opacity = 0.42 * exp(-distance * distance / 98)
            let contribution = (opacity - previousOpacity) / (1 - previousOpacity)
            NSColor.systemBlue.withAlphaComponent(contribution).setStroke()
            path.lineWidth = CGFloat(width)
            path.stroke()
            previousOpacity = opacity
        }
    }
}

final class CursorFeedbackView: NSView {
    private var visualScale: CGFloat = 0.42
    var extent: CGFloat { 144 * visualScale }
    var hotspot: CGPoint { CGPoint(x: extent / 2, y: extent / 2) }

    /// 目标边界已经是逻辑点；backing scale 由 AppKit 应用一次。
    func fit(to size: CGSize) -> Bool {
        let shorter = max(1, min(size.width, size.height))
        let scale = (min(0.448, max(0.378, 0.42 * sqrt(shorter / 720))) * 1000).rounded() / 1000
        guard visualScale != scale else { return false }
        visualScale = scale
        needsDisplay = true
        return true
    }

    override func draw(_ dirtyRect: NSRect) {
        // 系统光标由前台应用拥有；后台覆盖层只绘制透明反馈，不叠加第二个箭头。
        let color = NSColor.systemBlue
        let halo = NSGradient(colorsAndLocations: (color.withAlphaComponent(0.28), 0),
            (color.withAlphaComponent(0.13), 0.4), (color.withAlphaComponent(0.03), 0.75),
            (color.withAlphaComponent(0), 1))
        halo?.draw(fromCenter: hotspot, radius: 0, toCenter: hotspot, radius: 60 * visualScale, options: [])
    }

    func feedback() {
        guard let layer else { return }
        let diameter = 92 * visualScale
        let ripple = CAShapeLayer()
        ripple.bounds = CGRect(x: 0, y: 0, width: diameter, height: diameter)
        ripple.position = hotspot
        ripple.path = CGPath(ellipseIn: ripple.bounds, transform: nil)
        ripple.fillColor = NSColor.clear.cgColor; ripple.strokeColor = NSColor.systemBlue.cgColor
        ripple.lineWidth = 1.5 * visualScale
        layer.addSublayer(ripple)
        let expansion = CABasicAnimation(keyPath: "transform.scale")
        expansion.fromValue = 0.12; expansion.toValue = 1; expansion.duration = 0.76
        expansion.timingFunction = CAMediaTimingFunction(name: .easeOut)
        ripple.add(expansion, forKey: "expansion")
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 0.75; fade.toValue = 0; fade.duration = 0.76
        ripple.add(fade, forKey: "feedback"); ripple.opacity = 0
    }
}

/// 两个透明窗口只在一次真实输入期间可见；动画回调读取真实指针，不执行桌面输入。
@MainActor
final class Overlay: NSObject {
    private let glow: PassivePanel
    private let cursorFeedback: PassivePanel
    private let glowView = EdgeGlowView(frame: .zero)
    private let feedbackView = CursorFeedbackView(frame: .zero)
    private var clock: CADisplayLink?
    private var visible = false
    override init() {
        func panel(_ title: String) -> PassivePanel {
            let window = PassivePanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            window.title = title; window.isOpaque = false; window.backgroundColor = .clear
            window.hasShadow = false; window.ignoresMouseEvents = true; window.hidesOnDeactivate = false
            window.level = .statusBar; window.sharingType = .none
            window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
            window.isReleasedWhenClosed = false
            return window
        }
        glow = panel("Desktop operation glow"); cursorFeedback = panel("Desktop cursor feedback")
        super.init()
        glow.contentView = glowView
        cursorFeedback.setContentSize(NSSize(width: feedbackView.extent, height: feedbackView.extent))
        cursorFeedback.contentView = feedbackView
        feedbackView.wantsLayer = true
    }
    func show(bounds: CGRect, preview: Bool = false) async throws {
        guard NSApp != nil else { throw DesktopFailure("overlay_unavailable", "AppKit is unavailable.") }
        sync(bounds: bounds)
        glow.sharingType = preview ? .readOnly : .none
        cursorFeedback.sharingType = preview ? .readOnly : .none
        visible = true; tick()
        glow.alphaValue = 0; cursorFeedback.alphaValue = 0
        glow.orderFrontRegardless(); cursorFeedback.orderFrontRegardless()
        await NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.08
            glow.animator().alphaValue = 1; cursorFeedback.animator().alphaValue = 1
        }
        try Task.checkCancellation()
        clock = cursorFeedback.displayLink(target: self, selector: #selector(update(_:)))
        clock?.add(to: .main, forMode: .common)
        guard glow.isVisible, cursorFeedback.isVisible else { hide(); throw DesktopFailure("overlay_unavailable", "Desktop overlays could not be shown.") }
    }
    func sync(bounds: CGRect) {
        if feedbackView.fit(to: bounds.size) {
            cursorFeedback.setContentSize(NSSize(width: feedbackView.extent, height: feedbackView.extent))
            tick()
        }
        let frame = Coordinates.appKitRect(bounds, primaryTop: DesktopEnvironment.primaryTop).insetBy(dx: -20, dy: -20)
        glow.setFrame(frame, display: true)
        glowView.needsDisplay = true
    }
    @objc private func update(_ link: CADisplayLink) { tick() }
    private func tick() {
        guard visible else { return }
        guard let location = CGEvent(source: nil)?.location else { return }
        cursorFeedback.setFrameOrigin(NSPoint(x: location.x - feedbackView.hotspot.x,
                                             y: DesktopEnvironment.primaryTop - location.y - feedbackView.hotspot.y))
    }
    func feedback() {
        guard visible else { return }
        tick(); feedbackView.feedback()
    }
    func hide() {
        visible = false; clock?.invalidate(); clock = nil
        cursorFeedback.orderOut(nil); glow.orderOut(nil); feedbackView.layer?.sublayers?.forEach { $0.removeFromSuperlayer() }
    }
    func finish() async throws {
        visible = false; clock?.invalidate(); clock = nil
        await NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.1
            glow.animator().alphaValue = 0; cursorFeedback.animator().alphaValue = 0
        }
        try Task.checkCancellation()
        hide()
    }
    func close() { hide(); glow.close(); cursorFeedback.close() }

    static func preview(seconds: Int) async -> Int32 {
        let overlay = Overlay()
        defer { overlay.close() }
        guard let screen = NSScreen.main else { return 1 }
        let frame = Coordinates.appKitRect(screen.visibleFrame, primaryTop: DesktopEnvironment.primaryTop)
        do {
            try await overlay.show(bounds: frame.insetBy(dx: frame.width * 0.15, dy: frame.height * 0.15), preview: true)
            try await Task.sleep(nanoseconds: UInt64(seconds) * 1_000_000_000)
            try await overlay.finish()
            return 0
        } catch { return 1 }
    }
}
