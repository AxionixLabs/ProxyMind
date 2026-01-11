#  ____                            _
# |  _ \ ___  __ _ _   _  ___  ___| |_
# | |_) / _ \/ _` | | | |/ _ \/ __| __|
# |  _ <  __/ (_| | |_| |  __/\__ \ |_
# |_| \_\___|\__, |\__,_|\___||___/\__|
#               |_|
#

import json
import httpx
import base64
import typing
from loguru import logger
from utils import const


async def streaming(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, typing.Any],
    payload: dict[str, typing.Any],
    on_event: typing.Callable,
) -> typing.AsyncGenerator[dict, None]:

    async with client.stream("POST", url, headers=headers, json=payload) as resp:
        resp.raise_for_status()

        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue

            try:
                event = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue

            await on_event(event)

            yield event


async def stream_planner(
    payload: dict[str, typing.Any],
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:

    async def handle_event(event: dict) -> None:
        match event.get("type"):
            case "thinking":
                logger.info(f"🟣 {event['content']}")
            case "plan":
                if steps := event.get("steps"):
                    for step in steps: logger.info(f"🟠 {step['action']}")
                else:
                    logger.warning(f"🔴 {event}")
            case "done":
                logger.info(f"🟢 Plan done ...")
            case "error":
                logger.warning(f"🔴 Error {event.get('message')}")

    url = f"https://api.appserverx.com/planner"
    headers = {
        "Accept": "text/event-stream", "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async for line in streaming(client, url, headers, payload, handle_event):
            yield line


async def stream_self_heal(
    # page_id: str,
    # platform: str,
    # by: typing.Literal["text", "id"],
    # value: str,
    # page_dump: str,
    # screenshot: str,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:

    async def handle_event(event: dict) -> None:
        match event.get("type"):
            case "thinking":
                logger.info(f"🟣 {event['content']}")
            case "heal":
                logger.info(f"🟠 {event.get('message')}")
            case "done":
                logger.info(f"🟢 Heal done ...")
            case "error":
                logger.warning(f"🔴 Error {event.get('message')}")

    # workflow: ==== DEBUG ====
    page_id    = "com.demo.shopping.payment.PaymentActivity"
    platform   = "android"
    page_dump  = """<?xml version="1.0" encoding="UTF-8"?>
        <hierarchy rotation="0">
          <node index="0" text="" resource-id="com.demo.shopping:id/root" class="android.widget.FrameLayout" package="com.demo.shopping" bounds="[0,0][1080,1920]">
            <node index="0" text="Demo 商城" resource-id="com.demo.shopping:id/tv_title" class="android.widget.TextView" bounds="[40,80][400,150]" />
            <node index="1" text="请选择支付方式" resource-id="com.demo.shopping:id/tv_pay_title" class="android.widget.TextView" bounds="[40,260][600,330]" />
            <node index="2" text="¥ 1299.00" resource-id="com.demo.shopping:id/tv_amount" class="android.widget.TextView" content-desc="total_price" bounds="[40,340][400,410]" />
            <node index="3" text="微信支付" resource-id="com.demo.shopping:id/rb_wechat" class="android.widget.RadioButton" content-desc="pay_wechat" checked="true" bounds="[40,430][1040,510]" />
            <node index="4" text="支付宝" resource-id="com.demo.shopping:id/rb_alipay" class="android.widget.RadioButton" content-desc="pay_alipay" checked="false" bounds="[40,530][1040,610]" />
            <node index="5" text="继续支付" resource-id="com.demo.shopping:id/btn_continue" class="android.widget.Button" content-desc="Continue Pay" bounds="[40,1120][1040,1200]" />
            <node index="6" text="立即支付" resource-id="com.demo.shopping:id/btn_pay_now" class="android.widget.Button" content-desc="Pay Now" bounds="[40,1240][1040,1320]" />
            <node index="7" text="联系客服" resource-id="com.demo.shopping:id/btn_service" class="android.widget.Button" content-desc="Service" bounds="[40,1360][1040,1440]" />
          </node>
        </hierarchy>
    """
    by         = "id"
    value      = "wechat"
    screenshot = "./frame_00009.png"

    url = "https://api.appserverx.com/self-heal"
    headers = {
        "Accept": "text/event-stream", "Content-Type": "application/json"
    }

    with open(screenshot, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "app_id"      : const.APP_DESC,
        "page_id"     : page_id,
        "platform"    : platform,
        "old_locator" : {"by": by, "value": value},
        "page_dump"   : page_dump,
        "screenshot"  : f"data:image/png;base64,{image_b64}",
        "context"     : kwargs
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async for line in streaming(client, url, headers, payload, handle_event):
            yield line


async def main() -> None:
    from engine.tinker import Active
    Active.active("INFO")
    async for _ in stream_self_heal(): pass


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
    pass
