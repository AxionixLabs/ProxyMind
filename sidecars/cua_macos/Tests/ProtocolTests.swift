import AppKit
import XCTest
@testable import DesktopSidecar

final class ProtocolTests: XCTestCase {
    func testStrictJSON() throws {
        for text in [#"{"v":1,"v":1}"#, #"{"a":{"b":0,"b":1}}"#, #"{"a":NaN}"#, #"{"a":01}"#,
                     #"{"a":1e999}"#, #"{"a":"\ud800"}"#, #"{"a":true,}"#] {
            var parser = try JSONParser(Data(text.utf8))
            XCTAssertThrowsError(try parser.parse(), text)
        }
        var parser = try JSONParser(Data(#"{"text":"中文🙂","n":-1,"b":true}"#.utf8))
        XCTAssertEqual(try parser.parse()["text"], .string("中文🙂"))
        XCTAssertThrowsError(try JSON.bool(true).integer(0...1))
    }
    func testFrameBoundaries() throws {
        var decoder = FrameDecoder()
        XCTAssertEqual(try decoder.append(Data("one\ntw".utf8)), [Data("one".utf8)])
        XCTAssertThrowsError(try decoder.finish())
        XCTAssertEqual(try decoder.append(Data("o\n".utf8)), [Data("two".utf8)])
        XCTAssertNoThrow(try decoder.finish())
        XCTAssertNoThrow(try decoder.append(Data(repeating: 32, count: Wire.requestLimit - 1)))
        XCTAssertEqual(try decoder.append(Data([10])).first?.count, Wire.requestLimit - 1)
        XCTAssertThrowsError(try decoder.append(Data(repeating: 32, count: Wire.requestLimit)))
    }
    func testRetinaNegativeCoordinates() throws {
        let bounds = CGRect(x: -1400, y: -300, width: 800, height: 600)
        XCTAssertEqual(try Coordinates.imagePoint(x: 800, y: 600, width: 1600, height: 1200, bounds: bounds),
                       CGPoint(x: -1000, y: 0))
        XCTAssertThrowsError(try Coordinates.imagePoint(x: 1600, y: 0, width: 1600, height: 1200, bounds: bounds))
        let converted = Coordinates.appKitRect(bounds, primaryTop: 900)
        XCTAssertEqual(Coordinates.appKitRect(converted, primaryTop: 900), bounds)
    }
    func testRequestAndInputRejectUnknownAndMissingFields() throws {
        for text in [#"{"v":1,"id":"a","action":"hello"}"#, #"{"v":true,"id":"a","action":"hello","parameters":{}}"#,
                     #"{"v":1,"id":"a","action":"hello","parameters":{},"extra":1}"#] {
            var parser = try JSONParser(Data(text.utf8))
            XCTAssertThrowsError(try Request(parser.parse()))
        }
        for text in [#"{"kind":"press_key","keys":["A","A"]}"#, #"{"kind":"type_text","text":"\u0000"}"#,
                     #"{"kind":"scroll","target":{"x":1,"y":1},"deltaX":0,"deltaY":0}"#,
                     #"{"kind":"move","target":{"element":1},"durationMs":10}"#] {
            var parser = try JSONParser(Data(text.utf8))
            XCTAssertThrowsError(try InputOperation(parser.parse()))
        }
        XCTAssertEqual(try KeyMap.code("WIN"), 55)
        XCTAssertThrowsError(try KeyMap.code("F24"))
    }

    @MainActor
    func testGlowCornersRemainSoftWithoutCrossingBands() throws {
        let view = EdgeGlowView(frame: NSRect(x: 0, y: 0, width: 320, height: 240))
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 320, pixelsHigh: 240,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        let pixels = try XCTUnwrap(bitmap.bitmapData)
        pixels.initialize(repeating: 0, count: bitmap.bytesPerRow * bitmap.pixelsHigh)
        let context = try XCTUnwrap(NSGraphicsContext(bitmapImageRep: bitmap))
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        view.draw(view.bounds)
        NSGraphicsContext.restoreGraphicsState()
        let edge = try XCTUnwrap(bitmap.colorAt(x: 160, y: 20)).alphaComponent
        XCTAssertGreaterThan(edge, 0.35)
        XCTAssertLessThan(edge, 0.5)
        XCTAssertEqual(try XCTUnwrap(bitmap.colorAt(x: 160, y: 120)).alphaComponent, 0)
        for x in 0..<60 {
            for y in 0..<60 {
                let alpha = try XCTUnwrap(bitmap.colorAt(x: x, y: y)).alphaComponent
                XCTAssertLessThanOrEqual(alpha, edge + 0.03, "Corner opacity spike at \(x),\(y)")
            }
        }
        // 原来的横、纵渐变条会沿目标边线继续穿过圆角外侧。
        let outsideCorner = try XCTUnwrap(bitmap.colorAt(x: 5, y: 20)).alphaComponent
        let besideEdge = try XCTUnwrap(bitmap.colorAt(x: 5, y: 120)).alphaComponent
        XCTAssertLessThan(outsideCorner, besideEdge)
    }

    @MainActor
    func testCursorFeedbackIsTranslucentAndCentered() throws {
        let view = CursorFeedbackView(frame: .zero)
        view.setFrameSize(NSSize(width: view.extent, height: view.extent))
        let size = Int(ceil(view.extent))
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        let pixels = try XCTUnwrap(bitmap.bitmapData)
        pixels.initialize(repeating: 0, count: bitmap.bytesPerRow * bitmap.pixelsHigh)
        let context = try XCTUnwrap(NSGraphicsContext(bitmapImageRep: bitmap))
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        view.draw(view.bounds)
        NSGraphicsContext.restoreGraphicsState()
        let center = try XCTUnwrap(bitmap.colorAt(x: size / 2, y: size / 2)).alphaComponent
        XCTAssertGreaterThan(center, 0.2)
        for x in 0..<size {
            for y in 0..<size {
                XCTAssertLessThanOrEqual(try XCTUnwrap(bitmap.colorAt(x: x, y: y)).alphaComponent, 0.3)
            }
        }
        XCTAssertEqual(try XCTUnwrap(bitmap.colorAt(x: 0, y: 0)).alphaComponent, 0)
        view.wantsLayer = true
        view.feedback()
        let ripple = try XCTUnwrap(view.layer?.sublayers?.first as? CAShapeLayer)
        XCTAssertEqual(ripple.position.x, view.bounds.midX)
        XCTAssertEqual(ripple.position.y, view.bounds.midY)
    }
}
