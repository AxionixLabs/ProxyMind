import Foundation

struct DesktopFailure: Error {
    let code: String
    let message: String
    init(_ code: String, _ message: String) { self.code = code; self.message = message }
}

/// 私有协议的有界值；外部 JSON 不穿透到原生业务对象。
indirect enum JSON: Encodable, Equatable {
    case object([String: JSON]), array([JSON]), string(String), integer(Int), number(Double), bool(Bool), null

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .integer(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    func fields(_ names: String...) throws -> [String: JSON] {
        guard case .object(let value) = self, Set(value.keys) == Set(names) else {
            throw DesktopFailure("invalid_request", "Unknown, missing or non-object fields.")
        }
        return value
    }

    func string(limit: Int, empty: Bool = false) throws -> String {
        guard case .string(let value) = self, value.utf16.count <= limit,
              empty || !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw DesktopFailure("invalid_request", "Invalid string field.")
        }
        return value
    }

    func integer(_ range: ClosedRange<Int>) throws -> Int {
        guard case .integer(let value) = self, range.contains(value) else {
            throw DesktopFailure("invalid_request", "Invalid integer field.")
        }
        return value
    }

    func boolean() throws -> Bool {
        guard case .bool(let value) = self else { throw DesktopFailure("invalid_request", "Expected a boolean.") }
        return value
    }

    subscript(_ name: String) -> JSON {
        guard case .object(let fields) = self else { return .null }
        return fields[name] ?? .null
    }
}

/// 逐层拒绝重复键、非有限数字、无效 Unicode 和过深嵌套。
struct JSONParser {
    private let bytes: [UInt8]
    private var index = 0
    init(_ data: Data) throws {
        guard String(data: data, encoding: .utf8) != nil else {
            throw DesktopFailure("invalid_request", "Invalid UTF-8.")
        }
        bytes = Array(data)
    }
    mutating func parse() throws -> JSON {
        let result = try value(depth: 0)
        whitespace()
        guard index == bytes.count else { throw invalid() }
        return result
    }
    private func invalid() -> DesktopFailure { DesktopFailure("invalid_request", "Invalid JSON request.") }
    private mutating func whitespace() {
        while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 }
    }
    private mutating func consume(_ byte: UInt8) -> Bool {
        whitespace()
        guard index < bytes.count, bytes[index] == byte else { return false }
        index += 1; return true
    }
    private mutating func string() throws -> String {
        whitespace()
        let start = index
        guard consume(34) else { throw invalid() }
        while index < bytes.count {
            let byte = bytes[index]; index += 1
            if byte == 34 {
                do { return try JSONDecoder().decode(String.self, from: Data(bytes[start..<index])) }
                catch { throw invalid() }
            }
            if byte < 32 { throw invalid() }
            if byte == 92 { index += 1 }
        }
        throw invalid()
    }
    private mutating func value(depth: Int) throws -> JSON {
        whitespace()
        guard depth <= 16, index < bytes.count else { throw invalid() }
        switch bytes[index] {
        case 123:
            index += 1
            var result: [String: JSON] = [:]
            if consume(125) { return .object(result) }
            repeat {
                let key = try string()
                guard result[key] == nil, consume(58) else { throw invalid() }
                result[key] = try value(depth: depth + 1)
                if consume(125) { return .object(result) }
                guard consume(44) else { throw invalid() }
            } while true
        case 91:
            index += 1
            var result: [JSON] = []
            if consume(93) { return .array(result) }
            repeat {
                result.append(try value(depth: depth + 1))
                if consume(93) { return .array(result) }
                guard consume(44) else { throw invalid() }
            } while true
        case 34: return .string(try string())
        case 116: try literal("true"); return .bool(true)
        case 102: try literal("false"); return .bool(false)
        case 110: try literal("null"); return .null
        default:
            let start = index
            while index < bytes.count, ![9, 10, 13, 32, 44, 93, 125].contains(bytes[index]) { index += 1 }
            let text = String(decoding: bytes[start..<index], as: UTF8.self)
            guard text.range(of: #"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$"#, options: .regularExpression) != nil,
                  let number = Double(text), number.isFinite else { throw invalid() }
            if !text.contains("."), !text.lowercased().contains("e"), let number = Int(text) { return .integer(number) }
            return .number(number)
        }
    }
    private mutating func literal(_ text: String) throws {
        let expected = Array(text.utf8)
        guard index + expected.count <= bytes.count,
              Array(bytes[index..<index + expected.count]) == expected else { throw invalid() }
        index += expected.count
    }
}

struct Request {
    let id: String
    let action: String
    let parameters: JSON
    init(_ value: JSON) throws {
        _ = try value.fields("v", "id", "action", "parameters")
        id = try value["id"].string(limit: 128)
        guard case .integer(let version) = value["v"] else { throw DesktopFailure("invalid_request", "Expected an integer protocol version.") }
        guard version == 1 else { throw DesktopFailure("protocol_version", "Unsupported protocol.") }
        action = try value["action"].string(limit: 32)
        guard case .object = value["parameters"] else { throw DesktopFailure("invalid_request", "Expected parameters.") }
        parameters = value["parameters"]
    }
}

enum Wire {
    static let requestLimit = 64 * 1024
    static let responseLimit = 32 * 1024 * 1024
    static func response(id: String?, result: JSON?, error: DesktopFailure? = nil) throws -> Data {
        var fields: [String: JSON] = ["v": .integer(1), "id": id.map(JSON.string) ?? .null]
        if let error {
            fields["ok"] = .bool(false)
            fields["error"] = .object(["code": .string(error.code), "message": .string(error.message)])
        } else {
            fields["ok"] = .bool(true); fields["result"] = result ?? .object([:])
        }
        var data = try JSONEncoder().encode(JSON.object(fields)); data.append(10)
        guard data.count <= responseLimit else { throw DesktopFailure("response_limit", "Response exceeds limit.") }
        return data
    }
    static func diagnose(id: String?, action: String?, code: String, phase: String) {
        let value = JSON.object(["id": id.map(JSON.string) ?? .null, "action": action.map(JSON.string) ?? .null,
                                 "phase": .string(phase), "code": .string(code)])
        guard var data = try? JSONEncoder().encode(value) else { return }
        data.append(10)
        // 非阻塞诊断不能阻止输入释放或进程退出。
        data.withUnsafeBytes { buffer in _ = Darwin.write(STDERR_FILENO, buffer.baseAddress, buffer.count) }
    }
}

struct FrameDecoder {
    private var pending = Data()
    mutating func append(_ data: Data) throws -> [Data] {
        var frames: [Data] = []
        for byte in data {
            if byte == 10 { frames.append(pending); pending.removeAll(keepingCapacity: true) }
            else {
                guard pending.count < Wire.requestLimit - 1 else { throw DesktopFailure("request_limit", "Request exceeds limit.") }
                pending.append(byte)
            }
        }
        return frames
    }
    func finish() throws {
        guard pending.isEmpty else { throw DesktopFailure("truncated_frame", "EOF in request frame.") }
    }
}
