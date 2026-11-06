import AppKit

@MainActor
final class Driver {
    private let lifetime: Lifetime
    private let windows: Windows
    private let input: Input
    private var ids = Set<String>()
    private var diagnostics = 0
    init(lifetime: Lifetime) {
        self.lifetime = lifetime; windows = Windows(); input = Input(windows: windows, lifetime: lifetime)
    }
    func run(parent: pid_t) async -> Int32 {
        defer { lifetime.stop(); input.close(); windows.close() }
        do {
            for try await frame in Transport.frames(parent: parent, lifetime: lifetime) {
                if lifetime.isStopped { break }
                lifetime.beginRequest()
                defer { lifetime.endRequest() }
                var id: String?, action: String?
                let response: Data
                do {
                    var parser = try JSONParser(frame)
                    let value = try parser.parse()
                    id = try? value["id"].string(limit: 128)
                    let request = try Request(value)
                    guard ids.count < 10_000 else { throw DesktopFailure("session_limit", "Start a new desktop session.") }
                    guard ids.insert(request.id).inserted else { throw DesktopFailure("duplicate_request", "Request was already used; actions are never replayed.") }
                    action = request.action
                    let result = try await dispatch(request)
                    response = try Wire.response(id: request.id, result: result)
                } catch let failure as DesktopFailure {
                    if diagnostics < 256 { Wire.diagnose(id: id, action: action, code: failure.code, phase: phase(action)); diagnostics += 1 }
                    response = try Wire.response(id: id, result: nil, error: failure)
                }
                try await Transport.write(response, lifetime: lifetime)
            }
            return 0
        } catch let failure as DesktopFailure {
            Wire.diagnose(id: nil, action: nil, code: failure.code, phase: "communication"); return 1
        } catch {
            Wire.diagnose(id: nil, action: nil, code: "internal_error", phase: "communication"); return 1
        }
    }
    private func phase(_ action: String?) -> String {
        switch action {
        case "hello": return "startup"
        case "list_windows", "observe", "validate_observation": return "observation"
        case "activate", "input": return "input"
        default: return "communication"
        }
    }
    private func dispatch(_ request: Request) async throws -> JSON {
        let p = request.parameters
        switch request.action {
        case "hello":
            _ = try p.fields()
            let permissions = Permissions.current()
            let actions = ["list_windows", "observe", "validate_observation"] + (permissions.input ? ["activate", "input"] : [])
            return .object(["protocol": .integer(1), "platform": .string("macos"), "actions": .array(actions.map(JSON.string)),
                "coordinateSpace": .string("logical_screen_points"), "captureSupported": .bool(permissions.capture), "inputSupported": .bool(permissions.input)])
        case "list_windows": _ = try p.fields(); return try windows.list()
        case "observe":
            _ = try p.fields("window", "screenshot")
            return try await windows.observe(p["window"].string(limit: 64), screenshot: p["screenshot"].boolean())
        case "validate_observation":
            _ = try p.fields("window", "observation")
            _ = try windows.validate(p["window"].string(limit: 64), observation: p["observation"].string(limit: 64))
            return .object([:])
        case "activate": _ = try p.fields("window"); return try await input.activate(p["window"].string(limit: 64))
        case "input":
            _ = try p.fields("window", "observation", "operation")
            return try await input.perform(p["window"].string(limit: 64), observation: p["observation"].string(limit: 64), operation: p["operation"])
        default: throw DesktopFailure("unsupported_action", "Action is not supported.")
        }
    }
}

@main
enum DesktopApplication {
    @MainActor static func main() {
        signal(SIGPIPE, SIG_IGN)
        Transport.nonblocking(STDOUT_FILENO); Transport.nonblocking(STDERR_FILENO)
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.count == 3, arguments[0] == "--release-input", arguments[1] == "--parent-pid",
           let parent = pid_t(arguments[2]), parent > 0 { exit(ReleaseGuard.run(parent: parent)) }
        if arguments == ["--request-permissions"] {
            _ = NSApplication.shared
            _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
            _ = CGRequestScreenCaptureAccess(); _ = CGRequestListenEventAccess(); _ = CGRequestPostEventAccess()
            exit(0)
        }
        if arguments.count == 2, arguments[0] == "--preview", let seconds = Int(arguments[1]), (1...30).contains(seconds) {
            let application = NSApplication.shared
            application.setActivationPolicy(.accessory)
            Task { @MainActor in exit(await Overlay.preview(seconds: seconds)) }
            application.run(); return
        }
        guard arguments.count == 2, arguments[0] == "--parent-pid", let parent = pid_t(arguments[1]), parent > 0 else {
            Wire.diagnose(id: nil, action: nil, code: "invalid_arguments", phase: "startup"); exit(2)
        }
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        let lifetime = Lifetime()
        Transport.watch(parent: parent, lifetime: lifetime)
        let sources = [SIGTERM, SIGINT].map { signalNumber -> DispatchSourceSignal in
            signal(signalNumber, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .global())
            source.setEventHandler { lifetime.stop() }; source.resume(); return source
        }
        Task { @MainActor in
            let code = await Driver(lifetime: lifetime).run(parent: parent)
            for source in sources { source.cancel() }
            exit(code)
        }
        application.run()
    }
}
