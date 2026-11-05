using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows.Forms;
using System.Threading.Tasks;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        using var window = new VerificationWindow
        {
            Text = "Desktop sidecar verification fixture",
            ClientSize = new Size(600, 320), StartPosition = FormStartPosition.CenterScreen,
            BackColor = Color.FromArgb(22, 38, 65),
        };
        var heading = new Label
        {
            Text = "Desktop observation fixture", Name = "heading", ForeColor = Color.White,
            Location = new Point(28, 24), AutoSize = true, Font = new Font("Segoe UI", 18),
        };
        var counter = new Label
        {
            Text = "Count: 0", Name = "counter", ForeColor = Color.White,
            Location = new Point(28, 90), AutoSize = true,
        };
        var button = new Button
        {
            Text = "Increment counter", Name = "increment", AccessibleName = "Increment counter",
            Location = new Point(28, 130), Size = new Size(180, 44),
        };
        int count = 0;
        button.Click += (sender, args) => counter.Text = $"Count: {++count}";
        var input = new TextBox
        {
            Name = "textInput", AccessibleName = "Text input", Text = "Unicode: 中文",
            Location = new Point(28, 205), Width = 400,
        };
        window.Controls.AddRange(new Control[] { heading, counter, button, input });
        int wheel = 0;
        window.MouseWheel += (sender, args) => wheel += args.Delta;
        window.Shown += (sender, args) =>
        {
            Point original = window.Location;
            Console.WriteLine("ready");
            _ = Task.Run(async () =>
            {
                while (await Console.In.ReadLineAsync() is string command)
                {
                    await window.InvokeAsync(() =>
                    {
                        if (command == "state")
                        {
                            Console.WriteLine(JsonSerializer.Serialize(new { count, text = input.Text,
                                selection = input.SelectionLength, wheel, foreground = GetForegroundWindow() == window.Handle,
                                held = (GetAsyncKeyState(1) & 0x8000) != 0 || (GetAsyncKeyState(0x11) & 0x8000) != 0 }));
                            return;
                        }
                        if (command.StartsWith("overlays ", StringComparison.Ordinal))
                        {
                            uint owner = uint.Parse(command[9..]);
                            var overlays = new List<OverlayState>();
                            EnumWindows((handle, parameter) =>
                            {
                                GetWindowThreadProcessId(handle, out uint pid);
                                if (pid == owner && IsWindowVisible(handle))
                                {
                                    GetWindowDisplayAffinity(handle, out uint affinity);
                                    long styles = GetWindowLongPtr(handle, -20).ToInt64();
                                    GetWindowRect(handle, out Rect rect);
                                    overlays.Add(new OverlayState(rect.Left, rect.Top, affinity,
                                        (styles & 0x08000020) == 0x08000020,
                                        rect.Right - rect.Left, rect.Bottom - rect.Top));
                                }
                                return true;
                            }, nint.Zero);
                            using var pixel = new Bitmap(1, 1);
                            using (Graphics graphics = Graphics.FromImage(pixel))
                                graphics.CopyFromScreen(window.PointToScreen(new Point(4, 290)), Point.Empty, new Size(1, 1));
                            Color color = pixel.GetPixel(0, 0);
                            Console.WriteLine(JsonSerializer.Serialize(new
                            {
                                overlays,
                                pixel = new[] { (int)color.R, (int)color.G, (int)color.B },
                            }));
                            return;
                        }
                        if (command.StartsWith("metrics ", StringComparison.Ordinal))
                        {
                            using Process process = Process.GetProcessById(int.Parse(command[8..]));
                            process.Refresh();
                            Console.WriteLine(JsonSerializer.Serialize(new
                            {
                                handles = process.HandleCount, threads = process.Threads.Count,
                                privateBytes = process.PrivateMemorySize64,
                                gdi = GetGuiResources(process.Handle, 0), user = GetGuiResources(process.Handle, 1),
                                cpuMs = process.TotalProcessorTime.TotalMilliseconds,
                            }));
                            return;
                        }
                        switch (command)
                        {
                            case "move": window.Location = new Point(original.X + 80, original.Y + 50); break;
                            case "restore": window.Location = original; window.ClientSize = new Size(600, 320); break;
                            case "resize": window.ClientSize = new Size(680, 360); break;
                            case "hide": window.Hide(); break;
                            case "show": window.Show(); break;
                            case "minimize": window.WindowState = FormWindowState.Minimized; break;
                            case "recreate": window.RecreateWindow(); break;
                            case "activate": window.Activate(); break;
                            case "interrupt":
                                keybd_event(0x1B, 0, 0, 0);
                                keybd_event(0x1B, 0, 2, 0);
                                break;
                            default: throw new InvalidOperationException("Unknown fixture command");
                        }
                        Console.WriteLine("ok");
                    });
                }
                await window.InvokeAsync(window.Close);
            });
        };
        Application.Run(window);
    }

    [DllImport("user32.dll")] private static extern uint GetGuiResources(nint process, uint flags);
    [DllImport("user32.dll")] private static extern nint GetForegroundWindow();
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] private static extern void keybd_event(byte key, byte scan, uint flags, nuint extra);
    [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback, nint parameter);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint window, out uint pid);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(nint window);
    [DllImport("user32.dll")] private static extern bool GetWindowDisplayAffinity(nint window, out uint affinity);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern nint GetWindowLongPtr(nint window, int index);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(nint window, out Rect rect);
    private delegate bool WindowCallback(nint window, nint parameter);
    private sealed record OverlayState(int x, int y, uint affinity, bool transparent, int width, int height);
    [StructLayout(LayoutKind.Sequential)] private struct Rect { internal int Left, Top, Right, Bottom; }
}

/// <summary>提供同进程销毁重建场景，仅控制专用验收窗口。</summary>
internal sealed class VerificationWindow : Form
{
    protected override bool ShowWithoutActivation => true;

    /// <summary>在保持进程与托管窗口实例的情况下重建原生句柄。</summary>
    internal void RecreateWindow() => RecreateHandle();
}
