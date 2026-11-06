using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace DesktopSidecar;

/// <summary>串行提交 Win32 输入；按下状态由独立守护进程兜底释放，原生会话拥有本实例。</summary>
internal sealed class NativeInput : IDisposable
{
    // Windows 输入路径可能截断 dwExtraInfo 高 32 位，标记必须可跨该边界完整保留。
    internal static readonly nuint InjectionTag = (nuint)RandomNumberGenerator.GetInt32(1, int.MaxValue);
    private InputSurface? surface;
    private Process? guardian;
    private Process? cursorManager;
    private readonly HashSet<string> held = new(StringComparer.Ordinal);
    private int submitted;
    private Action? validate;
    private CancellationToken cancellation;

    /// <summary>占有系统会话输入锁，操作结束前收束覆盖层及本实例按下状态。</summary>
    internal int Run(nint target, NativeWindows.WindowBounds bounds, Action check, Action operation,
        CancellationToken token, bool activate = false, bool allowBackground = false)
    {
        EnsureGuardian();
        GuardCommand("begin");
        submitted = 0;
        validate = check;
        cancellation = token;
        bool cursorSuppressed = false;
        try
        {
            EnsureInteractiveDesktop();
            WaitForReleasedInput();
            if (activate && Foreground != target && !allowBackground)
            {
                if (!Activate(target)) throw new DesktopError("activation_failed", "Windows did not grant foreground activation.");
                Thread.Sleep(50);
            }
            EnsureCursorManager();
            CursorManagerCommand("suppress");
            cursorSuppressed = true;
            surface ??= new InputSurface();
            surface.Begin(target, bounds, allowBackground);
            surface.HideNativeCursor(target);
            Check();
            operation();
            return submitted;
        }
        catch (DesktopError error) when (submitted > 0)
        {
            throw new DesktopError("input_interrupted", $"{error.Code}: {error.Message}; {submitted} input events were submitted. Observe before any new action; never replay.");
        }
        catch (Exception error) when (error is Win32Exception or IOException)
        {
            throw new DesktopError(submitted > 0 ? "input_interrupted" : "input_unavailable",
                $"Native input or overlay failed ({error.GetType().Name}); {submitted} events submitted. Never replay.");
        }
        finally
        {
            try { ReleaseHeld(); }
            finally
            {
                try { surface?.RestoreNativeCursor(); }
                finally
                {
                    try { surface?.End(); }
                    finally
                    {
                        try
                        {
                            if (cursorSuppressed) CursorManagerCommand("restore");
                        }
                        finally
                        {
                            try { StopCursorManager(); }
                            finally
                            {
                                validate = null;
                                if (guardian is not null)
                                {
                                    try { GuardCommand("end"); }
                                    catch (DesktopError) { StopGuardian(); throw; }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    /// <summary>为审批确认键的抬起留出有界时间；连续空闲后才开始，持续占用不注入任何事件。</summary>
    private void WaitForReleasedInput()
    {
        var elapsed = Stopwatch.StartNew();
        long quietSince = -1;
        int heldKey = 0;
        while (true)
        {
            cancellation.ThrowIfCancellationRequested();
            EnsureInteractiveDesktop();
            int currentKey = 0;
            for (int key = 1; key < 256; key++)
                if (key is not (3 or 7) && (GetAsyncKeyState(key) & 0x8000) != 0)
                { currentKey = key; break; }
            long now = elapsed.ElapsedMilliseconds;
            if (currentKey != 0) { heldKey = currentKey; quietSince = -1; }
            else if (quietSince < 0) quietSince = now;
            if (quietSince >= 0 && now - quietSince >= 100) return;
            if (now >= 1000)
                throw new DesktopError("input_busy", $"Windows reports virtual key 0x{heldKey:X2} held or recently released; no input was submitted. Release it, then observe again. Reset is not required.");
            Thread.Sleep(10);
        }
    }

    /// <summary>每个输入片段前检查停止、前台、桌面与窗口约束。</summary>
    internal void Check()
    {
        if (cancellation.IsCancellationRequested)
            throw new DesktopError("input_cancelled", "Desktop operation was cancelled.");
        surface?.Check();
        EnsureInteractiveDesktop();
        validate?.Invoke();
    }

    /// <summary>同步输入覆盖层的窗口边界，随后由快照校验停止过期操作。</summary>
    internal void UpdateBounds(NativeWindows.WindowBounds bounds) => surface?.UpdateBounds(bounds);

    /// <summary>按真实时间插值，停帧后跳到当前进度，避免补播积压鼠标位置。</summary>
    internal void Move(int x, int y, int duration, Action<int, int>? validatePoint = null)
    {
        if (!GetCursorPos(out Point origin)) throw new DesktopError("input_unavailable", "Cursor position is unavailable.");
        var elapsed = Stopwatch.StartNew();
        do
        {
            Check();
            double progress = duration == 0 ? 1 : Math.Min(1, elapsed.Elapsed.TotalMilliseconds / duration);
            double eased = progress * progress * (3 - 2 * progress);
            int currentX = (int)Math.Round(origin.X + (x - origin.X) * eased);
            int currentY = (int)Math.Round(origin.Y + (y - origin.Y) * eased);
            validatePoint?.Invoke(currentX, currentY);
            int left = GetSystemMetrics(76), top = GetSystemMetrics(77);
            int width = GetSystemMetrics(78), height = GetSystemMetrics(79);
            if (width < 2 || height < 2 || currentX < left || currentX >= left + width || currentY < top || currentY >= top + height)
                throw new DesktopError("coordinate_invalid", "Point is outside the current virtual desktop.");
            Send(Mouse(0x8000 | 0x4000 | 0x0001, 0,
                (int)Math.Round((currentX - left) * 65535.0 / (width - 1)),
                (int)Math.Round((currentY - top) * 65535.0 / (height - 1))));
            if (progress >= 1) break;
            Thread.Sleep(8);
        } while (true);
        surface?.SynchronizePointer();
        Check();
    }

    /// <summary>点击前再次验证命中窗口，成功提交后才播放点击反馈。</summary>
    internal void Click(string button, int count, Action hitTest)
    {
        for (int index = 0; index < count; index++)
        {
            Check();
            hitTest();
            Hold("mouse:" + button);
            try { Thread.Sleep(25); Check(); }
            finally { Release("mouse:" + button); }
            surface?.Pulse();
            if (index + 1 < count) Thread.Sleep(70);
        }
        Thread.Sleep(140);
    }

    /// <summary>滚轮按单格提交，每格之间检查停止条件。</summary>
    internal void Scroll(int horizontal, int vertical)
    {
        foreach (var axis in new[] { (horizontal, 0x1000u), (vertical, 0x0800u) })
            for (int index = 0; index < Math.Abs(axis.Item1); index++)
            {
                Check();
                Send(Mouse(axis.Item2, unchecked((uint)(Math.Sign(axis.Item1) * 120))));
                Thread.Sleep(8);
            }
    }

    /// <summary>使用 Unicode 包逐字符提交，不改写用户剪贴板。</summary>
    internal void Text(string text)
    {
        const int chunkSize = 64;
        for (int offset = 0; offset < text.Length; offset += chunkSize)
        {
            Check();
            int count = Math.Min(chunkSize, text.Length - offset);
            var inputs = new Input[count * 2];
            for (int index = 0; index < count; index++)
            {
                char value = text[offset + index];
                inputs[index * 2] = Key(0, value, 4);
                inputs[index * 2 + 1] = Key(0, value, 4 | 2);
            }
            Send(inputs);
            Thread.Sleep(count);
        }
        Check();
    }

    /// <summary>组合键只在本次操作持有，所有退出路径释放已按下键。</summary>
    internal void Keys(ushort[] keys)
    {
        foreach (ushort key in keys) { Check(); Hold("key:" + key); }
        Thread.Sleep(30);
        Check();
        for (int index = keys.Length - 1; index >= 0; index--) Release("key:" + keys[index]);
    }

    /// <summary>先确认守护进程记录按下意图，再提交系统事件，覆盖父进程异常退出窗口。</summary>
    internal void Hold(string name)
    {
        GuardCommand("hold " + name);
        held.Add(name);
        Send(StateInput(name, false));
    }

    /// <summary>先释放系统状态，随后移除守护进程记录；重复释放不生成新动作。</summary>
    internal void Release(string name)
    {
        if (!held.Contains(name)) return;
        Send(StateInput(name, true));
        held.Remove(name);
        GuardCommand("release " + name);
    }

    private void GuardCommand(string command)
    {
        try
        {
            if (guardian is null) throw new DesktopError("input_guard_unavailable", "Input release guard is not running.");
            guardian.StandardInput.WriteLine(command);
            guardian.StandardInput.Flush();
            string? reply = guardian.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(2)).GetAwaiter().GetResult();
            if (reply == "busy") throw new DesktopError("desktop_busy", "Another desktop operation is active; no input submitted.");
            if (reply != "ok")
                throw new DesktopError("input_guard_unavailable", "Input release guard disconnected.");
        }
        catch (Exception error) when (error is IOException or TimeoutException)
        {
            throw new DesktopError("input_guard_unavailable", "Input release guard failed to acknowledge within its deadline.");
        }
    }

    private void ReleaseHeld()
    {
        try
        {
            foreach (string name in new List<string>(held)) Release(name);
        }
        finally
        {
            // 释放通道失败时关闭私有管道，由独立守护进程释放剩余状态。
            if (held.Count != 0) StopGuardian();
        }
    }

    private void EnsureGuardian()
    {
        if (guardian is not null) return;
        string executable = Environment.ProcessPath ?? throw new DesktopError("input_guard_unavailable", "Executable location is unavailable.");
        var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        if (Path.GetFileNameWithoutExtension(executable).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            start.ArgumentList.Add(typeof(Program).Assembly.Location);
        start.ArgumentList.Add("--release-input");
        guardian = Process.Start(start) ?? throw new DesktopError("input_guard_unavailable", "Cannot start input release guard.");
    }

    private void EnsureCursorManager()
    {
        if (cursorManager is not null) return;
        cursorManager = SystemCursorManager.Start(Process.GetCurrentProcess().Id);
    }

    private void CursorManagerCommand(string command)
    {
        try
        {
            if (cursorManager is null)
                throw new DesktopError("cursor_unavailable", "System cursor manager is not running.");
            cursorManager.StandardInput.WriteLine(command);
            cursorManager.StandardInput.Flush();
            string? reply = cursorManager.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(2))
                .GetAwaiter().GetResult();
            if (reply != "ok")
                throw new DesktopError("cursor_unavailable", "System cursor manager did not acknowledge the operation.");
        }
        catch (Exception error) when (error is IOException or TimeoutException)
        {
            throw new DesktopError("cursor_unavailable", "System cursor manager failed to acknowledge within its deadline.");
        }
    }

    private void Send(params Input[] inputs)
    {
        uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<Input>());
        submitted += (int)sent;
        if (sent != inputs.Length)
            throw new DesktopError("input_rejected", $"Windows accepted {sent}/{inputs.Length} events (error {Marshal.GetLastWin32Error()}); UIPI may restrict input.");
    }

    /// <summary>守护模式仅维护有限按下状态，私有管道 EOF 后释放；不读取桌面内容。</summary>
    internal static int RunReleaseGuard()
    {
        var pending = new HashSet<string>(StringComparer.Ordinal);
        using var lease = new Mutex(false, $"Local\\ProxyMind.DesktopInput.{Process.GetCurrentProcess().SessionId}");
        bool acquired = false;
        try
        {
            while (Console.ReadLine() is string line)
            {
                if (line.Length > 64 || pending.Count > 16) return 2;
                if (line == "begin" && !acquired)
                {
                    try { acquired = lease.WaitOne(0); }
                    catch (AbandonedMutexException) { acquired = true; }
                    Console.WriteLine(acquired ? "ok" : "busy");
                    continue;
                }
                if (line == "end" && acquired && pending.Count == 0)
                {
                    lease.ReleaseMutex();
                    acquired = false;
                    Console.WriteLine("ok");
                    continue;
                }
                if (!acquired) return 2;
                string[] parts = line.Split(' ');
                if (parts.Length != 2 || parts[0] is not ("hold" or "release")) return 2;
                _ = StateInput(parts[1], true);
                if (parts[0] == "hold") pending.Add(parts[1]);
                else pending.Remove(parts[1]);
                Console.WriteLine("ok");
            }
            return 0;
        }
        finally
        {
            try
            {
                foreach (string name in pending)
                    _ = SendInput(1, [StateInput(name, true)], Marshal.SizeOf<Input>());
            }
            finally { if (acquired) lease.ReleaseMutex(); }
        }
    }

    internal static ushort KeyCode(string key)
    {
        if (key.Length == 1 && (key[0] is >= 'A' and <= 'Z' or >= '0' and <= '9')) return key[0];
        if (key.StartsWith('F') && int.TryParse(key.AsSpan(1), out int function) && function is >= 1 and <= 24 && key == $"F{function}")
            return (ushort)(0x70 + function - 1);
        return key switch
        {
            "CTRL" => 0x11, "ALT" => 0x12, "SHIFT" => 0x10, "WIN" => 0x5B,
            "ENTER" => 0x0D, "TAB" => 0x09, "ESC" => 0x1B, "SPACE" => 0x20,
            "BACKSPACE" => 0x08, "DELETE" => 0x2E, "HOME" => 0x24, "END" => 0x23,
            "LEFT" => 0x25, "UP" => 0x26, "RIGHT" => 0x27, "DOWN" => 0x28,
            "PAGEUP" => 0x21, "PAGEDOWN" => 0x22,
            _ => throw new DesktopError("invalid_request", "Unsupported named key."),
        };
    }

    private static Input StateInput(string name, bool up)
    {
        if (name.StartsWith("key:", StringComparison.Ordinal) && ushort.TryParse(name.AsSpan(4), out ushort key) && key is > 0 and < 256)
            return Key(key, '\0', (up ? 2u : 0u) | (key is >= 0x21 and <= 0x2E or 0x5B ? 1u : 0u));
        uint down = name switch { "mouse:left" => 2, "mouse:right" => 8, "mouse:middle" => 32,
            _ => throw new DesktopError("invalid_request", "Unsupported held input."), };
        return Mouse(up ? down * 2 : down);
    }

    internal static void EnsureInteractiveDesktop()
    {
        nint desktop = OpenInputDesktop(0, false, 0x0001);
        if (desktop == nint.Zero) throw new DesktopError("desktop_unavailable", "Input desktop is unavailable or restricted.");
        try
        {
            var name = new StringBuilder(256);
            var current = new StringBuilder(256);
            if (!GetUserObjectInformation(desktop, 2, name, name.Capacity * 2, out _) ||
                !GetUserObjectInformation(GetThreadDesktop(GetCurrentThreadId()), 2, current, current.Capacity * 2, out _) ||
                name.ToString() != current.ToString())
                throw new DesktopError("desktop_unavailable", "The active desktop differs from this input session.");
        }
        finally { CloseDesktop(desktop); }
    }

    public void Dispose()
    {
        try { ReleaseHeld(); }
        finally
        {
            try { surface?.Dispose(); }
            finally
            {
                try { StopCursorManager(); }
                finally { StopGuardian(); }
            }
        }
    }

    private void StopCursorManager()
    {
        if (cursorManager is null) return;
        Process current = cursorManager;
        cursorManager = null;
        try
        {
            current.StandardInput.Close();
            if (!current.WaitForExit(2000))
            {
                current.Kill(entireProcessTree: true);
                current.WaitForExit(2000);
            }
        }
        finally
        {
            current.Dispose();
            try { SystemCursorManager.RestoreSystemCursors(); }
            catch (Win32Exception error)
            {
                throw new DesktopError("cursor_unavailable", $"Cannot restore the Windows cursor scheme ({error.NativeErrorCode}).");
            }
        }
    }

    private void StopGuardian()
    {
        if (guardian is null) return;
        guardian.StandardInput.Close();
        if (!guardian.WaitForExit(2000)) throw new DesktopError("input_guard_unavailable", "Input release guard did not stop.");
        guardian.Dispose();
        guardian = null;
        held.Clear();
    }

    internal static nint Foreground => GetForegroundWindow();
    /// <summary>显式请求激活；跨输入队列时临时连接前台线程，始终解除连接并核对实际前台。</summary>
    internal static bool Activate(nint target)
    {
        if (Foreground == target) return true;
        if (SetForegroundWindow(target) && WaitForForeground(target)) return true;
        uint currentThread = GetCurrentThreadId();
        uint foregroundThread = GetWindowThreadProcessId(Foreground, out _);
        if (foregroundThread == 0 || foregroundThread == currentThread) return false;
        // 此时按键已全部释放；连接会重置线程键盘状态，不能在持键期间使用。
        _ = PeekMessage(out _, nint.Zero, 0, 0, 0);
        if (!AttachThreadInput(currentThread, foregroundThread, true)) return false;
        try
        {
            _ = SetForegroundWindow(target);
            return WaitForForeground(target);
        }
        finally { _ = AttachThreadInput(currentThread, foregroundThread, false); }
    }

    private static bool WaitForForeground(nint target)
    {
        for (int attempt = 0; attempt < 20; attempt++)
        {
            if (Foreground == target) return true;
            Thread.Sleep(10);
        }
        return false;
    }
    internal static bool Hits(nint target, int x, int y) => GetAncestor(WindowFromPoint(new Point { X = x, Y = y }), 2) == target;

    /// <summary>识别 Explorer 桌面宿主；桌面不是可被 SetForegroundWindow 激活的普通应用。</summary>
    internal static bool IsDesktopWindow(nint target)
    {
        var name = new StringBuilder(64);
        int length = GetClassName(target, name, name.Capacity);
        string className = length > 0 ? name.ToString() : "";
        return className is "Progman" or "WorkerW";
    }
    private static Input Mouse(uint flags, uint data = 0, int x = 0, int y = 0) => new()
        { Type = 0, Data = new InputUnion { Mouse = new MouseInput { X = x, Y = y, Flags = flags, MouseData = data, Extra = InjectionTag } } };
    private static Input Key(ushort key, char scan, uint flags) => new()
        { Type = 1, Data = new InputUnion { Key = new KeyInput { VirtualKey = key, Scan = scan, Flags = flags, Extra = InjectionTag } } };
    [StructLayout(LayoutKind.Sequential)] internal struct Point { internal int X, Y; }
    [StructLayout(LayoutKind.Sequential)] private struct Message
    { internal nint Window; internal uint Kind; internal nuint WParam; internal nint LParam; internal uint Time; internal Point Position; internal uint Private; }
    [StructLayout(LayoutKind.Sequential)] private struct Input { internal uint Type; internal InputUnion Data; }
    [StructLayout(LayoutKind.Explicit)] private struct InputUnion
    { [FieldOffset(0)] internal MouseInput Mouse; [FieldOffset(0)] internal KeyInput Key; }
    [StructLayout(LayoutKind.Sequential)] private struct MouseInput { internal int X, Y; internal uint MouseData, Flags, Time; internal nuint Extra; }
    [StructLayout(LayoutKind.Sequential)] private struct KeyInput { internal ushort VirtualKey, Scan; internal uint Flags, Time; internal nuint Extra; }
    [DllImport("user32.dll", SetLastError = true)] private static extern uint SendInput(uint count, Input[] inputs, int size);
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] private static extern nint GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(nint window);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint window, out uint process);
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint current, uint target, bool attach);
    [DllImport("user32.dll")] private static extern bool PeekMessage(out Message message, nint window, uint first, uint last, uint remove);
    [DllImport("user32.dll")] private static extern nint WindowFromPoint(Point point);
    [DllImport("user32.dll")] private static extern nint GetAncestor(nint window, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(nint window, StringBuilder name, int capacity);
    [DllImport("user32.dll", SetLastError = true)] private static extern nint OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool GetUserObjectInformation(nint handle, int index, StringBuilder value, int length, out int needed);
    [DllImport("user32.dll")] private static extern nint GetThreadDesktop(uint thread);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern bool CloseDesktop(nint desktop);
}
