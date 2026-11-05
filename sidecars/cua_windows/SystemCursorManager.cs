using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;

namespace DesktopSidecar;

/// <summary>在一次 CUA 会话内替换系统光标并负责异常退出后的恢复。</summary>
internal static class SystemCursorManager
{
    private const uint SystemParametersSetCursors = 0x0057;
    private const uint SystemParametersSendChange = 0x0002;
    private static readonly uint[] CursorIds =
    [
        32512, 32513, 32514, 32515, 32516,
        32642, 32643, 32644, 32645, 32646,
        32648, 32649, 32650, 32651, 32671, 32672,
    ];

    /// <summary>启动独立 manager，使 sidecar 崩溃时仍能恢复系统光标。</summary>
    internal static Process Start(int parentId)
    {
        string executable = Environment.ProcessPath ??
            throw new DesktopError("cursor_unavailable", "Executable location is unavailable.");
        var start = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        if (Path.GetFileNameWithoutExtension(executable).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            start.ArgumentList.Add(typeof(Program).Assembly.Location);
        start.ArgumentList.Add("--system-cursor-manager");
        start.ArgumentList.Add("--parent-pid");
        start.ArgumentList.Add(parentId.ToString());
        return Process.Start(start) ??
            throw new DesktopError("cursor_unavailable", "Cannot start the system cursor manager.");
    }

    /// <summary>处理 manager 命令，并在管道断开或父进程退出时恢复光标。</summary>
    internal static async Task<int> Run(int parentId)
    {
        using Process parent = Process.GetProcessById(parentId);
        Task parentExit = parent.WaitForExitAsync();
        bool suppressed = false;
        try
        {
            while (true)
            {
                Task<string?> read = Console.In.ReadLineAsync();
                Task completed = await Task.WhenAny(read, parentExit);
                if (completed == parentExit) break;
                string? command = await read;
                if (command is null) break;
                if (command == "suppress")
                {
                    if (!suppressed)
                    {
                        // 标记先于替换，部分 SetSystemCursor 失败时 finally 仍会恢复完整方案。
                        suppressed = true;
                        SuppressSystemCursors();
                    }
                    Console.WriteLine("ok");
                }
                else if (command == "restore")
                {
                    RestoreSystemCursors();
                    suppressed = false;
                    Console.WriteLine("ok");
                }
                else if (command == "shutdown")
                {
                    RestoreSystemCursors();
                    suppressed = false;
                    Console.WriteLine("ok");
                    break;
                }
                else
                {
                    Console.WriteLine("error");
                    return 2;
                }
                Console.Out.Flush();
            }
            return 0;
        }
        catch (Exception error) when (error is Win32Exception or InvalidOperationException or IOException)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
        finally
        {
            if (suppressed)
            {
                try { RestoreSystemCursors(); }
                catch (Win32Exception error) { Console.Error.WriteLine(error.Message); }
            }
        }
    }

    /// <summary>恢复当前 Windows 光标方案，允许重复调用。</summary>
    internal static void RestoreSystemCursors()
    {
        if (!SystemParametersInfo(SystemParametersSetCursors, 0, nint.Zero, SystemParametersSendChange))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot restore the Windows cursor scheme.");
    }

    private static void SuppressSystemCursors()
    {
        foreach (uint cursorId in CursorIds)
        {
            nint cursor = CreateBlankCursor();
            if (cursor == nint.Zero)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot create a blank system cursor.");
            if (!SetSystemCursor(cursor, cursorId))
            {
                _ = DestroyCursor(cursor);
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot replace a Windows system cursor.");
            }
        }
    }

    private static nint CreateBlankCursor()
    {
        byte[] andMask = new byte[128];
        Array.Fill(andMask, byte.MaxValue);
        return CreateCursor(nint.Zero, 0, 0, 32, 32, andMask, new byte[128]);
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint CreateCursor(nint instance, int xHotSpot, int yHotSpot, int width,
        int height, byte[] andMask, byte[] xorMask);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetSystemCursor(nint cursor, uint cursorId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyCursor(nint cursor);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SystemParametersInfo(uint action, uint parameter, nint value, uint flags);
}
