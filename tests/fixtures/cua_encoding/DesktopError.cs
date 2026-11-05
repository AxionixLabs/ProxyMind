using System;

namespace DesktopSidecar;

internal sealed class DesktopError(string code, string message) : Exception(message)
{
    internal string Code { get; } = code;
}
