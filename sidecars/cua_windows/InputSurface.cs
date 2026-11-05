using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;

namespace DesktopSidecar;

/// <summary>在同一 STA 线程拥有操作覆盖层与输入干预钩子，回调只更新停止标志。</summary>
internal sealed class InputSurface : IDisposable
{
    private readonly Thread thread;
    private readonly Dispatcher dispatcher;
    private readonly HookCallback mouseCallback;
    private readonly HookCallback keyboardCallback;
    private GlowWindow? glow;
    private OperationPointerWindow? pointer;
    private nint target;
    private int active;
    private int stopped;
    private bool allowBackground;
    private string interruption = "External input";
    private NativeInput.Point lastMouse;
    private int cursorHideAdjustments;
    private uint attachedCursorThread;
    private nint previousCursor;

    internal InputSurface()
    {
        mouseCallback = MouseHook;
        keyboardCallback = KeyboardHook;
        var ready = new TaskCompletionSource<Dispatcher>(TaskCreationOptions.RunContinuationsAsynchronously);
        thread = new Thread(() =>
        {
            Dispatcher loop = Dispatcher.CurrentDispatcher;
            if (!GetCursorPos(out lastMouse))
            {
                ready.SetException(new DesktopError("input_monitor_unavailable", "Cannot initialize cursor monitoring."));
                return;
            }
            nint module = GetModuleHandle(null);
            nint mouse = SetWindowsHookEx(14, mouseCallback, module, 0);
            nint keyboard = SetWindowsHookEx(13, keyboardCallback, module, 0);
            if (mouse == nint.Zero || keyboard == nint.Zero)
            {
                if (mouse != nint.Zero) UnhookWindowsHookEx(mouse);
                if (keyboard != nint.Zero) UnhookWindowsHookEx(keyboard);
                ready.SetException(new DesktopError("input_monitor_unavailable", "Cannot monitor input interruption."));
                return;
            }
            ready.SetResult(loop);
            try { Dispatcher.Run(); }
            finally { UnhookWindowsHookEx(mouse); UnhookWindowsHookEx(keyboard); }
        }) { IsBackground = true, Name = "Desktop input visuals" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        dispatcher = ready.Task.GetAwaiter().GetResult();
    }

    internal void Begin(nint window, NativeWindows.WindowBounds bounds, bool permitBackground = false)
    {
        if (Volatile.Read(ref stopped) != 0)
            throw new DesktopError("input_stopped", interruption + " stopped this session; explicit reset is required to resume.");
        target = window;
        allowBackground = permitBackground;
        Volatile.Write(ref active, 1);
        dispatcher.Invoke(() =>
        {
            glow = new GlowWindow(new GlowPreview.ScreenBounds
                { Left = bounds.X, Top = bounds.Y, Right = bounds.X + bounds.Width, Bottom = bounds.Y + bounds.Height })
                { ExcludeFromCapture = true };
            pointer = new OperationPointerWindow { ExcludeFromCapture = true };
            glow.Show();
            pointer.Show();
            glow.Opacity = 0.72;
            pointer.Opacity = 1;
        });
    }

    internal void Check()
    {
        if (Volatile.Read(ref stopped) != 0)
            throw new DesktopError("user_interrupted", interruption + " interrupted this operation.");
        if (!allowBackground && NativeInput.Foreground != target)
            throw new DesktopError("focus_changed", "Target lost foreground focus; input stopped.");
    }

    internal void Move(int x, int y) => dispatcher.Invoke(() => pointer?.MoveTo(x, y));
    internal void Pulse() => dispatcher.Invoke(() => pointer?.Pulse());

    internal void End()
    {
        Volatile.Write(ref active, 0);
        dispatcher.Invoke(() =>
        {
            pointer?.Close();
            glow?.Close();
            pointer = null;
            glow = null;
        });
    }

    /// <summary>在窗口状态修订后同步柔光边界，再由观察校验拒绝继续输入。</summary>
    internal void UpdateBounds(NativeWindows.WindowBounds bounds)
    {
        dispatcher.Invoke(() => glow?.MoveToBounds(new GlowPreview.ScreenBounds
        {
            Left = bounds.X, Top = bounds.Y, Right = bounds.X + bounds.Width, Bottom = bounds.Y + bounds.Height,
        }));
    }

    /// <summary>在目标窗口的输入线程隐藏系统光标，并记录需要恢复的显示计数。</summary>
    internal void HideNativeCursor(nint window)
    {
        if (cursorHideAdjustments != 0) return;
        uint currentThread = GetCurrentThreadId();
        uint targetThread = GetWindowThreadProcessId(window, out _);
        if (targetThread == 0)
            throw new DesktopError("cursor_unavailable", "Cannot identify the target input thread.");
        if (targetThread != currentThread)
        {
            // ShowCursor 的可见计数按输入线程维护；加入目标队列后才会影响目标窗口的原生指针。
            if (!AttachThreadInput(currentThread, targetThread, true))
                throw new DesktopError("cursor_unavailable", "Cannot join the target input thread.");
            attachedCursorThread = targetThread;
        }
        try
        {
            int count = ShowCursor(false);
            cursorHideAdjustments = 1;
            while (count >= 0 && cursorHideAdjustments < 64)
            {
                count = ShowCursor(false);
                cursorHideAdjustments++;
            }
            if (count >= 0)
                throw new DesktopError("cursor_unavailable", "Cannot hide the native cursor for the input operation.");
            previousCursor = SetCursor(nint.Zero);
        }
        catch
        {
            RestoreNativeCursor();
            throw;
        }
    }

    /// <summary>仅撤销本次隐藏产生的显示计数，保留用户或其他线程原有状态。</summary>
    internal void RestoreNativeCursor()
    {
        for (int index = 0; index < cursorHideAdjustments; index++) ShowCursor(true);
        cursorHideAdjustments = 0;
        if (previousCursor != nint.Zero)
        {
            _ = SetCursor(previousCursor);
            previousCursor = nint.Zero;
        }
        if (attachedCursorThread != 0)
        {
            _ = AttachThreadInput(GetCurrentThreadId(), attachedCursorThread, false);
            attachedCursorThread = 0;
        }
    }

    private nint MouseHook(int code, nint message, nint data)
    {
        if (code >= 0)
        {
            MouseEvent value = Marshal.PtrToStructure<MouseEvent>(data);
            bool moved = value.Point.X != lastMouse.X || value.Point.Y != lastMouse.Y;
            lastMouse = value.Point;
            // 覆盖层出现可产生原地 WM_MOUSEMOVE；它没有新的输入位移，不能视为用户接管。
            if (Volatile.Read(ref active) != 0 && value.Extra != NativeInput.InjectionTag && (message != 0x0200 || moved))
            {
                interruption = "Physical or external mouse input";
                Interlocked.Exchange(ref stopped, 1);
            }
        }
        return CallNextHookEx(nint.Zero, code, message, data);
    }

    private nint KeyboardHook(int code, nint message, nint data)
    {
        if (code >= 0 && Volatile.Read(ref active) != 0 && Marshal.PtrToStructure<KeyEvent>(data).Extra != NativeInput.InjectionTag)
            Interlocked.Exchange(ref stopped, 1);
        return CallNextHookEx(nint.Zero, code, message, data);
    }

    public void Dispose()
    {
        End();
        dispatcher.BeginInvoke(new Action(dispatcher.InvokeShutdown), DispatcherPriority.Send);
        thread.Join();
        GC.KeepAlive(mouseCallback);
        GC.KeepAlive(keyboardCallback);
    }

    [StructLayout(LayoutKind.Sequential)] private struct MouseEvent
    { internal NativeInput.Point Point; internal uint Data, Flags, Time; internal nuint Extra; }
    [StructLayout(LayoutKind.Sequential)] private struct KeyEvent
    { internal uint Key, Scan, Flags, Time; internal nuint Extra; }
    private delegate nint HookCallback(int code, nint message, nint data);
    [DllImport("user32.dll", SetLastError = true)] private static extern nint SetWindowsHookEx(int kind, HookCallback callback, nint module, uint thread);
    [DllImport("user32.dll")] private static extern bool UnhookWindowsHookEx(nint hook);
    [DllImport("user32.dll")] private static extern nint CallNextHookEx(nint hook, int code, nint message, nint data);
    [DllImport("user32.dll")] private static extern int ShowCursor(bool show);
    [DllImport("user32.dll")] private static extern nint SetCursor(nint cursor);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint window, out uint process);
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint current, uint target, bool attach);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] private static extern nint GetModuleHandle(string? module);
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out NativeInput.Point point);
}

/// <summary>按实际系统指针坐标定位的小窗口，复用预览的尺寸与绘制，不播放预设轨迹。</summary>
internal sealed class OperationPointerWindow : PreviewOverlayWindow
{
    private readonly AgentPointer pointer = new();

    internal OperationPointerWindow() : base("Desktop input pointer")
    {
        Width = AgentPointer.Extent;
        Height = AgentPointer.Extent;
        Content = pointer;
    }

    protected override void OnSourceInitialized(EventArgs args)
    {
        base.OnSourceInitialized(args);
        if (GetCursorPos(out NativeInput.Point position)) MoveTo(position.X, position.Y);
    }

    internal void MoveTo(int x, int y)
    {
        DpiScale dpi = VisualTreeHelper.GetDpi(this);
        MovePhysical((int)Math.Round(x - AgentPointer.Hotspot * dpi.DpiScaleX),
            (int)Math.Round(y - AgentPointer.Hotspot * dpi.DpiScaleY));
    }

    internal void Pulse() => pointer.Pulse();
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out NativeInput.Point point);
}
