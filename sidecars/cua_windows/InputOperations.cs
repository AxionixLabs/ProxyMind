using System;
using System.Linq;
using System.Text.Json;

namespace DesktopSidecar;

/// <summary>在输入发生前严格解析动作形状和边界，不接受可执行代码或开放式原生消息。</summary>
internal static class InputOperations
{
    internal static Action Prepare(JsonElement operation, NativeInput input,
        Func<JsonElement, NativeInput.Point> resolve, Action<int, int> hit)
    {
        string kind = Program.RequiredString(operation, "kind", 32);
        switch (kind)
        {
            case "click":
            {
                Program.ValidateFields(operation, "kind", "target", "button", "count");
                NativeInput.Point point = resolve(operation.GetProperty("target"));
                string button = Program.RequiredString(operation, "button", 8);
                if (button is not ("left" or "right" or "middle")) throw new DesktopError("invalid_request", "Invalid mouse button.");
                int count = Integer(operation, "count", 1, 2);
                return () => { input.Move(point.X, point.Y, 160); input.Click(button, count, () => hit(point.X, point.Y)); };
            }
            case "move":
            {
                Program.ValidateFields(operation, "kind", "target", "durationMs");
                Program.ValidateFields(operation.GetProperty("target"), "x", "y");
                NativeInput.Point point = resolve(operation.GetProperty("target"));
                int duration = Integer(operation, "durationMs", 0, 1200);
                return () => { input.Move(point.X, point.Y, duration); hit(point.X, point.Y); };
            }
            case "drag":
            {
                Program.ValidateFields(operation, "kind", "start", "end", "durationMs");
                Program.ValidateFields(operation.GetProperty("start"), "x", "y");
                Program.ValidateFields(operation.GetProperty("end"), "x", "y");
                NativeInput.Point start = resolve(operation.GetProperty("start")), end = resolve(operation.GetProperty("end"));
                int duration = Integer(operation, "durationMs", 50, 1200);
                return () =>
                {
                    input.Move(start.X, start.Y, 160);
                    hit(start.X, start.Y);
                    input.Hold("mouse:left");
                    try { input.Move(end.X, end.Y, duration, hit); }
                    finally { input.Release("mouse:left"); }
                };
            }
            case "scroll":
            {
                Program.ValidateFields(operation, "kind", "target", "deltaX", "deltaY");
                Program.ValidateFields(operation.GetProperty("target"), "x", "y");
                NativeInput.Point point = resolve(operation.GetProperty("target"));
                int x = Integer(operation, "deltaX", -20, 20), y = Integer(operation, "deltaY", -20, 20);
                if (x == 0 && y == 0) throw new DesktopError("invalid_request", "Scroll must have a nonzero delta.");
                return () => { input.Move(point.X, point.Y, 160); hit(point.X, point.Y); input.Scroll(x, y); };
            }
            case "press_key":
            {
                Program.ValidateFields(operation, "kind", "keys");
                JsonElement keys = operation.GetProperty("keys");
                if (keys.ValueKind != JsonValueKind.Array || keys.GetArrayLength() is < 1 or > 4)
                    throw new DesktopError("invalid_request", "Expected one to four named keys.");
                ushort[] codes = keys.EnumerateArray().Select(key => NativeInput.KeyCode(key.GetString() ?? "")).ToArray();
                if (codes.Distinct().Count() != codes.Length) throw new DesktopError("invalid_request", "Duplicate key.");
                return () => input.Keys(codes);
            }
            case "type_text":
            {
                Program.ValidateFields(operation, "kind", "text");
                string text = operation.GetProperty("text").GetString() ?? "";
                if (text.Length is < 1 or > 2048 || text.Contains('\0')) throw new DesktopError("invalid_request", "Text length must be 1..2048 UTF-16 units without NUL.");
                return () => input.Text(text);
            }
            default: throw new DesktopError("unsupported_action", "Unsupported input operation.");
        }
    }

    private static int Integer(JsonElement value, string field, int minimum, int maximum)
    {
        int result = value.GetProperty(field).GetInt32();
        if (result < minimum || result > maximum) throw new DesktopError("invalid_request", "Input integer is outside its bounds.");
        return result;
    }
}
