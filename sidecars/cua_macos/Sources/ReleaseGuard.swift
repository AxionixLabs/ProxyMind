import AppKit
import ApplicationServices

/// 独立守护进程持有输入租约与按下记录，父进程崩溃后先释放再退出。
enum ReleaseGuard {
    static func run(parent: pid_t) -> Int32 {
        let path = "/tmp/com.axionixlabs.desktop-sidecar-input-\(getuid()).lock"
        let descriptor = open(path, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { return 1 }
        defer { close(descriptor) }
        var metadata = stat()
        guard fstat(descriptor, &metadata) == 0, metadata.st_uid == getuid(), metadata.st_mode & S_IFMT == S_IFREG,
              metadata.st_mode & 0o077 == 0 else { return 1 }
        var locked = false, held: [String] = [], tag: Int64 = 0
        func release(_ item: String, clickCount: Int64 = 1) {
            let pieces = item.split(separator: ":")
            guard pieces.count == 2, let number = UInt16(pieces[1]) else { return }
            let event: CGEvent?
            if pieces[0] == "key" {
                event = CGEvent(keyboardEventSource: nil, virtualKey: number, keyDown: false)
                let remaining = held.filter { $0 != item && $0.hasPrefix("key:") }.compactMap { UInt16($0.dropFirst(4)) }
                event?.flags = KeyMap.flags(remaining)
            }
            else if pieces[0] == "mouse", let button = CGMouseButton(rawValue: UInt32(number)) {
                let type: CGEventType = button == .left ? .leftMouseUp : button == .right ? .rightMouseUp : .otherMouseUp
                event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: CGEvent(source: nil)?.location ?? .zero, mouseButton: button)
                event?.setIntegerValueField(.mouseEventClickState, value: clickCount)
            } else { return }
            event?.setIntegerValueField(.eventSourceUserData, value: tag); event?.post(tap: .cghidEventTap)
        }
        func isPressed(_ item: String) -> Bool {
            let parts = item.split(separator: ":")
            guard parts.count == 2, let number = UInt16(parts[1]) else { return false }
            if parts[0] == "key" { return CGEventSource.keyState(.combinedSessionState, key: number) }
            if parts[0] == "mouse", let button = CGMouseButton(rawValue: UInt32(number)) {
                return CGEventSource.buttonState(.combinedSessionState, button: button)
            }
            return false
        }
        func cleanup() {
            let releasing = held
            while let item = held.last { release(item); held.removeLast() }
            // CGEventPost 异步送达；发送端立即退出会丢失最终抬起事件。
            let deadline = ProcessInfo.processInfo.systemUptime + 0.5
            while releasing.contains(where: isPressed) && ProcessInfo.processInfo.systemUptime < deadline { usleep(5_000) }
            if releasing.contains(where: isPressed) {
                Wire.diagnose(id: nil, action: "release", code: "input_release_unconfirmed", phase: "cleanup")
            }
            if locked { flock(descriptor, LOCK_UN); locked = false }
        }
        defer { cleanup() }
        var decoder = FrameDecoder(), bytes = [UInt8](repeating: 0, count: 512)
        do {
            while getppid() == parent && (kill(parent, 0) == 0 || errno == EPERM) {
                var state = pollfd(fd: STDIN_FILENO, events: Int16(POLLIN), revents: 0)
                let ready = poll(&state, 1, 100)
                if ready == 0 { continue }
                if ready < 0 { if errno == EINTR { continue }; return 1 }
                let count = read(STDIN_FILENO, &bytes, bytes.count)
                if count == 0 { try decoder.finish(); return 0 }
                if count < 0 { if errno == EINTR { continue }; return 1 }
                for frame in try decoder.append(Data(bytes.prefix(count))) {
                    guard frame.count < 128, let command = String(data: frame, encoding: .utf8) else { return 1 }
                    let parts = command.split(separator: " ")
                    var reply = "ok"
                    switch parts.first {
                    case "begin":
                        guard parts.count == 2, !locked, let value = Int64(parts[1]) else { return 1 }
                        if flock(descriptor, LOCK_EX | LOCK_NB) != 0 { reply = "busy" }
                        else { locked = true; tag = value }
                    case "hold":
                        guard locked, parts.count == 2, held.count < 8, !held.contains(String(parts[1])) else { return 1 }
                        let key = parts[1].split(separator: ":")
                        guard key.count == 2, let code = UInt16(key[1]),
                              (key[0] == "key" && code < 128) || (key[0] == "mouse" && code < 3) else { return 1 }
                        held.append(String(parts[1]))
                    case "release":
                        guard locked, (2...3).contains(parts.count), held.contains(String(parts[1])) else { return 1 }
                        let clicks = parts.count == 3 ? Int64(parts[2]) : 1
                        guard let clicks, (1...2).contains(clicks) else { return 1 }
                        release(String(parts[1]), clickCount: clicks); held.removeAll { $0 == parts[1] }
                    case "end": guard parts.count == 1 else { return 1 }; cleanup()
                    default: return 1
                    }
                    try Transport.boundedWrite(Data((reply + "\n").utf8), descriptor: STDOUT_FILENO)
                }
            }
        } catch { return 1 }
        return 0
    }
}

@MainActor
final class GuardClient {
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private var closed = false
    init(tag: Int64) throws {
        process.executableURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        process.arguments = ["--release-input", "--parent-pid", String(getpid())]
        process.standardInput = input; process.standardOutput = output; process.standardError = FileHandle.standardError
        do { try process.run(); try command("begin \(tag)") }
        catch { close(); throw error }
    }
    func command(_ command: String) throws {
        guard !closed, process.isRunning else { throw DesktopFailure("input_guard_unavailable", "Input release guard exited.") }
        do { try input.fileHandleForWriting.write(contentsOf: Data((command + "\n").utf8)) }
        catch { throw DesktopFailure("input_guard_unavailable", "Input release guard pipe closed.") }
        let deadline = ProcessInfo.processInfo.systemUptime + 1
        var reply = Data()
        while ProcessInfo.processInfo.systemUptime < deadline {
            var descriptor = pollfd(fd: output.fileHandleForReading.fileDescriptor, events: Int16(POLLIN), revents: 0)
            let ready = poll(&descriptor, 1, 20)
            if ready < 0 && errno == EINTR { continue }
            guard ready >= 0 else { break }
            if ready == 0 { continue }
            var byte: UInt8 = 0
            guard read(descriptor.fd, &byte, 1) == 1 else { break }
            if byte == 10 {
                if reply == Data("ok".utf8) { return }
                if reply == Data("busy".utf8) { throw DesktopFailure("desktop_busy", "Another process owns desktop input.") }
                break
            }
            reply.append(byte)
            if reply.count > 32 { break }
        }
        throw DesktopFailure("input_guard_unavailable", "Input release guard did not acknowledge the operation.")
    }
    func close() {
        guard !closed else { return }; closed = true
        try? input.fileHandleForWriting.close()
        // 子进程最多每 100ms 检查 EOF；不在退出前杀死负责释放按键的守护。
        let deadline = ProcessInfo.processInfo.systemUptime + 1.5
        while process.isRunning && ProcessInfo.processInfo.systemUptime < deadline { usleep(10_000) }
        try? output.fileHandleForReading.close()
        if process.isRunning { Wire.diagnose(id: nil, action: "close", code: "guard_cleanup_pending", phase: "cleanup") }
    }
}
