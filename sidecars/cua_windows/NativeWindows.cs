using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Automation;

namespace DesktopSidecar;

internal sealed class NativeWindows : IDisposable
{
    private const int WindowLimit = 128;
    private static readonly TimeSpan ObservationLifetime = TimeSpan.FromMinutes(2);
    private readonly Dictionary<string, WindowIdentity> windows = new(StringComparer.Ordinal);
    private readonly Dictionary<string, CachedObservation> observations = new(StringComparer.Ordinal);
    private readonly WindowLifetimes lifetimes = new();
    private readonly WindowCapture capture = new();
    private NativeInput? input;

    internal static void EnablePhysicalCoordinates() => SetProcessDpiAwarenessContext(new nint(-4));

    internal JsonObject List()
    {
        var result = new JsonArray();
        var live = new Dictionary<string, WindowIdentity>(StringComparer.Ordinal);
        var references = new Dictionary<WindowIdentity, string>();
        foreach (var existing in windows) references.Add(existing.Value, existing.Key);
        var handles = new List<nint>();
        bool limited = false;
        EnumWindows((handle, parameter) =>
        {
            if (!IsWindowVisible(handle)) return true;
            if (handles.Count >= 512) { limited = true; return false; }
            handles.Add(handle);
            return true;
        }, nint.Zero);
        foreach (nint handle in handles)
        {
            var title = new StringBuilder(1024);
            GetWindowText(handle, title, title.Capacity);
            if (title.Length == 0) continue;
            uint threadId = GetWindowThreadProcessId(handle, out uint pid);
            if (pid == 0 || pid == Environment.ProcessId) continue;
            _ = DwmGetWindowAttribute(handle, 14, out int cloaked, sizeof(int));
            if (cloaked != 0) continue;
            if (result.Count >= WindowLimit) { limited = true; break; }
            WindowBounds bounds;
            try { bounds = Bounds(handle); }
            catch (DesktopError error) when (error.Code == "window_unavailable") { continue; }
            uint dpi = GetDpiForWindow(handle);
            if (dpi == 0) continue;
            WindowStamp stamp = lifetimes.Track(handle);
            if (GetWindowThreadProcessId(handle, out uint currentPid) != threadId || currentPid != pid) continue;
            var identity = new WindowIdentity(handle, pid, threadId, stamp.Generation);
            if (!references.TryGetValue(identity, out string? reference))
                reference = Guid.NewGuid().ToString("N");
            live[reference] = identity;
            result.Add(new JsonObject
            {
                ["window"] = reference, ["title"] = title.ToString(), ["pid"] = pid,
                ["bounds"] = bounds.ToJson(), ["displayScale"] = dpi / 96.0,
                ["minimized"] = IsIconic(handle),
            });
        }
        windows.Clear();
        foreach (var entry in live) windows.Add(entry.Key, entry.Value);
        var retained = new HashSet<nint>();
        foreach (WindowIdentity identity in live.Values) retained.Add(identity.Handle);
        lifetimes.Retain(retained);
        PruneObservations();
        return new JsonObject { ["windows"] = result, ["truncated"] = limited };
    }

    internal async Task<JsonObject> Observe(string reference, bool screenshot, CancellationToken cancellation)
    {
        PruneObservations();
        observations.Remove(reference);
        if (!windows.TryGetValue(reference, out WindowIdentity? identity))
            throw new DesktopError("stale_window", "List windows again; reference is no longer valid.");
        WindowSnapshot snapshot = Snapshot(identity);
        ControlObservation controls = await Task.Run(() => ReadControls(identity.Handle, snapshot.Bounds, cancellation), cancellation);
        JsonObject? image = screenshot ? await capture.Capture(identity.Handle, cancellation) : null;
        if (Snapshot(identity) != snapshot)
            throw new DesktopError("window_changed", "Window changed during observation; observe again.");
        string token = Guid.NewGuid().ToString("N");
        DateTimeOffset captured = DateTimeOffset.UtcNow;
        observations[reference] = new CachedObservation(token, snapshot, Stopwatch.GetTimestamp(), controls.Controls,
            image?["width"]?.GetValue<int>() ?? 0, image?["height"]?.GetValue<int>() ?? 0);
        return new JsonObject
        {
            ["window"] = reference, ["observation"] = token,
            ["capturedAt"] = captured.ToString("O"), ["bounds"] = snapshot.Bounds.ToJson(),
            ["displayScale"] = snapshot.Dpi / 96.0, ["coordinateSpace"] = "physical_screen_pixels",
            ["tree"] = controls.Tree, ["image"] = image,
        };
    }

    /// <summary>只验证最新观察的时间、身份和窗口状态，不授予或执行输入。</summary>
    internal JsonObject ValidateObservation(string reference, string token)
    {
        PruneObservations();
        if (!observations.TryGetValue(reference, out CachedObservation? observation) || observation.Token != token)
            throw new DesktopError("stale_observation", "Observation expired or was replaced; observe again.");
        try
        {
            if (Snapshot(observation.Snapshot.Identity) != observation.Snapshot)
                throw new DesktopError("window_changed", "Window changed after observation; observe again.");
            return new JsonObject();
        }
        catch (DesktopError)
        {
            observations.Remove(reference);
            throw;
        }
    }

    /// <summary>显式激活已枚举窗口；成功仍要求重新观察，不复用激活前的缓存。</summary>
    internal JsonObject Activate(string reference, CancellationToken cancellation)
    {
        if (!windows.TryGetValue(reference, out WindowIdentity? identity))
            throw new DesktopError("stale_window", "List windows again before activating.");
        WindowSnapshot snapshot = Snapshot(identity);
        bool desktopTarget = NativeInput.IsDesktopWindow(identity.Handle);
        input ??= new NativeInput();
        try
        {
            int submitted = input.Run(identity.Handle, snapshot.Bounds, () => CheckInputSnapshot(snapshot), () => { },
                cancellation, activate: true, allowBackground: desktopTarget);
            return InputResult(submitted);
        }
        finally { observations.Remove(reference); }
    }

    /// <summary>只执行绑定当前观察的输入；窗口、控件和命中检查留在持有原生引用的一侧。</summary>
    internal JsonObject Input(string reference, string token, JsonElement operation, CancellationToken cancellation)
    {
        ValidateObservation(reference, token);
        CachedObservation observation = observations[reference];
        WindowSnapshot snapshot = observation.Snapshot;
        bool desktopTarget = NativeInput.IsDesktopWindow(snapshot.Identity.Handle);
        input ??= new NativeInput();
        var controlsToCheck = new List<ObservedControl>();

        NativeInput.Point Resolve(JsonElement target)
        {
            if (target.TryGetProperty("element", out JsonElement element))
            {
                Program.ValidateFields(target, "element");
                int index = element.GetInt32();
                if (index < 0 || index >= observation.Controls.Count)
                    throw new DesktopError("invalid_target", "Control index is outside this observation.");
                ObservedControl control = observation.Controls[index];
                ValidateControl(control);
                controlsToCheck.Add(control);
                return new NativeInput.Point { X = (int)Math.Round(control.Bounds.X + control.Bounds.Width / 2),
                    Y = (int)Math.Round(control.Bounds.Y + control.Bounds.Height / 2) };
            }
            Program.ValidateFields(target, "x", "y");
            int x = target.GetProperty("x").GetInt32(), y = target.GetProperty("y").GetInt32();
            if (observation.ImageWidth == 0 || observation.ImageHeight == 0 ||
                x < 0 || y < 0 || x >= observation.ImageWidth || y >= observation.ImageHeight)
                throw new DesktopError("invalid_target", "Coordinates require this observation's screenshot and must be inside it.");
            return new NativeInput.Point
            {
                X = snapshot.Bounds.X + (int)Math.Floor((x + 0.5) * snapshot.Bounds.Width / observation.ImageWidth),
                Y = snapshot.Bounds.Y + (int)Math.Floor((y + 0.5) * snapshot.Bounds.Height / observation.ImageHeight),
            };
        }

        void Hit(int x, int y)
        {
            WindowBounds bounds = snapshot.Bounds;
            if (x < bounds.X || y < bounds.Y || x >= bounds.X + bounds.Width || y >= bounds.Y + bounds.Height ||
                (!desktopTarget && !NativeInput.Hits(snapshot.Identity.Handle, x, y)))
                throw new DesktopError("target_obscured", "Input point does not hit the observed target window.");
        }

        Action execute = InputOperations.Prepare(operation, input, Resolve, Hit);
        try
        {
            int submitted = input.Run(snapshot.Identity.Handle, snapshot.Bounds, () =>
            {
                CheckInputSnapshot(snapshot);
                foreach (ObservedControl control in controlsToCheck) ValidateControl(control);
            }, execute, cancellation, allowBackground: desktopTarget);
            return InputResult(submitted);
        }
        finally { observations.Remove(reference); }
    }

    private void CheckInputSnapshot(WindowSnapshot snapshot)
    {
        WindowSnapshot current = Snapshot(snapshot.Identity);
        if (current.Bounds != snapshot.Bounds || current.Dpi != snapshot.Dpi)
        {
            input?.UpdateBounds(current.Bounds);
            throw new DesktopError("window_changed", "Window geometry changed during input.");
        }
    }

    private static void ValidateControl(ObservedControl control)
    {
        try
        {
            var current = control.Element.Current;
            if (!current.IsEnabled)
                throw new DesktopError("control_disabled", "The observed control is disabled. Choose an enabled visible control from a fresh observation.");
            if (current.IsOffscreen || current.IsPassword || current.BoundingRectangle.IsEmpty)
                throw new DesktopError("control_unavailable", "The observed control is hidden, protected or has no clickable bounds.");
            if (current.BoundingRectangle != control.Bounds || current.Name != control.Name ||
                current.AutomationId != control.AutomationId || !control.Element.GetRuntimeId().SequenceEqual(control.RuntimeId))
                throw new DesktopError("control_changed", "Observed control identity, name or bounds changed; observe again.");
        }
        catch (Exception error) when (error is ElementNotAvailableException or COMException or UnauthorizedAccessException)
        {
            throw new DesktopError("control_unavailable", "Observed control is no longer accessible.");
        }
    }

    private static JsonObject InputResult(int submitted) => new()
        { ["eventsSubmitted"] = submitted, ["requiresObservation"] = true };

    /// <summary>在事件版本前后检查原生身份、可见性、位置和 DPI，拒绝混合状态。</summary>
    private WindowSnapshot Snapshot(WindowIdentity identity)
    {
        WindowStamp? before = lifetimes.Read(identity.Handle);
        if (before is null || before.Generation != identity.Generation || !IsWindow(identity.Handle) ||
            GetWindowThreadProcessId(identity.Handle, out uint pid) != identity.ThreadId || pid != identity.Pid)
            throw new DesktopError("stale_window", "Window lifetime changed; list windows again.");
        _ = DwmGetWindowAttribute(identity.Handle, 14, out int cloaked, sizeof(int));
        if (IsIconic(identity.Handle) || !IsWindowVisible(identity.Handle) || cloaked != 0)
            throw new DesktopError("window_unavailable", "Window is minimized, hidden or cloaked.");
        WindowBounds bounds = Bounds(identity.Handle);
        uint dpi = GetDpiForWindow(identity.Handle);
        if (dpi == 0) throw new DesktopError("window_unavailable", "Window DPI is unavailable.");
        WindowStamp? after = lifetimes.Read(identity.Handle);
        if (after != before) throw new DesktopError("window_changed", "Window changed during validation.");
        return new WindowSnapshot(identity, bounds, dpi, before.Revision);
    }

    /// <summary>按单调时钟淘汰过期观察，每个仍在枚举范围的窗口至多保留一条元数据。</summary>
    private void PruneObservations()
    {
        foreach (var entry in new List<KeyValuePair<string, CachedObservation>>(observations))
            if (!windows.ContainsKey(entry.Key) || Stopwatch.GetElapsedTime(entry.Value.Completed) >= ObservationLifetime)
                observations.Remove(entry.Key);
    }

    /// <summary>释放窗口事件线程与全部会话引用。</summary>
    public void Dispose()
    {
        try { input?.Dispose(); }
        finally
        {
            try { capture.Dispose(); }
            finally
            {
                lifetimes.Dispose();
                observations.Clear();
                windows.Clear();
            }
        }
    }

    private static ControlObservation ReadControls(nint handle, WindowBounds bounds, CancellationToken cancellation)
    {
        var nodes = new JsonArray();
        var controls = new List<ObservedControl>();
        bool truncated = false;
        try
        {
            AutomationElement root = AutomationElement.FromHandle(handle);
            var queue = new Queue<(AutomationElement Element, int Parent, int Depth)>();
            queue.Enqueue((root, -1, 0));
            var stopwatch = Stopwatch.StartNew();
            while (queue.Count > 0)
            {
                cancellation.ThrowIfCancellationRequested();
                if (nodes.Count >= 256 || stopwatch.Elapsed > TimeSpan.FromSeconds(3)) { truncated = true; break; }
                var (element, parent, depth) = queue.Dequeue();
                var info = element.Current;
                var rect = info.BoundingRectangle;
                int index = nodes.Count;
                controls.Add(new ObservedControl(element, element.GetRuntimeId(), rect, info.Name, info.AutomationId));
                nodes.Add(new JsonObject
                {
                    ["index"] = index, ["parent"] = parent,
                    ["name"] = Limit(info.Name), ["role"] = info.ControlType.ProgrammaticName,
                    ["automationId"] = Limit(info.AutomationId), ["enabled"] = info.IsEnabled,
                    ["offscreen"] = info.IsOffscreen, ["password"] = info.IsPassword,
                    ["bounds"] = rect.IsEmpty ? null : new JsonObject
                    {
                        ["x"] = rect.X - bounds.X, ["y"] = rect.Y - bounds.Y,
                        ["width"] = rect.Width, ["height"] = rect.Height,
                    },
                });
                if (depth >= 8) { truncated = true; continue; }
                AutomationElement? child = TreeWalker.ControlViewWalker.GetFirstChild(element);
                while (child is not null)
                {
                    if (queue.Count + nodes.Count >= 256) { truncated = true; break; }
                    queue.Enqueue((child, index, depth + 1));
                    child = TreeWalker.ControlViewWalker.GetNextSibling(child);
                }
            }
            return new ControlObservation(new JsonObject { ["nodes"] = nodes, ["truncated"] = truncated, ["error"] = null }, controls);
        }
        catch (Exception error) when (error is ElementNotAvailableException or UnauthorizedAccessException or COMException)
        {
            return new ControlObservation(new JsonObject { ["nodes"] = nodes, ["truncated"] = true, ["error"] = "accessibility_unavailable" }, controls);
        }
    }

    private static string Limit(string text) => text.Length <= 1024 ? text : text[..1024];

    private static WindowBounds Bounds(nint handle)
    {
        int status = DwmGetWindowAttribute(handle, 9, out Rect rect, Marshal.SizeOf<Rect>());
        if (status < 0 || rect.Right <= rect.Left || rect.Bottom <= rect.Top)
            throw new DesktopError("window_unavailable", "Window bounds are unavailable.");
        return new WindowBounds(rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top);
    }

    private sealed record WindowIdentity(nint Handle, uint Pid, uint ThreadId, string Generation);
    private sealed record WindowSnapshot(WindowIdentity Identity, WindowBounds Bounds, uint Dpi, long Revision);
    private sealed record ObservedControl(AutomationElement Element, int[] RuntimeId, System.Windows.Rect Bounds,
        string Name, string AutomationId);
    private sealed record ControlObservation(JsonObject Tree, List<ObservedControl> Controls);
    private sealed record CachedObservation(string Token, WindowSnapshot Snapshot, long Completed,
        List<ObservedControl> Controls, int ImageWidth, int ImageHeight);
    internal sealed record WindowBounds(int X, int Y, int Width, int Height)
    {
        internal JsonObject ToJson() => new() { ["x"] = X, ["y"] = Y, ["width"] = Width, ["height"] = Height };
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { internal int Left; internal int Top; internal int Right; internal int Bottom; }
    private delegate bool EnumWindowCallback(nint handle, nint parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowCallback callback, nint parameter);
    [DllImport("user32.dll")] private static extern bool IsWindow(nint handle);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(nint handle);
    [DllImport("user32.dll")] private static extern bool IsIconic(nint handle);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(nint handle, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint handle, out uint processId);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(nint handle);
    [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(nint context);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(nint handle, int attribute, out int value, int size);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(nint handle, int attribute, out Rect value, int size);
}
