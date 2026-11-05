import base64
import io
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from PIL import JpegImagePlugin

from agent.ports.desktop import DesktopError
from infrastructure.sidecars.desktop.protocol import _image


@pytest.mark.skipif(sys.platform != "win32", reason="Requires the Windows image encoder")
@pytest.mark.parametrize("pattern,size", [("text", (1200, 700)), ("desktop", (3840, 2160)),
                                         ("noise", (1920, 1080)), ("noise", (3840, 2160))])
def test_native_capture_encoding_preserves_geometry_and_budget(repository_root: Path, tmp_path: Path, pattern, size):
    executable = repository_root / "tests/fixtures/cua_encoding/bin/Release/net10.0-windows10.0.19041.0/Encoding.exe"
    if not executable.is_file():
        pytest.skip("Build the native encoding fixture before running this test")
    if pattern in {"noise", "desktop"}:
        source = Image.frombytes("RGB", size, random.Random(42).randbytes(size[0] * size[1] * 3))
        if pattern == "desktop":
            gradient = Image.linear_gradient("L").resize(size).convert("RGB")
            source = Image.blend(gradient, source, 0.08)
    else:
        source = Image.new("RGB", size, (22, 38, 65))
        draw = ImageDraw.Draw(source)
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
        for y in range(20, size[1] - 25, 28):
            draw.text((20, y), "截图清晰度验收 ABC 0123456789 小字与细线", fill="white", font=font)
    landmark = (size[0] * 3 // 4, size[1] // 2)
    ImageDraw.Draw(source).rectangle((landmark[0] - 30, landmark[1] - 30,
                                     landmark[0] + 30, landmark[1] + 30), fill=(0, 255, 0))
    source_path = tmp_path / "input.png"
    source.save(source_path)
    started = time.perf_counter()
    completed = subprocess.run([str(executable), str(source_path)], capture_output=True, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    result = json.loads(completed.stdout)
    image = _image(result)
    assert len(image.content) <= 8 * 1024 * 1024
    assert image.width <= size[0] and image.height <= size[1]
    assert abs(image.width / image.height - size[0] / size[1]) < 0.003
    with Image.open(io.BytesIO(image.content)) as decoded:
        decoded.load()
        assert decoded.size == (image.width, image.height)
        if pattern == "text":
            assert image.mime_type == "image/png"
            assert decoded.convert("RGB").tobytes() == source.tobytes()
        else:
            assert image.mime_type == "image/jpeg"
            assert JpegImagePlugin.get_sampling(decoded) == 0
            if size[0] == 1920 or pattern == "desktop":
                assert decoded.size == size
        projected = (int(landmark[0] * image.width / size[0]), int(landmark[1] * image.height / size[1]))
        pixel = decoded.convert("RGB").getpixel(projected)
        assert pixel[1] > 245 and pixel[0] < 10 and pixel[2] < 10
    print({"pattern": pattern, "input": size, "output": (image.width, image.height),
           "png_bytes": source_path.stat().st_size, "encoded_bytes": len(image.content),
           "elapsed_ms": round((time.perf_counter() - started) * 1000)})


@pytest.mark.parametrize("kind", ["dimensions", "mime", "base64", "truncated"])
def test_capture_adapter_rejects_invalid_image(kind):
    stream = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(stream, format="PNG")
    encoded = stream.getvalue()
    payload = {"mimeType": "image/png", "width": 20, "height": 10, "data": base64.b64encode(encoded).decode()}
    if kind == "dimensions":
        payload["width"] = 21
    elif kind == "mime":
        payload["mimeType"] = "image/jpeg"
    elif kind == "base64":
        payload["data"] = "invalid%"
    else:
        payload["data"] = base64.b64encode(encoded[:24]).decode()
    with pytest.raises(DesktopError):
        _image(payload)
