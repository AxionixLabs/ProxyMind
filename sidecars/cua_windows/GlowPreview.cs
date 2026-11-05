using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;

namespace DesktopSidecar;

/// <summary>在独立 STA 消息循环中展示有时间上限的原生蓝光预览。</summary>
internal static class GlowPreview
{
    private static readonly JsonSerializerOptions PreviewJsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    /// <summary>隔离 WPF 消息循环与现有观察进程的 MTA 执行路径。</summary>
    internal static int Run(int seconds)
    {
        int result = 0;
        var thread = new Thread(() => result = RunOnUiThread(seconds));
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        return result;
    }

    /// <summary>显示各显示器的边缘蓝光，并在超时或 Esc 后淡出回收。</summary>
    private static int RunOnUiThread(int seconds)
    {
        NativeWindows.EnablePhysicalCoordinates();
        nint previousForeground = GetForegroundWindow();
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var windows = new List<PreviewOverlayWindow>();
        var pointers = new List<PointerPreviewWindow>();
        var screens = new List<ScreenBounds>();
        EnumDisplayMonitors(nint.Zero, nint.Zero,
            (nint monitor, nint context, ref ScreenBounds bounds, nint parameter) =>
            {
                screens.Add(bounds);
                return true;
            }, nint.Zero);
        if (screens.Count == 0)
        {
            Console.Error.WriteLine("No interactive display is available.");
            return 1;
        }
        try
        {
            foreach (ScreenBounds screen in screens)
            {
                var window = new GlowWindow(screen);
                windows.Add(window);
            }
            foreach (ScreenBounds screen in screens)
            {
                var pointer = new PointerPreviewWindow(screen);
                pointers.Add(pointer);
                windows.Add(pointer);
            }
            foreach (PreviewOverlayWindow window in windows)
            {
                window.Show();
                window.BeginAnimation(UIElement.OpacityProperty,
                    new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(450)));
            }
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                @event = "glow_started", monitors = screens.Count, durationSeconds = seconds,
                pointerWindows = pointers.Count,
                foregroundUnchanged = GetForegroundWindow() == previousForeground,
                visibleWindows = windows.FindAll(window => window.IsVisible).Count,
                clickThrough = windows.TrueForAll(window => window.HasPassThroughStyles),
            }, PreviewJsonOptions));
            var elapsed = Stopwatch.StartNew();
            var frameTiming = new PreviewFrameTiming();
            TimeSpan? fadeStarted = null;
            TimeSpan lastRender = TimeSpan.MinValue;
            int previousPulse = -1;
            EventHandler renderPointer = (sender, args) =>
            {
                if (fadeStarted is not null || args is not RenderingEventArgs rendering || rendering.RenderingTime == lastRender) return;
                lastRender = rendering.RenderingTime;
                frameTiming.Record(elapsed.Elapsed.TotalMilliseconds);
                double time = elapsed.Elapsed.TotalSeconds;
                int pulse = (int)Math.Floor(time / 2.6);
                foreach (PointerPreviewWindow window in pointers)
                    window.AnimatePointer(time, pulse != previousPulse && pulse > 0);
                previousPulse = pulse;
            };
            CompositionTarget.Rendering += renderPointer;
            var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(50) };
            string reason = "timeout";
            timer.Tick += (sender, args) =>
            {
                bool escape = (GetAsyncKeyState(0x1B) & 0x8000) != 0;
                if (fadeStarted is null && (escape || elapsed.Elapsed.TotalSeconds >= seconds))
                {
                    reason = escape ? "escape" : "timeout";
                    fadeStarted = elapsed.Elapsed;
                    foreach (PreviewOverlayWindow window in windows)
                        window.BeginAnimation(UIElement.OpacityProperty,
                            new DoubleAnimation(0, TimeSpan.FromMilliseconds(400)));
                }
                if (fadeStarted is TimeSpan start && elapsed.Elapsed - start >= TimeSpan.FromMilliseconds(450))
                {
                    app.Shutdown(0);
                }
            };
            timer.Start();
            try
            {
                int result = app.Run();
                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    @event = "glow_stopped", reason,
                    visibleWindows = windows.FindAll(window => window.IsVisible).Count,
                    frameTiming = frameTiming.Summarize(),
                }, PreviewJsonOptions));
                return result;
            }
            finally
            {
                timer.Stop();
                CompositionTarget.Rendering -= renderPointer;
            }
        }
        finally
        {
            foreach (PreviewOverlayWindow window in windows)
                if (window.IsVisible) window.Close();
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct ScreenBounds
    {
        internal int Left;
        internal int Top;
        internal int Right;
        internal int Bottom;
    }

    private delegate bool MonitorCallback(nint monitor, nint context, ref ScreenBounds bounds, nint parameter);
    [DllImport("user32.dll")] private static extern bool EnumDisplayMonitors(nint context, nint clip, MonitorCallback callback, nint parameter);
    [DllImport("user32.dll")] private static extern nint GetForegroundWindow();
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
}

/// <summary>记录预览调度的帧间隔，排除启动首秒与退出淡出，不代表实际呈现帧率。</summary>
internal sealed class PreviewFrameTiming
{
    private readonly List<double> intervals = new();
    private double previous;

    /// <summary>收集去重后的渲染回调间隔。</summary>
    internal void Record(double milliseconds)
    {
        if (previous >= 1000) intervals.Add(milliseconds - previous);
        previous = milliseconds;
    }

    /// <summary>输出稳定段的调度诊断，空样本保持明确的零值。</summary>
    internal FrameTimingSummary Summarize()
    {
        if (intervals.Count == 0) return new(0, 0, 0, 0, 0, 0);
        intervals.Sort();
        double sum = 0;
        int delayed = 0;
        foreach (double interval in intervals)
        {
            sum += interval;
            if (interval > 34) delayed++;
        }
        return new(intervals.Count, Math.Round(sum / intervals.Count, 2),
            Math.Round(intervals[(int)Math.Ceiling(intervals.Count * 0.95) - 1], 2),
            Math.Round(intervals[(int)Math.Ceiling(intervals.Count * 0.99) - 1], 2),
            Math.Round(intervals[^1], 2), delayed);
    }
}

/// <summary>保存预览调度间隔的统计结果，供退出诊断使用。</summary>
internal sealed record FrameTimingSummary(int Samples, double MeanMs, double P95Ms, double P99Ms, double MaximumMs, int Over34Ms);

/// <summary>承载蓝光绘制，不取得焦点，不参与鼠标命中或任务栏。</summary>
internal sealed class GlowWindow : PreviewOverlayWindow
{
    private GlowPreview.ScreenBounds screen;

    /// <summary>创建透明窗口，随后使用物理显示器边界定位。</summary>
    internal GlowWindow(GlowPreview.ScreenBounds screen) : base("Desktop visual preview")
    {
        this.screen = screen;
        Width = screen.Right - screen.Left;
        Height = screen.Bottom - screen.Top;
        Content = new EdgeGlow();
    }

    /// <summary>在句柄就绪时铺满物理显示器。</summary>
    protected override void OnSourceInitialized(EventArgs args)
    {
        base.OnSourceInitialized(args);
        SetPhysicalBounds(screen.Left, screen.Top, screen.Right - screen.Left, screen.Bottom - screen.Top);
    }

    /// <summary>同步窗口边界，支持目标窗口移动、调整大小和显示器切换后的短暂视觉收敛。</summary>
    internal void MoveToBounds(GlowPreview.ScreenBounds next)
    {
        screen = next;
        Width = next.Right - next.Left;
        Height = next.Bottom - next.Top;
        SetPhysicalBounds(next.Left, next.Top, next.Right - next.Left, next.Bottom - next.Top);
    }
}

/// <summary>在独立小窗口中展示指针，移动时不使整屏蓝光重新绘制。</summary>
internal sealed class PointerPreviewWindow : PreviewOverlayWindow
{
    private readonly GlowPreview.ScreenBounds screen;
    private readonly AgentPointer pointer = new();

    /// <summary>为当前显示器创建局部指针画布。</summary>
    internal PointerPreviewWindow(GlowPreview.ScreenBounds screen) : base("Desktop pointer preview")
    {
        this.screen = screen;
        Width = AgentPointer.Extent;
        Height = AgentPointer.Extent;
        Content = pointer;
    }

    /// <summary>在淡入前把指针定位到演示路径起点。</summary>
    protected override void OnSourceInitialized(EventArgs args)
    {
        base.OnSourceInitialized(args);
        AnimatePointer(0, false);
    }

    /// <summary>仅演示代理指针的运动与反馈，不生成系统鼠标输入。</summary>
    internal void AnimatePointer(double seconds, bool pulse)
    {
        ReadOnlySpan<Point> stops =
        [
            new Point(0.28, 0.35), new Point(0.66, 0.40),
            new Point(0.57, 0.69), new Point(0.35, 0.59),
        ];
        double segment = seconds / 2.6;
        int index = (int)Math.Floor(segment) % stops.Length;
        double progress = segment - Math.Floor(segment);
        double eased = progress * progress * progress * (progress * (progress * 6 - 15) + 10);
        Point start = stops[index];
        Point end = stops[(index + 1) % stops.Length];
        double x = start.X + (end.X - start.X) * eased;
        double y = start.Y + (end.Y - start.Y) * eased;
        double arc = Math.Sin(eased * Math.PI) * 0.035;
        DpiScale dpi = VisualTreeHelper.GetDpi(this);
        MovePhysical(
            (int)Math.Round(screen.Left + x * (screen.Right - screen.Left) - AgentPointer.Hotspot * dpi.DpiScaleX),
            (int)Math.Round(screen.Top + (y - arc) * (screen.Bottom - screen.Top) - AgentPointer.Hotspot * dpi.DpiScaleY));
        if (pulse) pointer.Pulse();
    }
}

/// <summary>以宽幅透明渐变和四角柔光覆盖屏幕边缘，不绘制边框线。</summary>
internal sealed class EdgeGlow : FrameworkElement
{
    /// <summary>固定大面积柔光的绘制，移动反馈由独立指针窗口承担。</summary>
    internal EdgeGlow()
    {
        IsHitTestVisible = false;
    }

    /// <summary>按当前窗口逻辑尺寸铺满边缘与四角，向屏幕内部连续衰减。</summary>
    protected override void OnRender(DrawingContext context)
    {
        base.OnRender(context);
        double width = ActualWidth;
        double height = ActualHeight;
        if (width < 2 || height < 2) return;
        double depth = Math.Clamp(Math.Min(width, height) * 0.14, 105, 165);
        var bounds = new Rect(0, 0, width, height);
        context.PushClip(new RectangleGeometry(bounds));
        context.DrawRectangle(Gradient(new Point(0, 0), new Point(1, 0)), null, new Rect(0, 0, depth, height));
        context.DrawRectangle(Gradient(new Point(1, 0), new Point(0, 0)), null, new Rect(width - depth, 0, depth, height));
        context.DrawRectangle(Gradient(new Point(0, 0), new Point(0, 1)), null, new Rect(0, 0, width, depth));
        context.DrawRectangle(Gradient(new Point(0, 1), new Point(0, 0)), null, new Rect(0, height - depth, width, depth));
        double radius = depth * 1.75;
        Corner(context, new Point(0, 0), radius, Color.FromRgb(61, 150, 255));
        Corner(context, new Point(width, 0), radius, Color.FromRgb(74, 178, 255));
        Corner(context, new Point(width, height), radius, Color.FromRgb(79, 133, 255));
        Corner(context, new Point(0, height), radius, Color.FromRgb(47, 137, 255));
        context.Pop();
    }

    /// <summary>建立由边缘向内衰减的蓝色透明渐变。</summary>
    private static LinearGradientBrush Gradient(Point start, Point end)
    {
        var brush = new LinearGradientBrush { StartPoint = start, EndPoint = end };
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(185, 53, 137, 255), 0));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(156, 49, 130, 255), 0.08));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(102, 48, 120, 250), 0.24));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(46, 49, 115, 247), 0.46));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(10, 48, 112, 245), 0.76));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(0, 48, 112, 245), 1));
        brush.Freeze();
        return brush;
    }

    /// <summary>补足屏幕四角的光量，避免矩形边缘相接时出现空角。</summary>
    private static void Corner(DrawingContext context, Point center, double radius, Color color)
    {
        var brush = new RadialGradientBrush();
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(145, color.R, color.G, color.B), 0));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(96, color.R, color.G, color.B), 0.24));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(35, color.R, color.G, color.B), 0.52));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(7, color.R, color.G, color.B), 0.78));
        brush.GradientStops.Add(new GradientStop(Color.FromArgb(0, color.R, color.G, color.B), 1));
        brush.Freeze();
        context.DrawEllipse(brush, null, center, radius, radius);
    }
}
