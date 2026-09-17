"""通过真实模型、Windows 命令进程和独立恢复进程验收工具结果交付。"""

import argparse
import asyncio
import base64
import contextlib
import dataclasses
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

import httpx

from agent.adapters.protocol.client import MindChatProtocolClient
from agent.adapters.protocol.tool_results import ToolResultDelivery
from agent.application.tools.coding_schemas import shell_command_input_schema
from agent.composition import open_effect_journal
from agent.protocol import ModelStreamRequest
from agent.ports import ProtocolCommandError
from infrastructure.platform.output_decoder import StreamingProcessOutputDecoder
from protocol.client.compact import (
    build_compact_payload,
    stream_compact_events,
)
from protocol.client.tools import (
    ToolResultRequestError,
    post_tool_result,
)
from protocol.schema.identifiers import (
    new_cid,
    new_sid,
    short_uid,
)
from protocol.schema.stream_events import (
    ContextCompactionEvent,
    ToolCallEvent,
    ToolCallsDoneEvent,
    TurnCompletedEvent,
)
from protocol.transport.auth import build_service_headers
from tests.manual.live_approval_runtime import _environment_snapshot
from tests.manual.live_durable_turn_runtime import (
    _consume_turn,
    _load_config,
)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile")
    result.add_argument("--timeout", type=float, default=240)
    result.add_argument("--child", choices=("persist", "replay"))
    result.set_defaults(interrupt_ack_deadline=3, terminal_deadline=15)
    return result


def delivery(client, journal, post):
    async def no_known_effect(_effect_id):
        return False

    return ToolResultDelivery(
        result_journal=journal, post_result=post,
        get_status=client.get_tool_result_status,
        post_reconciliation=client.post_effect_reconciliation,
        reconcile_known_effect=no_known_effect,
    )


async def child(args):
    record = json.loads(sys.stdin.buffer.read())
    journal = open_effect_journal(record["journal"], cid=record["cid"], sid=record["sid"])
    client = MindChatProtocolClient()
    if args.child == "persist":
        shell = (
            ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command")
            if os.name == "nt" else ("sh", "-c")
        )
        process = await asyncio.create_subprocess_exec(
            *shell, record["arguments"]["command"],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_owned(process, None, timeout=20)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        decoder = StreamingProcessOutputDecoder(encoding="utf-8")
        text = decoder.feed(stdout) + decoder.finish()
        assert "中文\x00😀" in text

        async def reject_before_send(*_args, **_kwargs):
            raise ToolResultRequestError("injected_delivery_failure", "before network", retryable=False)

        result = {
            "ok": True, "tool": "shell_command", "source": "client",
            "args": record["arguments"], "text": text, "attachments": [],
            "data": {"stdout": text, "stderr": "", "output_lines": text.splitlines(), "exit_code": 0},
        }
        try:
            await delivery(client, journal, reject_before_send).deliver(
                record["cid"], record["sid"], record["call_id"], "shell_command", True, result,
            )
        except ToolResultRequestError as error:
            assert error.code == "injected_delivery_failure"
        else:
            raise AssertionError("delivery fault did not occur")
        assert await journal.load_tool_result(record["cid"], record["sid"], record["call_id"]) is not None
        print("PASS actual command executed; result persisted before process exit")
        return

    await _load_config(args)
    posts = []

    async def lose_ack(*values, **kwargs):
        posts.append(1)
        await client.post_tool_result(*values, **kwargs)
        raise ToolResultRequestError("tool_result_transport_error", "injected lost ACK", retryable=True)

    action = await delivery(client, journal, lose_ack).resolve_replayed_call(
        cid=record["cid"], sid=record["sid"], call_id=record["call_id"], tool_name="shell_command",
    )
    assert action == "skip"
    assert len(posts) == 1
    print("PASS fresh process resubmitted saved result and reconciled lost ACK")


async def communicate_owned(process, data, *, timeout):
    try:
        return await asyncio.wait_for(process.communicate(data), timeout=timeout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def run_child(mode, record, profile):
    command = [sys.executable, "-X", "utf8", "-m", "tests.manual.live_tool_delivery_runtime", "--child", mode]
    if profile:
        command.extend(("--profile", profile))
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    output, errors = await communicate_owned(
        process, json.dumps(record).encode("utf-8"), timeout=90,
    )
    assert process.returncode == 0, (output + errors).decode("utf-8", errors="replace")[-3000:]
    print(output.decode("utf-8").strip())


async def verify_invalid_input(config, payload):
    async with httpx.AsyncClient(timeout=15) as client:
        for invalid in ("bad\x00path", "bad\ud800path", {"bad\x00key": 1}):
            candidate = {**payload, "result": {**payload["result"], "data": {"nested": [invalid]}}}
            response = await client.post(
                f"{config.domain.rstrip('/')}/tool-result", headers=build_service_headers(),
                content=json.dumps(candidate),
            )
            assert response.status_code == 422, response.text
            assert response.json()["details"]["retryable"] is False
        print("PASS live HTTP rejects nested NUL, surrogate and invalid key with 422 / false")


@contextlib.asynccontextmanager
async def owned_stream(client, request):
    stream = client.stream(request)
    try:
        yield stream
    finally:
        await stream.aclose()
        if stream.end_reason != "settled":
            with contextlib.suppress(ProtocolCommandError):
                await client.interrupt_turn(cid=request.cid, sid=request.sid, turn_id=request.turn_id)


async def verify_continuation(config, client, cid, sid):
    next_turn = await _consume_turn(client, ModelStreamRequest(
        cid=cid, sid=sid, turn_id=f"turn_{short_uid(20)}", message="Reply only NEXT_OK.",
        pref_config=config.pref_config, tools=(), timeout=config.timeout,
        options={"session_mode": "existing", "permissions": {
            "sandbox_mode": "read-only", "approval_policy": "never",
            "approvals_reviewer": "user", "network_access": "restricted",
        }},
    ))
    assert next_turn.terminal_status == "completed"
    print("PASS next query completes after recovery and automatic compaction")
    manual = [event async for event in stream_compact_events(build_compact_payload({
        "cid": cid, "sid": sid, "llm_conf": config.pref_config,
    }))]
    assert manual[-1].type == "context.compaction.completed", manual[-1]
    print("PASS manual compaction completes through the real provider")


async def verify(args):
    config = await _load_config(args)
    primary = dict(config.pref_config["primary"])
    primary["model_auto_compact_token_limit"] = 20000
    config = dataclasses.replace(config, pref_config={**config.pref_config, "primary": primary})
    cid = new_cid()
    sid = new_sid(cid)
    client = MindChatProtocolClient()
    print(f"SESSION cid={cid} sid={sid}")
    with tempfile.TemporaryDirectory(prefix="tool-delivery-e2e-") as temporary:
        root = Path(temporary)
        counter = root / "executions.txt"
        journal_path = root / "effects.db"
        script = (
            'import os,sys;from pathlib import Path;'
            'p=Path(sys.argv[1]);p.open("a").write("executed\\n");'
            'os.write(1,("中文\\x00😀\\n"*3000).encode("utf-8"))'
        )
        encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
        script = f"import base64;exec(base64.b64decode('{encoded_script}'))"
        if os.name == "nt":
            quoted = ["'" + value.replace("'", "''") + "'" for value in (sys.executable, script, str(counter))]
            command = f"& {quoted[0]} -c {quoted[1]} {quoted[2]}"
        else:
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {shlex.quote(str(counter))}"
        schema = shell_command_input_schema()
        schema["properties"]["command"]["enum"] = [command]
        request = ModelStreamRequest(
            cid=cid, sid=sid, turn_id=f"turn_{short_uid(20)}", pref_config=config.pref_config,
            message=("This is a bounded tool delivery acceptance test. Call shell_command exactly once with this exact command:\n"
                     + command + "\nDo not run any other command. Once the output is received, reply only DELIVERY_OK."),
            tools=({"name": "shell_command", "description": "Run the exact local acceptance command.",
                    "inputSchema": schema,
                    "meta": {"client_builtin": True, "domain": "coding", "class": "shell"}},),
            environment_snapshot=_environment_snapshot(),
            options={"permissions": {"sandbox_mode": "danger-full-access", "approval_policy": "never",
                                     "approvals_reviewer": "user", "network_access": "restricted"},
                     "tool_choice": "auto", "session_mode": "create"},
            metadata={"origin": "live_tool_delivery_runtime"}, timeout=config.timeout,
        )
        calls = []
        terminals = []
        compactions = []
        async with owned_stream(client, request) as stream:
            async for event in stream:
                if isinstance(event, ToolCallEvent):
                    calls.append(event)
                    assert event.name == "shell_command"
                    assert event.arguments["command"].strip() == command, "provider changed the sole permitted command"
                elif isinstance(event, ToolCallsDoneEvent):
                    assert len(calls) == 1
                    call = calls[0]
                    record = {"cid": cid, "sid": sid, "call_id": call.call_id,
                              "arguments": dict(call.arguments), "journal": str(journal_path)}
                    await run_child("persist", record, args.profile)
                    saved = await open_effect_journal(journal_path, cid=cid, sid=sid).load_tool_result(cid, sid, call.call_id)
                    assert "中文␀😀" in saved["result"]["text"]
                    assert "NUL represented" in saved["result"]["text"]
                    await verify_invalid_input(config, saved)
                    await run_child("replay", record, args.profile)
                    ack = await post_tool_result(
                        cid, sid, call.call_id, "shell_command", True, saved["result"],
                        additional_context=saved["additional_context"], request_id=saved["request_id"],
                    )
                    assert ack["data"]["already_received"] is True
                    print("PASS identical resubmission returns the stored receipt")
                elif isinstance(event, ContextCompactionEvent):
                    compactions.append(event)
                    print(f"COMPACT {event.type} reason={event.reason}")
                elif isinstance(event, TurnCompletedEvent):
                    terminals.append(event)
            assert len(terminals) == 1 and terminals[0].status == "completed", terminals
        assert counter.read_text().splitlines() == ["executed"]
        assert any(event.type == "context.compaction.completed" for event in compactions), "automatic compaction did not complete"
        assert len(calls) == 1
        print("PASS one execution across two processes; automatic compaction completed; one terminal")
        await verify_continuation(config, client, cid, sid)


async def main():
    args = parser().parse_args()
    if args.child:
        await child(args)
    else:
        await asyncio.wait_for(verify(args), timeout=args.timeout + 180)


if __name__ == "__main__":
    asyncio.run(main())
