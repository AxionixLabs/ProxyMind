using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace DesktopSidecar;

/// <summary>分块读取有界 JSONL 请求，保留同批次后续帧；输入流由调用方关闭。</summary>
internal sealed class RequestFrameReader(Stream input)
{
    private const int RequestLimit = 64 * 1024;
    private readonly byte[] buffer = new byte[8192];
    private int offset;
    private int available;

    /// <summary>返回一帧正文，大小限制包含换行；拒绝过长帧与半帧 EOF。</summary>
    internal async Task<byte[]?> Read(CancellationToken cancellation)
    {
        using var frame = new MemoryStream();
        while (true)
        {
            if (offset == available)
            {
                available = await input.ReadAsync(buffer, cancellation);
                offset = 0;
                if (available == 0)
                {
                    if (frame.Length != 0)
                        throw new DesktopError("truncated_frame", "EOF in request frame.");
                    return null;
                }
            }
            int newline = Array.IndexOf(buffer, (byte)'\n', offset, available - offset);
            int length = (newline >= 0 ? newline : available) - offset;
            if (frame.Length + length >= RequestLimit)
                throw new DesktopError("request_limit", "Request exceeds size limit.");
            frame.Write(buffer, offset, length);
            offset += length;
            if (newline >= 0)
            {
                offset++;
                return frame.ToArray();
            }
        }
    }
}
