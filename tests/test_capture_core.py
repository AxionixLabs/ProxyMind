import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.mcp_core.core_capture import Capture, CaptureSession
from backend.utilities.runtime.idle import Idle


class FakePhone(object):

    def __init__(self, proxy: str) -> None:
        self.proxy = proxy
        self.cleared = False

    async def http_proxy_get(self) -> str:
        return self.proxy

    async def http_proxy_set(self, endpoint: str) -> None:
        self.proxy = endpoint

    async def http_proxy_clear(self) -> None:
        self.proxy = ":0"
        self.cleared = True


class FakeDevice(object):

    def __init__(self, serial: str, proxy: str) -> None:
        self.serial = serial
        self.phone = FakePhone(proxy)


class FakeProcess(object):

    def __init__(self) -> None:
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


def make_session(root: Path, *, active: bool = False, proxy: str = "127.0.0.1:8888") -> tuple[Capture, CaptureSession]:
    capture = Capture()
    device = FakeDevice("device-1", proxy)
    idle = Idle(ttl_sec=0)
    session = CaptureSession(
        capture_id="cap_test",
        device=device,
        package_name="com.example.app",
        proxy_endpoint=proxy,
        previous_proxy="old.proxy:8080",
        directory=root,
        flow_path=root / "capture.mitm",
        index_path=root / "flows.jsonl",
        har_path=root / "capture.har",
        process=FakeProcess(),
        idle=idle,
        active=active,
    )
    capture.sessions[session.capture_id] = session
    if active:
        capture.active_by_serial[device.serial] = session.capture_id
    return capture, session


def write_flows(path: Path) -> None:
    rows = [
        {
            "started_at": "2026-01-01T00:00:00Z",
            "duration_ms": 25,
            "request": {"method": "GET", "scheme": "https", "host": "api.example.com", "port": 443, "path": "/v1/profile", "url": "https://api.example.com/v1/profile?token=visible", "headers": [["Authorization", "Bearer visible-token"]], "body": {"text": "", "encoding": "utf-8", "size": 0}},
            "response": {"status_code": 200, "headers": [["Set-Cookie", "session=visible"]], "body": {"text": "{\"name\":\"Ada\"}", "encoding": "utf-8", "size": 14}},
            "error": None,
        },
        {
            "started_at": "2026-01-01T00:00:01Z",
            "duration_ms": 480,
            "request": {"method": "POST", "scheme": "https", "host": "api.example.com", "port": 443, "path": "/v1/login"},
            "response": {"status_code": 500},
            "error": None,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_capture_query_assert_and_har_keep_full_content() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        capture, session = make_session(root)
        write_flows(session.index_path)

        query = capture.query("cap_test", host="api.example.com", path="profile", status_code=200)
        assert query.data["count"] == 1
        assert query.data["flows"][0]["request"]["path"] == "/v1/profile"
        assert query.data["flows"][0]["request"]["headers"][0][1] == "Bearer visible-token"

        passed = capture.assert_rule("cap_test", {"status_code": 200, "min_count": 1, "max_duration_ms": 50})
        assert passed.ok is True

        failed = capture.assert_rule("cap_test", {"status_code": 500, "max_duration_ms": 100})
        assert failed.ok is False
        assert failed.data["failures"][0]["field"] == "max_duration_ms"

        exported = capture.export("cap_test", "har")
        document = json.loads(Path(exported.data["path"]).read_text(encoding="utf-8"))
        entry = document["log"]["entries"][0]
        assert entry["request"]["headers"][0]["value"] == "Bearer visible-token"
        assert entry["response"]["headers"][0]["value"] == "session=visible"
        assert entry["request"]["url"] == "https://api.example.com/v1/profile?token=visible"
        assert entry["request"]["queryString"] == [{"name": "token", "value": "visible"}]
        assert entry["response"]["content"]["text"] == '{"name":"Ada"}'


def test_capture_stop_restores_owned_proxy_only() -> None:
    async def run() -> None:
        with TemporaryDirectory() as temporary:
            capture, session = make_session(Path(temporary), active=True)
            result = await capture.stop(session.capture_id)
            assert result.data["proxy_restored"] is True
            assert session.device.phone.proxy == "old.proxy:8080"
            assert session.active is False

            capture, session = make_session(Path(temporary), active=True)
            session.device.phone.proxy = "other.proxy:9000"
            result = await capture.stop(session.capture_id)
            assert result.data["proxy_restored"] is False
            assert session.device.phone.proxy == "other.proxy:9000"

    asyncio.run(run())
