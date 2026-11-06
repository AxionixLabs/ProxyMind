import Foundation

/// 读取线程只拥有通信终止状态，原生窗口和输入资源仍由主线程关闭。
final class Lifetime: @unchecked Sendable {
    private let lock = NSLock()
    private var inputClosed = false
    private var stopped = false
    private var terminationDeadline: TimeInterval?
    private var requestDeadline: TimeInterval?
    var inputCancelled: Bool { lock.lock(); defer { lock.unlock() }; return inputClosed || stopped }
    var isStopped: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    func cancelInput() {
        lock.lock(); defer { lock.unlock() }
        inputClosed = true
        if terminationDeadline == nil { terminationDeadline = ProcessInfo.processInfo.systemUptime + 2 }
    }
    func stop() {
        lock.lock(); defer { lock.unlock() }
        stopped = true; inputClosed = true
        if terminationDeadline == nil { terminationDeadline = ProcessInfo.processInfo.systemUptime + 1.5 }
    }
    func beginRequest() { lock.lock(); requestDeadline = ProcessInfo.processInfo.systemUptime + 25; lock.unlock() }
    func endRequest() { lock.lock(); requestDeadline = nil; lock.unlock() }
    func expired() -> Bool {
        lock.lock(); defer { lock.unlock() }
        let now = ProcessInfo.processInfo.systemUptime
        return terminationDeadline.map { now >= $0 } == true || requestDeadline.map { now >= $0 } == true
    }
}

enum Transport {
    // 系统 AX / 捕获调用不保证响应取消；独立时钟终止卡住的进程，释放守护仍负责按下状态。
    static func watch(parent: pid_t, lifetime: Lifetime) {
        DispatchQueue.global(qos: .utility).async {
            while true {
                if getppid() != parent || (kill(parent, 0) != 0 && errno != EPERM) { lifetime.stop() }
                if lifetime.expired() { _exit(1) }
                usleep(100_000)
            }
        }
    }
    static func frames(parent: pid_t, lifetime: Lifetime) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream(bufferingPolicy: .bufferingOldest(16)) { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                var decoder = FrameDecoder(), buffer = [UInt8](repeating: 0, count: 8192)
                defer { lifetime.cancelInput(); continuation.finish() }
                do {
                    while !lifetime.isStopped {
                        guard getppid() == parent, kill(parent, 0) == 0 || errno == EPERM else { lifetime.stop(); return }
                        var descriptor = pollfd(fd: STDIN_FILENO, events: Int16(POLLIN), revents: 0)
                        let ready = poll(&descriptor, 1, 100)
                        if ready < 0 { if errno == EINTR { continue }; throw DesktopFailure("transport_closed", "Input polling failed.") }
                        if ready == 0 { continue }
                        let size = read(STDIN_FILENO, &buffer, buffer.count)
                        if size == 0 { try decoder.finish(); return }
                        if size < 0 { if errno == EINTR { continue }; throw DesktopFailure("transport_closed", "Input read failed.") }
                        for frame in try decoder.append(Data(buffer.prefix(size))) {
                            if case .dropped = continuation.yield(frame) {
                                throw DesktopFailure("request_limit", "Too many queued desktop requests.")
                            }
                        }
                    }
                } catch { continuation.finish(throwing: error) }
            }
        }
    }
    static func write(_ data: Data, lifetime: Lifetime) async throws {
        try await Task.detached {
            try boundedWrite(data, descriptor: STDOUT_FILENO, lifetime: lifetime)
        }.value
    }
    static func boundedWrite(_ data: Data, descriptor: Int32, lifetime: Lifetime? = nil) throws {
        let deadline = ProcessInfo.processInfo.systemUptime + 5
        var offset = 0
        try data.withUnsafeBytes { bytes in
            while offset < bytes.count {
                guard lifetime?.isStopped != true, ProcessInfo.processInfo.systemUptime < deadline else {
                    throw DesktopFailure("transport_closed", "Output blocked or parent exited.")
                }
                var descriptorState = pollfd(fd: descriptor, events: Int16(POLLOUT), revents: 0)
                let ready = poll(&descriptorState, 1, 50)
                if ready < 0 && errno == EINTR { continue }
                guard ready >= 0 else { throw DesktopFailure("transport_closed", "Output polling failed.") }
                if ready == 0 { continue }
                let count = Darwin.write(descriptor, bytes.baseAddress?.advanced(by: offset), bytes.count - offset)
                if count < 0 && [EAGAIN, EINTR].contains(errno) { continue }
                guard count > 0 else { throw DesktopFailure("transport_closed", "Output pipe closed.") }
                offset += count
            }
        }
    }
    static func nonblocking(_ descriptor: Int32) { _ = fcntl(descriptor, F_SETFL, fcntl(descriptor, F_GETFL) | O_NONBLOCK) }
}
