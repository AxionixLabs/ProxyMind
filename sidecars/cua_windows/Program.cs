using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;

namespace DesktopSidecar;

internal static class Program
{
    private const int ResponseLimit = 32 * 1024 * 1024;

    private static async Task<int> Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--release-input") return NativeInput.RunReleaseGuard();
        if (args.Length == 3 && args[0] == "--system-cursor-manager" && args[1] == "--parent-pid" &&
            int.TryParse(args[2], out int cursorManagerParent) && cursorManagerParent > 0)
            return await SystemCursorManager.Run(cursorManagerParent);
        if (args.Length > 0 && args[0] == "--preview-glow")
        {
            if (args.Length != 2 || !int.TryParse(args[1], out int seconds) || seconds is < 1 or > 120)
            {
                Console.Error.WriteLine("Expected --preview-glow <seconds: 1..120>.");
                return 2;
            }
            return GlowPreview.Run(seconds);
        }
        if (args.Length != 2 || args[0] != "--parent-pid" ||
            !int.TryParse(args[1], out int parentId) || parentId <= 0)
        {
            Console.Error.WriteLine("Expected --parent-pid <positive process id>.");
            return 2;
        }
        using var lifetime = new CancellationTokenSource();
        Task parent = WatchParent(parentId, lifetime);
        NativeWindows.EnablePhysicalCoordinates();
        using var windows = new NativeWindows();
        using Stream input = Console.OpenStandardInput();
        var frames = new RequestFrameReader(input);
        using var inputLifetime = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        var pending = Channel.CreateBounded<byte[]>(new BoundedChannelOptions(1) { SingleReader = true, SingleWriter = true });
        Task reading = ReadFrames(frames, pending.Writer, lifetime.Token, inputLifetime);
        using Stream output = Console.OpenStandardOutput();
        var ids = new HashSet<string>(StringComparer.Ordinal);
        try
        {
            await foreach (byte[] frame in pending.Reader.ReadAllAsync(lifetime.Token))
            {
                string? id = null;
                string? action = null;
                JsonObject response;
                try
                {
                    using JsonDocument document = JsonDocument.Parse(frame,
                        new JsonDocumentOptions { MaxDepth = 16 });
                    ValidateFields(document.RootElement, "v", "id", "action", "parameters");
                    JsonElement request = document.RootElement;
                    id = RequiredString(request, "id", 128);
                    if (request.GetProperty("v").GetInt32() != 1)
                        throw new DesktopError("protocol_version", "Unsupported protocol version.");
                    if (ids.Count >= 10000)
                        throw new DesktopError("session_limit", "Start a new sidecar session.");
                    if (!ids.Add(id))
                        throw new DesktopError("duplicate_request", "Request id already used; actions are never replayed.");
                    action = RequiredString(request, "action", 32);
                    JsonElement parameters = request.GetProperty("parameters");
                    JsonNode result = await Dispatch(windows, action, parameters,
                        action is "input" or "activate" ? inputLifetime.Token : lifetime.Token);
                    response = new JsonObject { ["v"] = 1, ["id"] = id, ["ok"] = true, ["result"] = result };
                }
                catch (DesktopError error)
                {
                    response = Failure(id, error.Code, error.Message);
                    Diagnose(id, action, error.Code);
                }
                catch (Exception error) when (error is JsonException or InvalidOperationException or
                    KeyNotFoundException or FormatException or OverflowException)
                {
                    response = Failure(id, "invalid_request", "Request does not match the protocol.");
                    Diagnose(id, action, "invalid_request");
                }
                byte[] bytes = Encoding.UTF8.GetBytes(response.ToJsonString() + "\n");
                if (bytes.Length > ResponseLimit)
                    bytes = Encoding.UTF8.GetBytes(Failure(id, "response_limit", "Response exceeds size limit.").ToJsonString() + "\n");
                await output.WriteAsync(bytes, lifetime.Token);
                await output.FlushAsync(lifetime.Token);
            }
            return 0;
        }
        catch (Exception error) when (error is IOException or OperationCanceledException or DesktopError)
        {
            Diagnose(null, "transport", error is DesktopError desktop ? desktop.Code : error.GetType().Name);
            return 1;
        }
        finally
        {
            lifetime.Cancel();
            await parent;
            await reading;
        }
    }

    private static async Task ReadFrames(RequestFrameReader frames, ChannelWriter<byte[]> writer,
        CancellationToken cancellation, CancellationTokenSource inputLifetime)
    {
        try
        {
            while (await frames.Read(cancellation) is byte[] frame)
                await writer.WriteAsync(frame, cancellation);
            writer.TryComplete();
        }
        catch (Exception error) when (error is IOException or OperationCanceledException or DesktopError)
        {
            writer.TryComplete(error);
        }
        finally { inputLifetime.Cancel(); }
    }

    private static async Task<JsonNode> Dispatch(
        NativeWindows windows, string action, JsonElement parameters, CancellationToken cancellation)
    {
        switch (action)
        {
            case "hello":
                ValidateFields(parameters);
                return new JsonObject
                {
                    ["protocol"] = 1, ["platform"] = "windows",
                    ["actions"] = new JsonArray("list_windows", "observe", "validate_observation", "activate", "input"),
                    ["coordinateSpace"] = "physical_screen_pixels",
                    ["captureSupported"] = WindowCapture.IsSupported,
                    ["inputSupported"] = true,
                };
            case "list_windows":
                ValidateFields(parameters);
                return windows.List();
            case "observe":
                ValidateFields(parameters, "window", "screenshot");
                string reference = RequiredString(parameters, "window", 64);
                bool screenshot = parameters.GetProperty("screenshot").GetBoolean();
                return await windows.Observe(reference, screenshot, cancellation);
            case "validate_observation":
                ValidateFields(parameters, "window", "observation");
                return windows.ValidateObservation(RequiredString(parameters, "window", 64),
                    RequiredString(parameters, "observation", 64));
            case "activate":
                ValidateFields(parameters, "window");
                return await Task.Run(() => windows.Activate(RequiredString(parameters, "window", 64), cancellation), cancellation);
            case "input":
                ValidateFields(parameters, "window", "observation", "operation");
                return await Task.Run(() => windows.Input(RequiredString(parameters, "window", 64),
                    RequiredString(parameters, "observation", 64), parameters.GetProperty("operation"), cancellation), cancellation);
            default:
                throw new DesktopError("unsupported_action", "Action is not supported by this sidecar.");
        }
    }

    internal static void ValidateFields(JsonElement value, params string[] names)
    {
        if (value.ValueKind != JsonValueKind.Object)
            throw new DesktopError("invalid_request", "Expected an object.");
        var remaining = new HashSet<string>(names, StringComparer.Ordinal);
        foreach (JsonProperty property in value.EnumerateObject())
            if (!remaining.Remove(property.Name))
                throw new DesktopError("invalid_request", "Unknown or duplicate field.");
        if (remaining.Count != 0)
            throw new DesktopError("invalid_request", "Missing required field.");
    }

    internal static string RequiredString(JsonElement value, string name, int limit)
    {
        string? text = value.GetProperty(name).GetString();
        if (string.IsNullOrWhiteSpace(text) || text.Length > limit)
            throw new DesktopError("invalid_request", "String field is empty or too long.");
        return text;
    }

    private static JsonObject Failure(string? id, string code, string message) => new()
    {
        ["v"] = 1, ["id"] = id, ["ok"] = false,
        ["error"] = new JsonObject { ["code"] = code, ["message"] = message },
    };

    private static void Diagnose(string? id, string? action, string code) =>
        Console.Error.WriteLine(JsonSerializer.Serialize(new
        {
            @event = "desktop_failure", id, action, phase = DiagnosticPhase(action, code), code,
        }));

    /// <summary>将失败映射到启动、通信、观察、输入、覆盖层或清理阶段。</summary>
    private static string DiagnosticPhase(string? action, string code)
    {
        if (code is "overlay_unavailable" or "cursor_unavailable") return "overlay";
        if (code == "input_guard_unavailable") return "cleanup";
        if (action == "hello") return "startup";
        if (action is "list_windows" or "observe" or "validate_observation") return "observation";
        if (action is "activate" or "input") return "input";
        return "communication";
    }

    private static async Task WatchParent(int parentId, CancellationTokenSource lifetime)
    {
        try
        {
            using Process parent = Process.GetProcessById(parentId);
            await parent.WaitForExitAsync(lifetime.Token);
            lifetime.Cancel();
        }
        catch (ArgumentException) { lifetime.Cancel(); }
        catch (OperationCanceledException) { }
    }
}

internal sealed class DesktopError(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
