using System;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Windows.Foundation;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;

namespace DesktopSidecar;

/// <summary>按图片预算编码捕获帧，输出尺寸作为观察坐标映射的唯一依据。</summary>
internal static class CaptureEncoder
{
    internal const int TargetBytes = 1024 * 1024;
    internal const int MaximumBytes = 8 * 1024 * 1024;

    internal static async Task<JsonObject> Encode(SoftwareBitmap bitmap, CancellationToken cancellation)
    {
        int width = bitmap.PixelWidth, height = bitmap.PixelHeight;
        byte[]? png = await EncodeFrame(bitmap, width, height, null, TargetBytes, cancellation);
        if (png is not null) return Result(png, "image/png", width, height);

        byte[]? highQuality = await EncodeFrame(bitmap, width, height, 0.90f, MaximumBytes, cancellation);
        if (highQuality is not null && highQuality.Length <= TargetBytes)
            return Result(highQuality, "image/jpeg", width, height);
        byte[]? normalQuality = await EncodeFrame(bitmap, width, height, 0.85f, MaximumBytes, cancellation);
        if (normalQuality is not null || highQuality is not null)
        {
            byte[] selected = normalQuality is not null && (highQuality is null || normalQuality.Length < highQuality.Length)
                ? normalQuality : highQuality!;
            return Result(selected, "image/jpeg", width, height);
        }

        for (int attempt = 0; attempt < 4; attempt++)
        {
            width = Math.Max(1, (int)Math.Floor(width * 0.75));
            height = Math.Max(1, (int)Math.Floor(height * 0.75));
            byte[]? scaled = await EncodeFrame(bitmap, width, height, 0.85f, MaximumBytes, cancellation);
            if (scaled is not null) return Result(scaled, "image/jpeg", width, height);
        }
        throw new DesktopError("capture_limit", "Encoded screenshot exceeds the image budget.");
    }

    private static async Task<byte[]?> EncodeFrame(SoftwareBitmap bitmap, int width, int height,
        float? quality, int maximumBytes, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        using var stream = new InMemoryRandomAccessStream();
        var options = new BitmapPropertySet();
        if (quality.HasValue)
        {
            options.Add("ImageQuality", new BitmapTypedValue(quality.Value, PropertyType.Single));
            // 4:4:4 保留彩色文字边缘，避免色度抽样把细线混入背景。
            options.Add("JpegYCrCbSubsampling", new BitmapTypedValue((byte)3, PropertyType.UInt8));
        }
        BitmapEncoder encoder = await BitmapEncoder.CreateAsync(
            quality.HasValue ? BitmapEncoder.JpegEncoderId : BitmapEncoder.PngEncoderId, stream, options);
        encoder.SetSoftwareBitmap(bitmap);
        encoder.BitmapTransform.ScaledWidth = (uint)width;
        encoder.BitmapTransform.ScaledHeight = (uint)height;
        encoder.BitmapTransform.InterpolationMode = BitmapInterpolationMode.Fant;
        await encoder.FlushAsync();
        cancellation.ThrowIfCancellationRequested();
        if (stream.Size > (ulong)maximumBytes) return null;
        using var reader = new DataReader(stream.GetInputStreamAt(0));
        await reader.LoadAsync((uint)stream.Size);
        byte[] bytes = new byte[(int)stream.Size];
        reader.ReadBytes(bytes);
        return bytes;
    }

    private static JsonObject Result(byte[] bytes, string mime, int width, int height) => new()
    {
        ["mimeType"] = mime, ["width"] = width, ["height"] = height,
        ["data"] = Convert.ToBase64String(bytes),
    };
}
