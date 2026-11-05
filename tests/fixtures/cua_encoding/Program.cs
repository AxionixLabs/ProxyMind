using System;
using System.Threading;
using System.Threading.Tasks;
using Windows.Graphics.Imaging;
using Windows.Storage;

internal static class Program
{
    private static async Task Main(string[] args)
    {
        StorageFile file = await StorageFile.GetFileFromPathAsync(args[0]);
        using var stream = await file.OpenAsync(FileAccessMode.Read);
        BitmapDecoder decoder = await BitmapDecoder.CreateAsync(stream);
        using SoftwareBitmap bitmap = await decoder.GetSoftwareBitmapAsync(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore);
        Console.WriteLine((await DesktopSidecar.CaptureEncoder.Encode(bitmap, CancellationToken.None)).ToJsonString());
    }
}
