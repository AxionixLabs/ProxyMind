using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Input;
using System.Windows.Media;

namespace DesktopSidecar;

/// <summary>统一预览窗口的原生穿透与定位，窗口由预览消息循环创建并关闭。</summary>
internal abstract class PreviewOverlayWindow : Window
{
    private const int ExtendedStyle = -20;
    private const long PassThroughStyles = 0x00000020L | 0x00000080L | 0x08000000L;
    private nint handle;
    private (int X, int Y)? previousPosition;
    internal bool ExcludeFromCapture { get; init; }

    /// <summary>建立不激活、不命中、不进入任务栏的透明窗口。</summary>
    protected PreviewOverlayWindow(string title)
    {
        Title = title;
        WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        ShowInTaskbar = false;
        ShowActivated = false;
        Focusable = false;
        IsHitTestVisible = false;
        Cursor = Cursors.None;
        Topmost = true;
        Opacity = 0;
    }

    internal bool HasPassThroughStyles =>
        (GetWindowLongPtr(handle, ExtendedStyle).ToInt64() & PassThroughStyles) == PassThroughStyles;

    /// <summary>在原生句柄创建后设置穿透样式并取消系统圆角。</summary>
    protected override void OnSourceInitialized(EventArgs args)
    {
        base.OnSourceInitialized(args);
        handle = new WindowInteropHelper(this).Handle;
        long styles = GetWindowLongPtr(handle, ExtendedStyle).ToInt64();
        SetWindowLongPtr(handle, ExtendedStyle, new nint(styles | PassThroughStyles));
        if (!HasPassThroughStyles)
            throw new InvalidOperationException("Overlay pass-through styles could not be applied.");
        int cornerPreference = 1;
        _ = DwmSetWindowAttribute(handle, 33, ref cornerPreference, sizeof(int));
        HwndSource.FromHwnd(handle)?.AddHook(WindowMessage);
        if (ExcludeFromCapture && !SetWindowDisplayAffinity(handle, 0x11))
            throw new DesktopError("overlay_unavailable", "Cannot exclude operation overlay from capture.");
    }

    /// <summary>用物理像素设置显示器覆盖范围，允许显示器位于负坐标。</summary>
    protected void SetPhysicalBounds(int x, int y, int width, int height)
    {
        if (!SetWindowPos(handle, new nint(-1), x, y, width, height, 0x0010 | 0x0040))
            throw new Win32Exception(Marshal.GetLastWin32Error());
    }

    /// <summary>只移动已缓存的小窗口，不修改尺寸、焦点或层叠顺序。</summary>
    protected void MovePhysical(int x, int y)
    {
        if (previousPosition == (x, y)) return;
        if (!SetWindowPos(handle, nint.Zero, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        previousPosition = (x, y);
    }

    /// <summary>拒绝鼠标激活与命中，保持底层应用交互。</summary>
    private static nint WindowMessage(nint window, int message, nint wparam, nint lparam, ref bool handled)
    {
        if (message == 0x0084)
        {
            handled = true;
            return new nint(-1);
        }
        if (message == 0x0021)
        {
            handled = true;
            return new nint(3);
        }
        return nint.Zero;
    }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern nint GetWindowLongPtr(nint window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern nint SetWindowLongPtr(nint window, int index, nint value);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool SetWindowPos(nint window, nint after, int x, int y, int width, int height, uint flags);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(nint window, int attribute, ref int value, int size);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool SetWindowDisplayAffinity(nint window, uint affinity);
}
