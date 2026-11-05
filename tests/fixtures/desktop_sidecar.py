import json
import sys
import time


def main() -> None:
    """按指定故障模式运行原生协议测试替身。"""
    mode = sys.argv[1]
    for line in sys.stdin.buffer:
        request = json.loads(line)
        if request["action"] == "hello":
            result = {
                "protocol": 1, "platform": "macos" if mode == "macos" else "windows",
                "actions": ["list_windows", "observe", "validate_observation"],
                "coordinateSpace": "logical_screen_points" if mode == "macos" else "physical_screen_pixels",
                "captureSupported": True, "inputSupported": False,
            }
        else:
            if mode == "timeout":
                time.sleep(10)
            if mode == "eof":
                return
            if mode == "invalid_json":
                print("invalid JSON", flush=True)
                continue
            if mode == "oversized":
                print("x" * (33 * 1024 * 1024), flush=True)
                continue
            if mode == "stderr":
                sys.stderr.write("diagnostic" * 10000)
                sys.stderr.flush()
            result = {} if request["action"] == "validate_observation" else {"windows": [], "truncated": False}
            if mode == "wrong_coordinates" and request["action"] == "observe":
                result = {
                    "window": request["parameters"]["window"], "observation": "observation",
                    "capturedAt": "2026-11-05T00:00:00Z", "displayScale": 1,
                    "coordinateSpace": "logical_screen_points", "image": None,
                    "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
                    "tree": {"nodes": [], "truncated": False, "error": None},
                }
        response = {"v": 1, "id": request["id"], "ok": True, "result": result}
        if mode == "wrong_version":
            response["v"] = 2
        if mode == "wrong_id":
            response["id"] = "wrong"
        print(json.dumps(response), flush=True)
        if mode == "flood":
            sys.stdout.write("x" * (256 * 1024))
            sys.stdout.flush()
    if mode == "slow_exit":
        time.sleep(10)


if __name__ == "__main__":
    main()
