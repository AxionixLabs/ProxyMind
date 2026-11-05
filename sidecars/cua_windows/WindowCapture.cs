using System;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11;
using Windows.Graphics.Imaging;

namespace DesktopSidecar;

/// <summary>在串行桌面会话内复用一个图形设备，逐次释放捕获帧池与图像。</summary>
internal sealed class WindowCapture : IDisposable
{
    private IDirect3DDevice? device;
    internal static bool IsSupported => GraphicsCaptureSession.IsSupported();

    internal async Task<JsonObject> Capture(nint handle, CancellationToken cancellation)
    {
        if (!IsSupported) throw new DesktopError("capture_unavailable", "Windows Graphics Capture is unavailable.");
        try
        {
            GraphicsCaptureItem item = CreateItem(handle);
            if (item.Size.Width <= 0 || item.Size.Height <= 0 || (long)item.Size.Width * item.Size.Height > 16000000)
                throw new DesktopError("capture_limit", "Window dimensions exceed the capture limit.");
            device ??= CreateDevice();
            using var pool = Direct3D11CaptureFramePool.CreateFreeThreaded(
                device, DirectXPixelFormat.B8G8R8A8UIntNormalized, 1, item.Size);
            using var session = pool.CreateCaptureSession(item);
            session.IsCursorCaptureEnabled = false;
            var firstFrame = new TaskCompletionSource<Direct3D11CaptureFrame>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            timeout.CancelAfter(TimeSpan.FromSeconds(8));
            using var registration = timeout.Token.Register(() => firstFrame.TrySetCanceled(timeout.Token));
            pool.FrameArrived += (sender, _) =>
            {
                Direct3D11CaptureFrame? candidate = sender.TryGetNextFrame();
                if (candidate is not null && !firstFrame.TrySetResult(candidate)) candidate.Dispose();
            };
            session.StartCapture();
            using Direct3D11CaptureFrame frame = await firstFrame.Task;
            if (frame.ContentSize.Width != item.Size.Width || frame.ContentSize.Height != item.Size.Height)
                throw new DesktopError("window_changed", "Capture dimensions changed; observe again.");
            using SoftwareBitmap bitmap = await SoftwareBitmap.CreateCopyFromSurfaceAsync(frame.Surface);
            return await CaptureEncoder.Encode(bitmap, cancellation);
        }
        catch (Exception error) when (error is COMException or UnauthorizedAccessException)
        {
            throw new DesktopError("capture_failed", $"Windows capture failed (HRESULT 0x{error.HResult:X8}).");
        }
        catch (OperationCanceledException) when (!cancellation.IsCancellationRequested)
        {
            throw new DesktopError("capture_timeout", "No capture frame arrived before the deadline.");
        }
    }

    /// <summary>在会话结束时关闭复用设备，不跨会话保存原生资源。</summary>
    public void Dispose()
    {
        device?.Dispose();
        device = null;
    }

    private static GraphicsCaptureItem CreateItem(nint handle)
    {
        const string name = "Windows.Graphics.Capture.GraphicsCaptureItem";
        Marshal.ThrowExceptionForHR(WindowsCreateString(name, name.Length, out nint hstring));
        try
        {
            Guid factoryId = typeof(ICaptureItemInterop).GUID;
            Marshal.ThrowExceptionForHR(RoGetActivationFactory(hstring, ref factoryId, out ICaptureItemInterop factory));
            try
            {
                Guid itemId = new("79C3F95B-31F7-4EC2-A464-632EF5D30760");
                nint pointer = factory.CreateForWindow(handle, ref itemId);
                try { return WinRT.MarshalInterface<GraphicsCaptureItem>.FromAbi(pointer); }
                finally { Marshal.Release(pointer); }
            }
            finally { Marshal.ReleaseComObject(factory); }
        }
        finally { WindowsDeleteString(hstring); }
    }

    private static IDirect3DDevice CreateDevice()
    {
        Marshal.ThrowExceptionForHR(D3D11CreateDevice(nint.Zero, 1, nint.Zero, 0x20, nint.Zero, 0, 7,
            out nint device, out _, out nint context));
        try
        {
            Guid dxgiId = new("54EC77FA-1377-44E6-8C32-88FD5F44C84C");
            Marshal.ThrowExceptionForHR(Marshal.QueryInterface(device, in dxgiId, out nint dxgi));
            try
            {
                Marshal.ThrowExceptionForHR(CreateDirect3D11DeviceFromDXGIDevice(dxgi, out nint projected));
                try { return WinRT.MarshalInterface<IDirect3DDevice>.FromAbi(projected); }
                finally { Marshal.Release(projected); }
            }
            finally { Marshal.Release(dxgi); }
        }
        finally { Marshal.Release(context); Marshal.Release(device); }
    }

    [ComImport, Guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface ICaptureItemInterop
    {
        nint CreateForWindow(nint handle, ref Guid iid);
        // 此声明映射系统 COM 接口，保留成员顺序以明确原生 ABI。
        nint CreateForMonitor(nint handle, ref Guid iid);
    }
    [DllImport("combase.dll", CharSet = CharSet.Unicode)] private static extern int WindowsCreateString(string value, int length, out nint hstring);
    [DllImport("combase.dll")] private static extern int WindowsDeleteString(nint hstring);
    [DllImport("combase.dll")] private static extern int RoGetActivationFactory(nint name, ref Guid iid, out ICaptureItemInterop factory);
    [DllImport("d3d11.dll")] private static extern int D3D11CreateDevice(nint adapter, int driverType, nint software, uint flags,
        nint levels, uint levelCount, uint sdkVersion, out nint device, out int level, out nint context);
    [DllImport("d3d11.dll")] private static extern int CreateDirect3D11DeviceFromDXGIDevice(nint dxgi, out nint device);
}
