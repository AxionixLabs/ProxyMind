using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Threading;

namespace DesktopSidecar;

/// <summary>在专用消息线程跟踪窗口代次及变化序号，由原生桌面会话创建和释放。</summary>
internal sealed class WindowLifetimes : IDisposable
{
    private readonly Dictionary<nint, WindowStamp> tracked = new();
    private readonly WinEventCallback callback;
    private readonly Thread thread;
    private readonly Dispatcher dispatcher;
    private int disposed;

    /// <summary>先建立事件钩子再开放枚举，钩子失败时不返回缺少生命周期保护的会话。</summary>
    internal WindowLifetimes()
    {
        callback = OnWindowEvent;
        var ready = new TaskCompletionSource<Dispatcher>(TaskCreationOptions.RunContinuationsAsynchronously);
        thread = new Thread(() =>
        {
            Dispatcher loop = Dispatcher.CurrentDispatcher;
            nint hook = SetWinEventHook(0x8000, 0x8018, nint.Zero, callback, 0, 0, 0x0002);
            if (hook == nint.Zero)
            {
                ready.SetException(new Win32Exception(Marshal.GetLastWin32Error()));
                return;
            }
            ready.SetResult(loop);
            try { Dispatcher.Run(); }
            finally { UnhookWinEvent(hook); }
        }) { IsBackground = true, Name = "Desktop window lifecycle" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        dispatcher = ready.Task.GetAwaiter().GetResult();
    }

    /// <summary>为已枚举窗口取得代次；事件线程独占索引，避免与销毁通知并发写入。</summary>
    internal WindowStamp Track(nint handle) => dispatcher.Invoke(() =>
    {
        if (!tracked.TryGetValue(handle, out WindowStamp? stamp))
        {
            stamp = new WindowStamp(Guid.NewGuid().ToString("N"), 0);
            tracked.Add(handle, stamp);
        }
        return stamp;
    }, DispatcherPriority.Background);

    /// <summary>读取当前窗口代次；收到销毁事件后，同一个 HWND 不能恢复旧身份。</summary>
    internal WindowStamp? Read(nint handle) => dispatcher.Invoke(
        () => tracked.GetValueOrDefault(handle), DispatcherPriority.Background);

    /// <summary>只保留最新枚举范围，使事件索引随会话窗口上限收敛。</summary>
    internal void Retain(HashSet<nint> handles) => dispatcher.Invoke(() =>
    {
        foreach (nint handle in new List<nint>(tracked.Keys))
            if (!handles.Contains(handle)) tracked.Remove(handle);
    }, DispatcherPriority.Background);

    /// <summary>作废销毁或重新创建的窗口；其他窗口级事件推进观察版本。</summary>
    private void OnWindowEvent(nint hook, uint kind, nint handle, int objectId, int childId, uint eventThread, uint time)
    {
        if (objectId != 0 || childId != 0 || !tracked.TryGetValue(handle, out WindowStamp? stamp)) return;
        if (kind is 0x8000 or 0x8001) tracked.Remove(handle);
        else tracked[handle] = stamp with { Revision = stamp.Revision + 1 };
    }

    /// <summary>在安装钩子的线程结束消息循环并注销钩子，重复关闭不产生额外操作。</summary>
    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        dispatcher.BeginInvoke(new Action(dispatcher.InvokeShutdown), DispatcherPriority.Send);
        thread.Join();
        GC.KeepAlive(callback);
    }

    private delegate void WinEventCallback(nint hook, uint kind, nint handle, int objectId, int childId, uint eventThread, uint time);
    [DllImport("user32.dll", SetLastError = true)] private static extern nint SetWinEventHook(uint first, uint last, nint module,
        WinEventCallback callback, uint process, uint thread, uint flags);
    [DllImport("user32.dll")] private static extern bool UnhookWinEvent(nint hook);
}

/// <summary>保存一次窗口生命期及其事件修订，不暴露可变的事件线程状态。</summary>
internal sealed record WindowStamp(string Generation, long Revision);
